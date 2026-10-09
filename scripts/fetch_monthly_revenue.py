"""Fetch and index official TWSE/TPEx monthly revenue data.

Latest OpenAPI batches provide forward-observed point-in-time evidence. Historical
MOPS CSVs reconstruct values but do not expose each company's exact filing time;
those rows deliberately keep ``available_at`` and ``announcement_at`` null.

Amounts are stored in thousands of New Taiwan dollars, matching the source.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
import math
import sqlite3
import stat
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import config
from shp_utils.logging_utils import setup_logging
from shp_utils.official_api import fetch_bytes_with_retry_url

logger = logging.getLogger(__name__)

MARKET_TWSE = "TWSE"
MARKET_TPEX = "TPEx"

SOURCE_LATEST = "mops_openapi_latest"
SOURCE_HISTORICAL = "mops_historical_csv"

AVAILABILITY_FIRST_OBSERVED = "FIRST_OBSERVED"
AVAILABILITY_HISTORICAL_UNKNOWN = "HISTORICAL_NO_EXACT_TIME"

DEFAULT_DB_PATH = config.DATA_DIR / "monthly_revenue.sqlite"
DEFAULT_RAW_DIR = config.DATA_DIR / "raw" / "monthly_revenue"
MAX_PAYLOAD_ROWS = 10_000
MAX_FIELDS_PER_ROW = 64


@dataclass(frozen=True)
class MarketSource:
    market: str
    latest_url: str
    historical_market: str

    def historical_url(self, revenue_month: str) -> str:
        year, month = revenue_month.split("-", 1)
        roc_year = int(year) - 1911
        return f"https://mopsov.twse.com.tw/nas/t21/{self.historical_market}/t21sc03_{roc_year}_{int(month)}.csv"


MARKET_SOURCES = {
    MARKET_TWSE: MarketSource(
        market=MARKET_TWSE,
        latest_url="https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
        historical_market="sii",
    ),
    MARKET_TPEX: MarketSource(
        market=MARKET_TPEX,
        latest_url="https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O",
        historical_market="otc",
    ),
}

FIELD_REPORT_DATE = "出表日期"
FIELD_REVENUE_MONTH = "資料年月"
FIELD_CODE = "公司代號"
FIELD_NAME = "公司名稱"
FIELD_INDUSTRY = "產業別"
FIELD_CURRENT_REVENUE = "營業收入-當月營收"
FIELD_PREVIOUS_REVENUE = "營業收入-上月營收"
FIELD_PRIOR_YEAR_REVENUE = "營業收入-去年當月營收"
FIELD_MOM_PCT = "營業收入-上月比較增減(%)"
FIELD_YOY_PCT = "營業收入-去年同月增減(%)"
FIELD_CUMULATIVE_REVENUE = "累計營業收入-當月累計營收"
FIELD_PRIOR_YEAR_CUMULATIVE = "累計營業收入-去年累計營收"
FIELD_CUMULATIVE_YOY_PCT = "累計營業收入-前期比較增減(%)"
FIELD_NOTE = "備註"

REQUIRED_FIELDS = {
    FIELD_REVENUE_MONTH,
    FIELD_CODE,
    FIELD_CURRENT_REVENUE,
    FIELD_PREVIOUS_REVENUE,
    FIELD_PRIOR_YEAR_REVENUE,
    FIELD_CUMULATIVE_REVENUE,
    FIELD_PRIOR_YEAR_CUMULATIVE,
}

BUSINESS_COLUMNS = (
    "market",
    "code",
    "revenue_month",
    "name",
    "industry",
    "current_revenue_k_twd",
    "previous_month_revenue_k_twd",
    "prior_year_month_revenue_k_twd",
    "mom_pct",
    "yoy_pct",
    "cumulative_revenue_k_twd",
    "prior_year_cumulative_revenue_k_twd",
    "cumulative_yoy_pct",
    "note",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and index official monthly revenue data.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    latest_parser = subparsers.add_parser("latest", help="Refresh the latest listed and OTC batches.")
    _add_common_fetch_args(latest_parser)

    backfill_parser = subparsers.add_parser("backfill", help="Backfill an inclusive month range.")
    backfill_parser.add_argument("--start-month", required=True, help="First month, YYYY-MM.")
    backfill_parser.add_argument("--end-month", required=True, help="Last month, YYYY-MM.")
    backfill_parser.add_argument(
        "--markets",
        nargs="+",
        choices=sorted(MARKET_SOURCES),
        default=sorted(MARKET_SOURCES),
    )
    backfill_parser.add_argument("--max-requests", type=int, default=24)
    backfill_parser.add_argument("--sleep", type=float, default=1.5)
    backfill_parser.add_argument("--force", action="store_true")
    _add_common_fetch_args(backfill_parser)

    status_parser = subparsers.add_parser("status", help="Show local artifact coverage.")
    status_parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)

    args = parser.parse_args()
    if args.command == "latest":
        counts = refresh_latest(
            db_path=args.db_path,
            raw_dir=args.raw_dir,
            retries=args.retries,
            retry_sleep=args.retry_sleep,
        )
        logger.info("monthly_revenue_latest_counts: %s", counts)
    elif args.command == "backfill":
        counts = backfill(
            start_month=args.start_month,
            end_month=args.end_month,
            markets=args.markets,
            max_requests=args.max_requests,
            sleep=args.sleep,
            force=args.force,
            db_path=args.db_path,
            raw_dir=args.raw_dir,
            retries=args.retries,
            retry_sleep=args.retry_sleep,
        )
        logger.info("monthly_revenue_backfill_counts: %s", counts)
    else:
        logger.info("monthly_revenue_status: %s", status(args.db_path))


def _add_common_fetch_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-sleep", type=float, default=5.0)


def connect_database(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    initialize_database(conn)
    return conn


def initialize_database(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS monthly_revenue_current (
            market TEXT NOT NULL,
            code TEXT NOT NULL,
            revenue_month TEXT NOT NULL,
            name TEXT,
            industry TEXT,
            current_revenue_k_twd INTEGER NOT NULL,
            previous_month_revenue_k_twd INTEGER,
            prior_year_month_revenue_k_twd INTEGER,
            mom_pct REAL,
            yoy_pct REAL,
            cumulative_revenue_k_twd INTEGER,
            prior_year_cumulative_revenue_k_twd INTEGER,
            cumulative_yoy_pct REAL,
            note TEXT,
            source TEXT NOT NULL,
            source_report_date TEXT,
            announcement_at TEXT,
            available_at TEXT,
            availability_kind TEXT NOT NULL,
            first_observed_at TEXT NOT NULL,
            last_fetched_at TEXT NOT NULL,
            revision_no INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            raw_fields_json TEXT NOT NULL,
            PRIMARY KEY (market, code, revenue_month)
        );

        CREATE TABLE IF NOT EXISTS monthly_revenue_revisions (
            market TEXT NOT NULL,
            code TEXT NOT NULL,
            revenue_month TEXT NOT NULL,
            revision_no INTEGER NOT NULL,
            name TEXT,
            industry TEXT,
            current_revenue_k_twd INTEGER NOT NULL,
            previous_month_revenue_k_twd INTEGER,
            prior_year_month_revenue_k_twd INTEGER,
            mom_pct REAL,
            yoy_pct REAL,
            cumulative_revenue_k_twd INTEGER,
            prior_year_cumulative_revenue_k_twd INTEGER,
            cumulative_yoy_pct REAL,
            note TEXT,
            source TEXT NOT NULL,
            source_report_date TEXT,
            announcement_at TEXT,
            available_at TEXT,
            availability_kind TEXT NOT NULL,
            first_observed_at TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            raw_fields_json TEXT NOT NULL,
            PRIMARY KEY (market, code, revenue_month, revision_no)
        );

        CREATE TABLE IF NOT EXISTS monthly_revenue_raw_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            market TEXT NOT NULL,
            revenue_month TEXT,
            url TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            status TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            raw_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            error_message TEXT
        );

        CREATE INDEX IF NOT EXISTS ix_monthly_revenue_current_month
            ON monthly_revenue_current(revenue_month, code);
        CREATE INDEX IF NOT EXISTS ix_monthly_revenue_snapshot_checkpoint
            ON monthly_revenue_raw_snapshots(source, market, revenue_month, status);
        """
    )


def refresh_latest(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    raw_dir: Path = DEFAULT_RAW_DIR,
    retries: int = 3,
    retry_sleep: float = 5.0,
    now: datetime | None = None,
) -> dict[str, int]:
    fetched: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    try:
        for market in sorted(MARKET_SOURCES):
            source_config = MARKET_SOURCES[market]
            raw_bytes, url = fetch_bytes_with_retry_url(
                source_config.latest_url,
                retries=retries,
                retry_sleep=retry_sleep,
            )
            # FIRST_OBSERVED must never predate receipt of the response, including
            # any timeout/retry delay inside the fetch helper.
            observed_at = _utc_now(now)
            item: dict[str, Any] = {
                "market": market,
                "raw_bytes": raw_bytes,
                "raw_extension": "json",
                "source": SOURCE_LATEST,
                "url": url,
                "availability_kind": AVAILABILITY_FIRST_OBSERVED,
                "observed_at": observed_at,
                "expected_revenue_month": None,
            }
            fetched.append(item)
            item["payload"] = parse_json_payload(raw_bytes)

        # Treat the two official markets as one logical refresh. A malformed or
        # unwriteable second market must not leave the first market advanced.
        with connect_database(db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            for item in fetched:
                counts[item["market"]] = ingest_payload(
                    payload=item["payload"],
                    raw_bytes=item["raw_bytes"],
                    raw_extension=item["raw_extension"],
                    market=item["market"],
                    source=item["source"],
                    url=item["url"],
                    availability_kind=item["availability_kind"],
                    observed_at=item["observed_at"],
                    db_path=db_path,
                    raw_dir=raw_dir,
                    connection=conn,
                )
            conn.commit()
    except Exception as exc:
        for item in fetched:
            try:
                record_failed_response(
                    raw_bytes=item["raw_bytes"],
                    raw_extension=item["raw_extension"],
                    market=item["market"],
                    source=item["source"],
                    url=item["url"],
                    observed_at=item["observed_at"],
                    db_path=db_path,
                    raw_dir=raw_dir,
                    expected_revenue_month=item["expected_revenue_month"],
                    error_message=f"latest batch aborted: {exc}",
                )
            except Exception:
                logger.exception("failed to persist monthly-revenue batch failure evidence")
        raise
    return counts


def backfill(
    *,
    start_month: str,
    end_month: str,
    markets: list[str] | tuple[str, ...] | None = None,
    max_requests: int = 24,
    sleep: float = 1.5,
    force: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
    raw_dir: Path = DEFAULT_RAW_DIR,
    retries: int = 3,
    retry_sleep: float = 5.0,
) -> dict[str, int]:
    months = month_range(start_month, end_month)
    selected_markets = list(markets or sorted(MARKET_SOURCES))
    if max_requests <= 0:
        raise ValueError("max_requests must be positive")
    unknown = sorted(set(selected_markets) - set(MARKET_SOURCES))
    if unknown:
        raise ValueError(f"unknown markets: {unknown}")

    counts = {"fetched": 0, "skipped": 0, "rows": 0, "remaining": 0}
    pending: list[tuple[str, str]] = []
    with connect_database(db_path) as conn:
        for revenue_month in months:
            for market in selected_markets:
                if not force and checkpoint_succeeded(conn, market=market, revenue_month=revenue_month):
                    counts["skipped"] += 1
                else:
                    pending.append((market, revenue_month))

    selected = pending[:max_requests]
    counts["remaining"] = len(pending) - len(selected)
    for index, (market, revenue_month) in enumerate(selected):
        source_config = MARKET_SOURCES[market]
        url = source_config.historical_url(revenue_month)
        raw_bytes, resolved_url = fetch_bytes_with_retry_url(
            url,
            retries=retries,
            retry_sleep=retry_sleep,
        )
        observed_at = _utc_now()
        try:
            payload = parse_csv_payload(raw_bytes)
        except Exception as exc:
            record_failed_response(
                raw_bytes=raw_bytes,
                raw_extension="csv",
                market=market,
                source=SOURCE_HISTORICAL,
                url=resolved_url,
                observed_at=observed_at,
                db_path=db_path,
                raw_dir=raw_dir,
                expected_revenue_month=revenue_month,
                error_message=str(exc),
            )
            raise
        rows = ingest_payload(
            payload=payload,
            raw_bytes=raw_bytes,
            raw_extension="csv",
            market=market,
            source=SOURCE_HISTORICAL,
            url=resolved_url,
            availability_kind=AVAILABILITY_HISTORICAL_UNKNOWN,
            observed_at=observed_at,
            db_path=db_path,
            raw_dir=raw_dir,
            expected_revenue_month=revenue_month,
        )
        counts["fetched"] += 1
        counts["rows"] += rows
        if sleep > 0 and index + 1 < len(selected):
            time.sleep(sleep)
    return counts


def ingest_payload(
    *,
    payload: Any,
    raw_bytes: bytes,
    raw_extension: str,
    market: str,
    source: str,
    url: str,
    availability_kind: str,
    observed_at: str,
    db_path: Path,
    raw_dir: Path,
    expected_revenue_month: str | None = None,
    connection: sqlite3.Connection | None = None,
) -> int:
    raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    raw_path = write_raw_snapshot(
        raw_bytes=raw_bytes,
        raw_dir=raw_dir,
        source=source,
        market=market,
        revenue_month=expected_revenue_month,
        fetched_at=observed_at,
        sha256=raw_sha256,
        extension=raw_extension,
    )
    snapshot_id = _snapshot_id(source, market, expected_revenue_month, observed_at, raw_sha256)

    try:
        rows = normalize_payload(
            payload,
            market=market,
            source=source,
            availability_kind=availability_kind,
            observed_at=observed_at,
        )
        periods = {row["revenue_month"] for row in rows}
        validate_unique_rows(rows)
        if expected_revenue_month is not None and periods != {expected_revenue_month}:
            raise ValueError(f"historical payload period mismatch: expected {expected_revenue_month}, got {sorted(periods)}")
    except Exception as exc:
        if connection is not None:
            raise
        with connect_database(db_path) as conn:
            record_snapshot(
                conn,
                snapshot_id=snapshot_id,
                source=source,
                market=market,
                revenue_month=expected_revenue_month,
                url=url,
                fetched_at=observed_at,
                status="FAILED",
                row_count=0,
                raw_path=raw_path,
                sha256=raw_sha256,
                error_message=str(exc),
            )
        raise

    snapshot_month = expected_revenue_month or _single_period(rows)
    if connection is not None:
        apply_prepared_payload(
            connection,
            rows=rows,
            snapshot_id=snapshot_id,
            source=source,
            market=market,
            revenue_month=snapshot_month,
            url=url,
            fetched_at=observed_at,
            raw_path=raw_path,
            sha256=raw_sha256,
        )
        return len(rows)

    with connect_database(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        apply_prepared_payload(
            conn,
            rows=rows,
            snapshot_id=snapshot_id,
            source=source,
            market=market,
            revenue_month=snapshot_month,
            url=url,
            fetched_at=observed_at,
            raw_path=raw_path,
            sha256=raw_sha256,
        )
        conn.commit()
    return len(rows)


def apply_prepared_payload(
    conn: sqlite3.Connection,
    *,
    rows: list[dict[str, Any]],
    snapshot_id: str,
    source: str,
    market: str,
    revenue_month: str | None,
    url: str,
    fetched_at: str,
    raw_path: Path,
    sha256: str,
) -> None:
    for row in rows:
        upsert_normalized_row(conn, row)
    record_snapshot(
        conn,
        snapshot_id=snapshot_id,
        source=source,
        market=market,
        revenue_month=revenue_month,
        url=url,
        fetched_at=fetched_at,
        status="SUCCESS",
        row_count=len(rows),
        raw_path=raw_path,
        sha256=sha256,
        error_message=None,
    )


def record_failed_response(
    *,
    raw_bytes: bytes,
    raw_extension: str,
    market: str,
    source: str,
    url: str,
    observed_at: str,
    db_path: Path,
    raw_dir: Path,
    expected_revenue_month: str | None,
    error_message: str,
) -> None:
    raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    raw_path = write_raw_snapshot(
        raw_bytes=raw_bytes,
        raw_dir=raw_dir,
        source=source,
        market=market,
        revenue_month=expected_revenue_month,
        fetched_at=observed_at,
        sha256=raw_sha256,
        extension=raw_extension,
    )
    with connect_database(db_path) as conn:
        record_snapshot(
            conn,
            snapshot_id=_snapshot_id(source, market, expected_revenue_month, observed_at, raw_sha256),
            source=source,
            market=market,
            revenue_month=expected_revenue_month,
            url=url,
            fetched_at=observed_at,
            status="FAILED",
            row_count=0,
            raw_path=raw_path,
            sha256=raw_sha256,
            error_message=error_message,
        )


def validate_unique_rows(rows: list[dict[str, Any]]) -> None:
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (row["market"], row["code"], row["revenue_month"])
        if key in seen:
            raise ValueError(f"duplicate monthly revenue key in payload: {key}")
        seen.add(key)


def normalize_payload(
    payload: Any,
    *,
    market: str,
    source: str,
    availability_kind: str,
    observed_at: str,
) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or not payload:
        raise ValueError("monthly revenue payload must be a non-empty row list")
    if len(payload) > MAX_PAYLOAD_ROWS:
        raise ValueError(f"monthly revenue payload exceeds {MAX_PAYLOAD_ROWS} row limit")
    rows: list[dict[str, Any]] = []
    for index, original in enumerate(payload):
        if not isinstance(original, dict):
            raise ValueError(f"row {index} is not an object")
        if len(original) > MAX_FIELDS_PER_ROW:
            raise ValueError(f"row {index} exceeds {MAX_FIELDS_PER_ROW} field limit")
        raw = {_clean_header(key): value for key, value in original.items()}
        missing = sorted(field for field in REQUIRED_FIELDS if field not in raw)
        if missing:
            raise ValueError(f"row {index} missing required fields: {missing}")
        code = str(raw[FIELD_CODE]).strip()
        if not code:
            raise ValueError(f"row {index} has an empty company code")
        revenue_month = parse_source_month(raw[FIELD_REVENUE_MONTH])
        current_revenue = parse_integer(raw[FIELD_CURRENT_REVENUE], required=True)
        row = {
            "market": market,
            "code": code,
            "revenue_month": revenue_month,
            "name": clean_optional_text(raw.get(FIELD_NAME)),
            "industry": clean_optional_text(raw.get(FIELD_INDUSTRY)),
            "current_revenue_k_twd": current_revenue,
            "previous_month_revenue_k_twd": parse_integer(raw.get(FIELD_PREVIOUS_REVENUE)),
            "prior_year_month_revenue_k_twd": parse_integer(raw.get(FIELD_PRIOR_YEAR_REVENUE)),
            "mom_pct": parse_percentage(raw.get(FIELD_MOM_PCT)),
            "yoy_pct": parse_percentage(raw.get(FIELD_YOY_PCT)),
            "cumulative_revenue_k_twd": parse_integer(raw.get(FIELD_CUMULATIVE_REVENUE)),
            "prior_year_cumulative_revenue_k_twd": parse_integer(raw.get(FIELD_PRIOR_YEAR_CUMULATIVE)),
            "cumulative_yoy_pct": parse_percentage(raw.get(FIELD_CUMULATIVE_YOY_PCT)),
            "note": clean_optional_text(raw.get(FIELD_NOTE)),
            "source": source,
            "source_report_date": parse_source_date(raw.get(FIELD_REPORT_DATE)),
            "announcement_at": None,
            "available_at": observed_at if availability_kind == AVAILABILITY_FIRST_OBSERVED else None,
            "availability_kind": availability_kind,
            "first_observed_at": observed_at,
            "last_fetched_at": observed_at,
            "raw_fields_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
        }
        row["content_hash"] = business_content_hash(row)
        rows.append(row)
    return rows


def upsert_normalized_row(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    current = conn.execute(
        """
        SELECT revision_no, content_hash, availability_kind, available_at
        FROM monthly_revenue_current
        WHERE market = ? AND code = ? AND revenue_month = ?
        """,
        (row["market"], row["code"], row["revenue_month"]),
    ).fetchone()
    if current is not None and current["content_hash"] == row["content_hash"]:
        upgrades_availability = current["available_at"] is None and row["availability_kind"] == AVAILABILITY_FIRST_OBSERVED
        conn.execute(
            """
            UPDATE monthly_revenue_current
            SET last_fetched_at = ?,
                source_report_date = ?,
                raw_fields_json = ?,
                source = CASE WHEN ? THEN ? ELSE source END,
                available_at = CASE WHEN ? THEN ? ELSE available_at END,
                availability_kind = CASE WHEN ? THEN ? ELSE availability_kind END
            WHERE market = ? AND code = ? AND revenue_month = ?
            """,
            (
                row["last_fetched_at"],
                row["source_report_date"],
                row["raw_fields_json"],
                upgrades_availability,
                row["source"],
                upgrades_availability,
                row["available_at"],
                upgrades_availability,
                row["availability_kind"],
                row["market"],
                row["code"],
                row["revenue_month"],
            ),
        )
        return

    revision_no = 1 if current is None else int(current["revision_no"]) + 1
    revision = {**row, "revision_no": revision_no, "observed_at": row["last_fetched_at"]}
    conn.execute(
        """
        INSERT INTO monthly_revenue_revisions (
            market, code, revenue_month, revision_no, name, industry,
            current_revenue_k_twd, previous_month_revenue_k_twd,
            prior_year_month_revenue_k_twd, mom_pct, yoy_pct,
            cumulative_revenue_k_twd, prior_year_cumulative_revenue_k_twd,
            cumulative_yoy_pct, note, source, source_report_date,
            announcement_at, available_at, availability_kind, first_observed_at,
            observed_at, content_hash, raw_fields_json
        ) VALUES (
            :market, :code, :revenue_month, :revision_no, :name, :industry,
            :current_revenue_k_twd, :previous_month_revenue_k_twd,
            :prior_year_month_revenue_k_twd, :mom_pct, :yoy_pct,
            :cumulative_revenue_k_twd, :prior_year_cumulative_revenue_k_twd,
            :cumulative_yoy_pct, :note, :source, :source_report_date,
            :announcement_at, :available_at, :availability_kind, :first_observed_at,
            :observed_at, :content_hash, :raw_fields_json
        )
        """,
        revision,
    )
    conn.execute(
        """
        INSERT INTO monthly_revenue_current (
            market, code, revenue_month, name, industry, current_revenue_k_twd,
            previous_month_revenue_k_twd, prior_year_month_revenue_k_twd,
            mom_pct, yoy_pct, cumulative_revenue_k_twd,
            prior_year_cumulative_revenue_k_twd, cumulative_yoy_pct, note,
            source, source_report_date, announcement_at, available_at,
            availability_kind, first_observed_at, last_fetched_at, revision_no,
            content_hash, raw_fields_json
        ) VALUES (
            :market, :code, :revenue_month, :name, :industry,
            :current_revenue_k_twd, :previous_month_revenue_k_twd,
            :prior_year_month_revenue_k_twd, :mom_pct, :yoy_pct,
            :cumulative_revenue_k_twd, :prior_year_cumulative_revenue_k_twd,
            :cumulative_yoy_pct, :note, :source, :source_report_date,
            :announcement_at, :available_at, :availability_kind,
            :first_observed_at, :last_fetched_at, :revision_no, :content_hash,
            :raw_fields_json
        )
        ON CONFLICT(market, code, revenue_month) DO UPDATE SET
            name = excluded.name,
            industry = excluded.industry,
            current_revenue_k_twd = excluded.current_revenue_k_twd,
            previous_month_revenue_k_twd = excluded.previous_month_revenue_k_twd,
            prior_year_month_revenue_k_twd = excluded.prior_year_month_revenue_k_twd,
            mom_pct = excluded.mom_pct,
            yoy_pct = excluded.yoy_pct,
            cumulative_revenue_k_twd = excluded.cumulative_revenue_k_twd,
            prior_year_cumulative_revenue_k_twd = excluded.prior_year_cumulative_revenue_k_twd,
            cumulative_yoy_pct = excluded.cumulative_yoy_pct,
            note = excluded.note,
            source = excluded.source,
            source_report_date = excluded.source_report_date,
            announcement_at = excluded.announcement_at,
            available_at = excluded.available_at,
            availability_kind = excluded.availability_kind,
            first_observed_at = excluded.first_observed_at,
            last_fetched_at = excluded.last_fetched_at,
            revision_no = excluded.revision_no,
            content_hash = excluded.content_hash,
            raw_fields_json = excluded.raw_fields_json
        """,
        revision,
    )


def write_raw_snapshot(
    *,
    raw_bytes: bytes,
    raw_dir: Path,
    source: str,
    market: str,
    revenue_month: str | None,
    fetched_at: str,
    sha256: str,
    extension: str,
) -> Path:
    month_dir = revenue_month or "latest"
    destination_dir = raw_dir / source / market / month_dir
    ensure_safe_raw_directory(raw_dir, destination_dir)
    timestamp = fetched_at.replace("+00:00", "Z").replace(":", "").replace("-", "")
    destination = destination_dir / f"{timestamp}_{sha256[:12]}.{extension}"
    if destination.exists():
        if hashlib.sha256(destination.read_bytes()).hexdigest() != sha256:
            raise RuntimeError(f"raw snapshot hash mismatch at existing path: {destination}")
        return destination
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(raw_bytes)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def ensure_safe_raw_directory(raw_root: Path, destination_dir: Path) -> None:
    raw_root.mkdir(parents=True, exist_ok=True)
    if _is_link_or_reparse(raw_root):
        raise RuntimeError(f"monthly revenue raw root must not be a link or reparse point: {raw_root}")
    destination_dir.mkdir(parents=True, exist_ok=True)

    current = raw_root
    for part in destination_dir.relative_to(raw_root).parts:
        current /= part
        if _is_link_or_reparse(current):
            raise RuntimeError(f"monthly revenue raw path must not contain links or reparse points: {current}")

    resolved_root = raw_root.resolve(strict=True)
    resolved_destination = destination_dir.resolve(strict=True)
    if resolved_destination != resolved_root and resolved_root not in resolved_destination.parents:
        raise RuntimeError(f"monthly revenue raw path escapes configured root: {destination_dir}")


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def record_snapshot(
    conn: sqlite3.Connection,
    *,
    snapshot_id: str,
    source: str,
    market: str,
    revenue_month: str | None,
    url: str,
    fetched_at: str,
    status: str,
    row_count: int,
    raw_path: Path,
    sha256: str,
    error_message: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO monthly_revenue_raw_snapshots (
            snapshot_id, source, market, revenue_month, url, fetched_at,
            status, row_count, raw_path, sha256, error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_id) DO NOTHING
        """,
        (
            snapshot_id,
            source,
            market,
            revenue_month,
            url,
            fetched_at,
            status,
            row_count,
            str(raw_path),
            sha256,
            error_message,
        ),
    )


def checkpoint_succeeded(conn: sqlite3.Connection, *, market: str, revenue_month: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM monthly_revenue_raw_snapshots
        WHERE source = ? AND market = ? AND revenue_month = ? AND status = 'SUCCESS'
        LIMIT 1
        """,
        (SOURCE_HISTORICAL, market, revenue_month),
    ).fetchone()
    return row is not None


def status(db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    if not db_path.exists():
        return {
            "rows": 0,
            "symbols": 0,
            "earliest_month": None,
            "latest_month": None,
            "revisions": 0,
            "raw_snapshots": 0,
            "availability": {},
        }
    with connect_database(db_path) as conn:
        overview = conn.execute(
            """
            SELECT COUNT(*) AS rows, COUNT(DISTINCT code) AS symbols,
                   MIN(revenue_month) AS earliest_month, MAX(revenue_month) AS latest_month
            FROM monthly_revenue_current
            """
        ).fetchone()
        revisions = conn.execute("SELECT COUNT(*) FROM monthly_revenue_revisions").fetchone()[0]
        snapshots = conn.execute("SELECT COUNT(*) FROM monthly_revenue_raw_snapshots").fetchone()[0]
        availability = {
            row["availability_kind"]: row["rows"]
            for row in conn.execute(
                """
                SELECT availability_kind, COUNT(*) AS rows
                FROM monthly_revenue_current
                GROUP BY availability_kind
                ORDER BY availability_kind
                """
            )
        }
    return {
        "rows": overview["rows"],
        "symbols": overview["symbols"],
        "earliest_month": overview["earliest_month"],
        "latest_month": overview["latest_month"],
        "revisions": revisions,
        "raw_snapshots": snapshots,
        "availability": availability,
    }


def parse_csv_payload(raw_bytes: bytes) -> list[dict[str, str]]:
    text: str | None = None
    errors: list[str] = []
    for encoding in ("utf-8-sig", "cp950", "big5"):
        try:
            text = raw_bytes.decode(encoding)
            break
        except UnicodeDecodeError as exc:
            errors.append(f"{encoding}: {exc}")
    if text is None:
        raise ValueError(f"monthly revenue CSV has an unsupported encoding: {'; '.join(errors)}")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("monthly revenue CSV has no header")
    return [dict(row) for row in reader if any(str(value or "").strip() for value in row.values())]


def parse_json_payload(raw_bytes: bytes) -> Any:
    try:
        return json.loads(raw_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("monthly revenue latest payload is not valid UTF-8 JSON") from exc


def parse_source_month(value: Any) -> str:
    """Normalise a source month to ``YYYY-MM``.

    Two shapes reach this. The latest OpenAPI feed sends a zero-padded ``11507``
    or ``2026-07``. The historical MOPS CSV sends ``108/1`` -- its month is *not*
    zero-padded.

    The previous rule stripped the separator and then branched on the remaining
    length. That reads the historical CSV correctly only by accident: ``108/10``
    collapses to five digits and parses, while ``108/1`` collapses to four and
    raises. Measured over the 120 market-months retrieved on 2026-09-12, calendar
    months 01-09 raised and 10-12 parsed.

    A separator says exactly where the year ends, so split on it instead of
    inferring from length; the separator-free forms keep their original
    length-based reading.

    This describes parser behaviour only. Neither the bug nor the current
    contents of the database establishes whether a historical backfill was ever
    run -- that question is not answered here.
    """
    text = str(value).strip()
    for separator in ("/", "-"):
        head, found, tail = text.partition(separator)
        if not found:
            continue
        if not (head.isdigit() and tail.isdigit() and len(head) in {3, 4}):
            raise ValueError(f"invalid revenue month: {value!r}")
        year = int(head) + (1911 if len(head) == 3 else 0)
        month = int(tail)
        break
    else:
        if not text.isdigit() or len(text) not in {5, 6}:
            raise ValueError(f"invalid revenue month: {value!r}")
        if len(text) == 5:
            year = int(text[:3]) + 1911
            month = int(text[3:])
        else:
            year = int(text[:4])
            month = int(text[4:])
    if not 1 <= month <= 12:
        raise ValueError(f"invalid revenue month: {value!r}")
    return f"{year:04d}-{month:02d}"


def parse_source_date(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace("/", "").replace("-", "")
    if not text:
        return None
    if not text.isdigit() or len(text) not in {7, 8}:
        raise ValueError(f"invalid source report date: {value!r}")
    if len(text) == 7:
        year = int(text[:3]) + 1911
        month = int(text[3:5])
        day = int(text[5:7])
    else:
        year = int(text[:4])
        month = int(text[4:6])
        day = int(text[6:8])
    return datetime(year, month, day).date().isoformat()


def parse_integer(value: Any, *, required: bool = False) -> int | None:
    if value is None:
        if required:
            raise ValueError("required integer value is missing")
        return None
    text = str(value).replace(",", "").strip()
    if not text or text in {"-", "--", "---", "N/A"}:
        if required:
            raise ValueError(f"required integer value is invalid: {value!r}")
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(f"invalid integer value: {value!r}") from exc


def parse_percentage(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text or text in {"-", "--", "---", "N/A"}:
        return None
    try:
        parsed = float(text)
    except ValueError as exc:
        raise ValueError(f"invalid percentage value: {value!r}") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"invalid percentage value: {value!r}")
    return parsed


def clean_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text in {"-", "--", "---"} else text


def business_content_hash(row: dict[str, Any]) -> str:
    payload = {column: row.get(column) for column in BUSINESS_COLUMNS}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def month_range(start_month: str, end_month: str) -> list[str]:
    start = parse_source_month(start_month)
    end = parse_source_month(end_month)
    start_year, start_number = map(int, start.split("-"))
    end_year, end_number = map(int, end.split("-"))
    start_index = start_year * 12 + start_number - 1
    end_index = end_year * 12 + end_number - 1
    if start_index > end_index:
        raise ValueError("start_month must not be after end_month")
    return [f"{index // 12:04d}-{index % 12 + 1:02d}" for index in range(start_index, end_index + 1)]


def _clean_header(value: Any) -> str:
    return str(value).lstrip("\ufeff").strip()


def _single_period(rows: list[dict[str, Any]]) -> str | None:
    periods = {row["revenue_month"] for row in rows}
    return next(iter(periods)) if len(periods) == 1 else None


def _snapshot_id(source: str, market: str, month: str | None, fetched_at: str, sha256: str) -> str:
    identity = "\0".join((source, market, month or "", fetched_at, sha256)).encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def _utc_now(value: datetime | None = None) -> str:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("observation time must be timezone-aware")
    return current.astimezone(UTC).isoformat(timespec="seconds")


if __name__ == "__main__":
    setup_logging("fetch_monthly_revenue.log")
    main()
