from __future__ import annotations

import argparse
import hashlib
import logging
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import requests

from config import config
from shp_utils.logging_utils import setup_logging

logger = logging.getLogger(__name__)

BASE_URL = "https://isin.twse.com.tw/isin/C_public.jsp"
DEFAULT_DB_PATH = config.DATA_DIR / "symbol_meta.sqlite"
DEFAULT_RAW_DIR = config.DATA_DIR / "raw" / "isin"

# Only these ISIN table segments describe tradable stocks/ETFs relevant to universes;
# warrants, bonds, TDRs, and REIT beneficiary certificates are excluded.
STOCK_SEGMENTS = {"股票", "創新板"}
ETF_SEGMENTS = {"ETF"}
INCLUDED_SEGMENTS = STOCK_SEGMENTS | ETF_SEGMENTS

MARKET_MODES = {
    "twse": "2",
    "tpex": "4",
}


@dataclass(frozen=True)
class SymbolClassification:
    code: str
    name: str | None
    market: str
    industry_category: str | None
    is_etf: bool
    listed_date: str | None
    security_category: str
    fetched_at: str


class IsinTableParser(HTMLParser):
    """Parses the TWSE/TPEx ISIN public listing table (header rows via colspan)."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[list[str], int | None]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None
        self._in_td = False
        self._current_colspan: int | None = None
        self._row_colspan: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._current_row = []
            self._row_colspan = None
        elif tag == "td":
            self._in_td = True
            self._current_cell = []
            colspan = dict(attrs).get("colspan")
            self._current_colspan = int(colspan) if colspan else None

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._current_row is not None:
            text = "".join(self._current_cell or []).strip()
            self._current_row.append(text)
            if self._current_colspan and self._current_colspan > 1:
                self._row_colspan = self._current_colspan
            self._in_td = False
        elif tag == "tr" and self._current_row is not None:
            self.rows.append((self._current_row, self._row_colspan))
            self._current_row = None

    def handle_data(self, data: str) -> None:
        if self._in_td and self._current_cell is not None:
            self._current_cell.append(data)


def main() -> None:
    setup_logging("fetch_symbol_meta.log")
    parser = argparse.ArgumentParser(description="Fetch and index TWSE/TPEx ISIN symbol classification data.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser("fetch", help="Fetch ISIN listing data.")
    fetch_parser.add_argument(
        "--markets",
        default="twse,tpex",
        help="Comma-separated markets to fetch: twse, tpex.",
    )
    fetch_parser.add_argument("--retries", type=int, default=3)
    fetch_parser.add_argument("--retry-sleep", type=float, default=5.0)
    fetch_parser.add_argument("--timeout", type=float, default=30.0)
    fetch_parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    fetch_parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)

    status_parser = subparsers.add_parser("status", help="Show local classification coverage.")
    status_parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)

    lookup_parser = subparsers.add_parser("lookup", help="Look up a symbol's classification.")
    lookup_parser.add_argument("--code", required=True)
    lookup_parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)

    args = parser.parse_args()
    if args.command == "fetch":
        run_fetch(args)
    elif args.command == "status":
        run_status(args)
    elif args.command == "lookup":
        run_lookup(args)


def run_fetch(args: argparse.Namespace) -> None:
    markets = [item.strip() for item in args.markets.split(",") if item.strip()]
    unknown = [market for market in markets if market not in MARKET_MODES]
    if unknown:
        raise SystemExit(f"Unknown market(s): {unknown}. Expected one of {sorted(MARKET_MODES)}.")

    init_db(args.db_path)
    args.raw_dir.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(args.db_path) as conn:
        for market in markets:
            html, url = fetch_isin_page(
                market,
                timeout=args.timeout,
                retries=args.retries,
                retry_sleep=args.retry_sleep,
            )
            sha256 = hashlib.sha256(html.encode("utf-8")).hexdigest()
            write_raw_snapshot(args.raw_dir, market, html, sha256)

            rows = parse_isin_html(html)
            upsert_rows(conn, rows)
            upsert_fetch_log(conn, market=market, url=url, row_count=len(rows), sha256=sha256)
            conn.commit()
            logger.info("fetched %s: %d rows", market, len(rows))


DEFAULT_MAX_AGE_DAYS = 7
# A listing page that suddenly holds far fewer rows than the previous fetch is a
# broken response, not a mass delisting; refuse it rather than record it.
MIN_ROW_FRACTION_OF_PREVIOUS = 0.5


def write_raw_snapshot(raw_dir: Path, market: str, html: str, sha256: str) -> Path:
    """Keep each fetched listing page as its own file, never overwriting an earlier one.

    The name carries the fetch time and content hash, so a later fetch (or a rerun in the
    same second with different content) adds evidence instead of replacing it. The legacy
    fixed-name ``<market>.html`` files from earlier fetches are left untouched.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = raw_dir / f"{market}-{stamp}-{sha256[:12]}.html"
    raw_dir.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="") as handle:
            handle.write(html)
    except FileExistsError:
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
            raise
    return path


def refresh_symbol_meta(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    raw_dir: Path = DEFAULT_RAW_DIR,
    now: datetime | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    markets: tuple[str, ...] = tuple(MARKET_MODES),
    retries: int = 3,
    retry_sleep: float = 5.0,
    timeout: float = 30.0,
    force: bool = False,
) -> bool:
    """Refresh the ISIN listing when any market's last fetch is older than max_age_days.

    Rows are only upserted, never deleted, so a delisted code keeps its last known
    classification. Returns True when the network was used. Nothing is written for any
    market unless every requested market parsed to a plausible number of rows, so one
    broken page cannot leave the table half-refreshed.
    """
    now = now or datetime.now(UTC)
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        previous: dict[str, tuple[datetime | None, int]] = {}
        for market in markets:
            row = conn.execute(
                "SELECT fetched_at, row_count FROM symbol_meta_fetch_log WHERE market_mode = ? ORDER BY fetched_at DESC LIMIT 1",
                (market,),
            ).fetchone()
            previous[market] = (datetime.fromisoformat(row[0]) if row else None, row[1] if row else 0)
        cutoff = now - timedelta(days=max_age_days)
        if not force and all(fetched is not None and fetched >= cutoff for fetched, _ in previous.values()):
            logger.info("symbol metadata is fresh; skipping network refresh")
            return False

        fetched_pages: list[tuple[str, str, str, list[dict[str, Any]]]] = []
        for market in markets:
            html, url = fetch_isin_page(market, timeout=timeout, retries=retries, retry_sleep=retry_sleep)
            rows = parse_isin_html(html)
            prior_count = previous[market][1]
            if not rows or len(rows) < prior_count * MIN_ROW_FRACTION_OF_PREVIOUS:
                raise RuntimeError(f"ISIN page for market={market} parsed to {len(rows)} rows (previous fetch: {prior_count}); not recorded")
            fetched_pages.append((market, url, html, rows))

        raw_dir.mkdir(parents=True, exist_ok=True)
        for market, url, html, rows in fetched_pages:
            sha256 = hashlib.sha256(html.encode("utf-8")).hexdigest()
            write_raw_snapshot(raw_dir, market, html, sha256)
            upsert_rows(conn, rows)
            upsert_fetch_log(conn, market=market, url=url, row_count=len(rows), sha256=sha256)
            logger.info("refreshed %s symbol metadata: %d rows", market, len(rows))
        conn.commit()
    return True


def run_status(args: argparse.Namespace) -> None:
    init_db(args.db_path)
    with sqlite3.connect(args.db_path) as conn:
        conn.row_factory = sqlite3.Row
        overview = conn.execute(
            """
            SELECT COUNT(*) AS rows, COUNT(DISTINCT market) AS markets, MAX(fetched_at) AS last_fetched
            FROM symbol_meta
            """
        ).fetchone()
        logger.info("db_path: %s", args.db_path)
        logger.info("symbol_rows: %s", overview["rows"])
        logger.info("markets: %s", overview["markets"])
        logger.info("last_fetched_at: %s", overview["last_fetched"])

        industry_counts = conn.execute(
            """
            SELECT industry_category, COUNT(*) AS rows
            FROM symbol_meta
            WHERE industry_category IS NOT NULL
            GROUP BY industry_category
            ORDER BY rows DESC
            """
        ).fetchall()
        logger.info("industry_categories:")
        for row in industry_counts:
            logger.info("  %s: %s", row["industry_category"], row["rows"])


def run_lookup(args: argparse.Namespace) -> None:
    with sqlite3.connect(args.db_path) as conn:
        classification = lookup_symbol(conn, args.code)
    if classification is None:
        logger.info("no classification data for code: %s", args.code)
        return
    logger.info(
        "%s %s market=%s industry=%s is_etf=%s listed_date=%s",
        classification.code,
        classification.name or "",
        classification.market,
        classification.industry_category,
        classification.is_etf,
        classification.listed_date,
    )


def lookup_symbol(conn: sqlite3.Connection, code: str) -> SymbolClassification | None:
    """Look up a symbol's classification, returning None when no data is available."""
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT code, name, market, industry_category, is_etf, listed_date, security_category, fetched_at
        FROM symbol_meta
        WHERE code = ?
        """,
        (code,),
    ).fetchone()
    if row is None:
        return None
    return SymbolClassification(
        code=row["code"],
        name=row["name"],
        market=row["market"],
        industry_category=row["industry_category"],
        is_etf=bool(row["is_etf"]),
        listed_date=row["listed_date"],
        security_category=row["security_category"],
        fetched_at=row["fetched_at"],
    )


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS symbol_meta (
                code TEXT PRIMARY KEY,
                name TEXT,
                isin TEXT,
                listed_date TEXT,
                market TEXT NOT NULL,
                industry_category TEXT,
                is_etf INTEGER NOT NULL DEFAULT 0,
                security_category TEXT NOT NULL,
                cfi_code TEXT,
                fetched_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS symbol_meta_fetch_log (
                market_mode TEXT NOT NULL,
                url TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                PRIMARY KEY (market_mode, fetched_at)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_symbol_meta_industry
            ON symbol_meta (industry_category)
            """
        )
        conn.commit()
    finally:
        conn.close()


def fetch_isin_page(
    market: str,
    *,
    timeout: float,
    retries: int,
    retry_sleep: float,
) -> tuple[str, str]:
    params = {"strMode": MARKET_MODES[market]}
    last_error: Exception | None = None
    attempts = max(retries, 0) + 1
    for attempt in range(1, attempts + 1):
        response = requests.get(
            BASE_URL,
            params=params,
            timeout=timeout,
            headers={"User-Agent": "StockResearchBot/0.1 local research"},
        )
        try:
            response.raise_for_status()
            response.encoding = "cp950"
            return response.text, response.url
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= attempts:
                raise RuntimeError(f"Failed to fetch ISIN page for market={market}") from exc
            time.sleep(retry_sleep * attempt)
    raise RuntimeError(f"Failed to fetch ISIN page for market={market}") from last_error


def parse_isin_html(html: str) -> list[dict[str, Any]]:
    """Parse the ISIN table into classification rows, keeping only stock/ETF segments."""
    table_parser = IsinTableParser()
    table_parser.feed(html)
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")

    rows: list[dict[str, Any]] = []
    segment: str | None = None
    for cells, colspan in table_parser.rows:
        if colspan and colspan > 1:
            segment = cells[0].strip() if cells else None
            continue
        if segment not in INCLUDED_SEGMENTS or len(cells) != 7:
            continue

        code_and_name, isin, listed_date_raw, market, industry_category, cfi_code, _note = cells
        code, _, name = code_and_name.partition("　")
        code = code.strip()
        if not code:
            continue

        rows.append({
            "code": code,
            "name": name.strip() or None,
            "isin": isin.strip() or None,
            "listed_date": parse_listed_date(listed_date_raw),
            "market": market.strip(),
            "industry_category": industry_category.strip() or None,
            "is_etf": 1 if segment in ETF_SEGMENTS else 0,
            "security_category": segment,
            "cfi_code": cfi_code.strip() or None,
            "fetched_at": fetched_at,
        })
    return rows


def parse_listed_date(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    parts = value.split("/")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    year, month, day = parts
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def upsert_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO symbol_meta (
            code, name, isin, listed_date, market, industry_category,
            is_etf, security_category, cfi_code, fetched_at
        )
        VALUES (
            :code, :name, :isin, :listed_date, :market, :industry_category,
            :is_etf, :security_category, :cfi_code, :fetched_at
        )
        ON CONFLICT(code) DO UPDATE SET
            name = excluded.name,
            isin = excluded.isin,
            listed_date = excluded.listed_date,
            market = excluded.market,
            industry_category = excluded.industry_category,
            is_etf = excluded.is_etf,
            security_category = excluded.security_category,
            cfi_code = excluded.cfi_code,
            fetched_at = excluded.fetched_at
        """,
        rows,
    )


def upsert_fetch_log(conn: sqlite3.Connection, *, market: str, url: str, row_count: int, sha256: str) -> None:
    conn.execute(
        """
        INSERT INTO symbol_meta_fetch_log (market_mode, url, fetched_at, row_count, sha256)
        VALUES (?, ?, ?, ?, ?)
        """,
        (market, url, datetime.now(UTC).isoformat(timespec="seconds"), row_count, sha256),
    )


if __name__ == "__main__":
    main()
