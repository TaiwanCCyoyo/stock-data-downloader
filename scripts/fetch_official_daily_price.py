"""Fetch official TWSE/TPEx bulk daily price data (latest trading day only).

Stores results in `data/official_daily.sqlite`, kept distinct from the
Shioaji-derived `{code}_day.csv` files, so both sources can be compared later
by the data-quality health check rather than one silently overwriting the
other. See `docs/contracts/official-price-source/spec.md`.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import config
from shp_utils.logging_utils import setup_logging
from shp_utils.official_api import MarketFetchCounts, fetch_json_with_retry, parse_int, parse_number, parse_roc_compact_date
from shp_utils.twse_market_day import fetch_twse_market_day, normalize_twse_market_day_payload

logger = logging.getLogger(__name__)

MARKET_TWSE = "TWSE"
MARKET_TPEX = "TPEx"

TWSE_BULK_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_BULK_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"

DEFAULT_DB_PATH = config.DATA_DIR / "official_daily.sqlite"

# WAL (persisted in the database file) lets a writer and readers proceed concurrently,
# so a long-running backfill doesn't block run_daily.py's own fetch or health check.
# busy_timeout is per-connection, not persisted, so it must be set on every connect().
DEFAULT_BUSY_TIMEOUT_MS = 30_000


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(f"PRAGMA busy_timeout = {DEFAULT_BUSY_TIMEOUT_MS}")
    return conn


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch official TWSE/TPEx bulk daily prices.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-sleep", type=float, default=5.0)
    args = parser.parse_args()
    counts = run_fetch(db_path=args.db_path, retries=args.retries, retry_sleep=args.retry_sleep)
    logger.info("official_daily_fetch_counts: %s", counts)


def run_fetch(*, db_path: Path = DEFAULT_DB_PATH, retries: int = 3, retry_sleep: float = 5.0) -> MarketFetchCounts:
    """Fetch both markets' latest-day bulk data and upsert into `db_path`.

    Each market is isolated: a failure fetching or normalizing one market is
    logged and does not prevent the other market's data from being fetched
    and persisted, and never corrupts a previously stored day.
    """
    init_db(db_path)
    counts = MarketFetchCounts()
    trade_dates: dict[str, str] = {}
    with connect(db_path) as conn:
        for market, fetch_fn, normalize_fn in (
            (MARKET_TWSE, fetch_twse_bulk, normalize_twse_bulk_payload),
            (MARKET_TPEX, fetch_tpex_bulk, normalize_tpex_bulk_payload),
        ):
            try:
                payload = fetch_fn(retries=retries, retry_sleep=retry_sleep)
                rows = normalize_fn(payload)
                upsert_rows(conn, rows)
                conn.commit()
                counts[market] = len(rows)
                dates = sorted({str(row["date"]) for row in rows})
                if dates:
                    trade_dates[market] = dates[-1]
                logger.info(
                    "fetched %s official daily bulk: %d rows for %s",
                    market,
                    len(rows),
                    ", ".join(dates) if dates else "no dated rows",
                )
            except Exception:
                conn.rollback()
                logger.exception("failed to fetch %s official daily bulk", market)
                counts[market] = 0
                counts.failed_markets.add(market)
        twse_day = trade_dates.get(MARKET_TWSE)
        tpex_day = trade_dates.get(MARKET_TPEX)
        if tpex_day and (not twse_day or twse_day < tpex_day):
            counts["TWSE_supplement"] = 0
            logger.info("requesting TWSE dated supplement for %s (bulk date %s)", tpex_day, twse_day or "missing")
            try:
                payload = fetch_twse_market_day(tpex_day, retries=retries, retry_sleep=retry_sleep)
                rows = normalize_twse_market_day_payload(tpex_day, payload)
                inserted = insert_rows(conn, rows)
                conn.commit()
                counts["TWSE_supplement"] = inserted
                logger.info("TWSE dated supplement requested=%s returned=%d inserted=%d", tpex_day, len(rows), inserted)
                if rows:
                    # The dated request covers the day the bulk feed lacked, so the
                    # feeds no longer disagree; only an unresolved lag should warn.
                    trade_dates[MARKET_TWSE] = tpex_day
                else:
                    logger.warning("TWSE dated supplement for %s returned no rows; retaining bulk results", tpex_day)
            except Exception:
                conn.rollback()
                counts.failed_markets.add(MARKET_TWSE)
                logger.warning("TWSE dated supplement for %s failed; retaining bulk results", tpex_day, exc_info=True)
    warn_if_markets_disagree(trade_dates)
    return counts


def warn_if_markets_disagree(trade_dates: dict[str, str]) -> None:
    """Warn when the two bulk feeds report different trading days.

    Both endpoints serve "the latest trading day" with no date parameter, so a
    specific day cannot be requested and there is no local trading calendar to
    check the answer against. The other market is the cheap independent
    witness: on a holiday both feeds hold the same stale day and stay silent,
    but when only one lags the dates disagree. That happened on 2026-08-19,
    when TWSE's bulk endpoint was still serving 2026-08-18 more than a day
    later -- the fetch looked healthy (1378 rows, no error) while that trading
    day went silently missing until a per-symbol backfill could recover it.
    """
    if len(set(trade_dates.values())) <= 1:
        return
    logger.warning(
        "official daily feeds disagree on the trading day (%s); the lagging market is missing a day until it is backfilled",
        ", ".join(f"{market}={day}" for market, day in sorted(trade_dates.items())),
    )


def fetch_twse_bulk(*, retries: int = 3, retry_sleep: float = 5.0) -> list[dict[str, Any]]:
    return fetch_json_with_retry(TWSE_BULK_URL, retries=retries, retry_sleep=retry_sleep)


def fetch_tpex_bulk(*, retries: int = 3, retry_sleep: float = 5.0) -> list[dict[str, Any]]:
    return fetch_json_with_retry(TPEX_BULK_URL, retries=retries, retry_sleep=retry_sleep)


def normalize_twse_bulk_payload(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    rows = []
    for raw in payload:
        code = str(raw.get("Code", "")).strip()
        trade_date = parse_roc_compact_date(raw.get("Date", ""))
        if not code or not trade_date:
            continue
        close = parse_number(raw.get("ClosingPrice"))
        rows.append(
            _build_row(
                market=MARKET_TWSE,
                code=code,
                trade_date=trade_date,
                name=clean_text(raw.get("Name")),
                open_price=parse_number(raw.get("OpeningPrice")),
                high=parse_number(raw.get("HighestPrice")),
                low=parse_number(raw.get("LowestPrice")),
                close=close,
                volume=parse_int(raw.get("TradeVolume")),
                value=parse_number(raw.get("TradeValue")),
                transactions=parse_int(raw.get("Transaction")),
                raw=raw,
                fetched_at=fetched_at,
            )
        )
    return rows


def normalize_tpex_bulk_payload(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    rows = []
    for raw in payload:
        code = str(raw.get("SecuritiesCompanyCode", "")).strip()
        trade_date = parse_roc_compact_date(str(raw.get("Date", "")).strip())
        if not code or not trade_date:
            continue
        close = parse_number(raw.get("Close"))
        rows.append(
            _build_row(
                market=MARKET_TPEX,
                code=code,
                trade_date=trade_date,
                name=clean_text(raw.get("CompanyName")),
                open_price=parse_number(raw.get("Open")),
                high=parse_number(raw.get("High")),
                low=parse_number(raw.get("Low")),
                close=close,
                volume=parse_int(raw.get("TradingShares")),
                value=parse_number(raw.get("TransactionAmount")),
                transactions=parse_int(raw.get("TransactionNumber")),
                raw=raw,
                fetched_at=fetched_at,
            )
        )
    return rows


def _build_row(
    *,
    market: str,
    code: str,
    trade_date: str,
    name: str | None,
    open_price: float | None,
    high: float | None,
    low: float | None,
    close: float | None,
    volume: int | None,
    value: float | None,
    transactions: int | None,
    raw: dict[str, Any],
    fetched_at: str,
) -> dict[str, Any]:
    # A no-trade day has no real closing price in either market, even though TWSE
    # reports it as the literal string "0.00" rather than TPEx's "---" sentinel; treat
    # both the same way so a downstream close comparison isn't skewed by a fake 0.0.
    close_missing = close is None or not volume
    return {
        "market": market,
        "code": code,
        "date": trade_date,
        "name": name,
        "open": open_price,
        "high": high,
        "low": low,
        "close": None if close_missing else close,
        "volume": volume,
        "value": value,
        "transactions": transactions,
        "close_missing": close_missing,
        "raw_fields_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
        "fetched_at": fetched_at,
    }


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS official_daily_price (
                market TEXT NOT NULL,
                code TEXT NOT NULL,
                date TEXT NOT NULL,
                name TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                value REAL,
                transactions INTEGER,
                close_missing INTEGER NOT NULL DEFAULT 0,
                raw_fields_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (market, code, date)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_official_daily_price_code_date
            ON official_daily_price (code, date)
            """
        )


def insert_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """Insert absent observations without changing existing prices or provenance."""
    if not rows:
        return 0
    params = [{**row, "close_missing": int(row["close_missing"])} for row in rows]
    cursor = conn.executemany(
        """
        INSERT INTO official_daily_price (
            market, code, date, name, open, high, low, close, volume,
            value, transactions, close_missing, raw_fields_json, fetched_at
        )
        VALUES (
            :market, :code, :date, :name, :open, :high, :low, :close, :volume,
            :value, :transactions, :close_missing, :raw_fields_json, :fetched_at
        )
        ON CONFLICT(market, code, date) DO NOTHING
        """,
        params,
    )
    return cursor.rowcount


def upsert_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    params = [{**row, "close_missing": int(row["close_missing"])} for row in rows]
    conn.executemany(
        """
        INSERT INTO official_daily_price (
            market, code, date, name, open, high, low, close, volume,
            value, transactions, close_missing, raw_fields_json, fetched_at
        )
        VALUES (
            :market, :code, :date, :name, :open, :high, :low, :close, :volume,
            :value, :transactions, :close_missing, :raw_fields_json, :fetched_at
        )
        ON CONFLICT(market, code, date) DO UPDATE SET
            name = excluded.name,
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            volume = excluded.volume,
            value = excluded.value,
            transactions = excluded.transactions,
            close_missing = excluded.close_missing,
            raw_fields_json = excluded.raw_fields_json,
            fetched_at = excluded.fetched_at
        """,
        params,
    )


if __name__ == "__main__":
    setup_logging("fetch_official_daily_price.log")
    main()
