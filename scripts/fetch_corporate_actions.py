from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from config import config
from shp_utils.logging_utils import setup_logging
from shp_utils.official_api import fetch_json_with_retry_url

logger = logging.getLogger(__name__)

MARKET_TWSE = "TWSE"
MARKET_TPEX = "TPEx"
TWSE_BASE_URL = "https://www.twse.com.tw"
TPEX_BASE_URL = "https://www.tpex.org.tw/www/zh-tw"
DEFAULT_DB_PATH = config.DATA_DIR / "corporate_actions.sqlite"
# One level above the market segment: `run_fetch` appends `<market>/<source>/`, which
# reproduces the existing `data/raw/twse/<source>/` paths exactly, so no file moves.
DEFAULT_RAW_DIR = config.DATA_DIR / "raw"
FIRST_TWSE_DATE = "20030505"

# Each endpoint's own data floor, from `docs/tpex-history-sources.md`.
FIRST_EXDAILYQ_DATE = "20080102"
FIRST_REVIVT_DATE = "20130101"
FIRST_TPEX_ETF_DATE = "20210601"

# TWSE wants YYYYMMDD in the query string; TPEx wants YYYY/MM/DD. Both call the
# parameters startDate/endDate, which is why only the format is configurable.
DATE_PARAM_COMPACT = "compact"
DATE_PARAM_SLASHED = "slashed"

# TWSE returns fields/data at the top level; TPEx nests them under tables[0]
# (design.md F6).
PAYLOAD_FLAT = "flat"
PAYLOAD_TABLES = "tables"

SOURCE_TWT49U = "twse_twt49u"
SOURCE_TWTCAU = "twse_twtcau"
SOURCE_TWTAUU = "twse_twtauu"
SOURCE_EXDAILYQ = "tpex_exdailyq"
SOURCE_REVIVT = "tpex_revivt"
SOURCE_ETFSPLIT = "tpex_etfsplit"
SOURCE_ETFRVS = "tpex_etfrvs"

EVENT_CASH_DIVIDEND = "CASH_DIVIDEND"
EVENT_EX_RIGHT = "EX_RIGHT"
EVENT_EX_RIGHT_AND_DIVIDEND = "EX_RIGHT_AND_DIVIDEND"
EVENT_ETF_SPLIT = "ETF_SPLIT"
EVENT_ETF_REVERSE_SPLIT = "ETF_REVERSE_SPLIT"
EVENT_CASH_CAPITAL_REDUCTION = "CASH_CAPITAL_REDUCTION"
EVENT_LOSS_OFFSET_CAPITAL_REDUCTION = "LOSS_OFFSET_CAPITAL_REDUCTION"
EVENT_CAPITAL_REDUCTION = "CAPITAL_REDUCTION"
EVENT_UNKNOWN = "UNKNOWN"

FACTOR_METHOD = "TWSE_REFERENCE_PRICE_RATIO"
EVIDENCE_FULL = "FULL"
EVIDENCE_REFERENCE_PRICE_ONLY = "REFERENCE_PRICE_ONLY"


@dataclass(frozen=True)
class SourceConfig:
    key: str
    source: str
    path: str
    cache_dir: str
    first_date: str
    market: str = MARKET_TWSE
    base_url: str = TWSE_BASE_URL
    payload_shape: str = PAYLOAD_FLAT
    date_param_format: str = DATE_PARAM_COMPACT
    # Set only where the endpoint itself is the event type. TPEx's ETF split and
    # reverse-split bulletins carry no column saying which they are -- unlike TWSE's
    # `twtcau`, which has a `分割(反分割)` field -- so the source has to say it.
    implied_event_type: str | None = None


@dataclass(frozen=True)
class FetchRange:
    start_date: str
    end_date: str

    @property
    def cache_name(self) -> str:
        return f"{self.start_date}_{self.end_date}.json"


SOURCE_CONFIGS = {
    "twt49u": SourceConfig(
        key="twt49u",
        source=SOURCE_TWT49U,
        path="/rwd/zh/exRight/TWT49U",
        cache_dir="twt49u",
        first_date=FIRST_TWSE_DATE,
    ),
    "twtcau": SourceConfig(
        key="twtcau",
        source=SOURCE_TWTCAU,
        path="/rwd/zh/split/TWTCAU",
        cache_dir="twtcau",
        first_date=FIRST_TWSE_DATE,
    ),
    "twtauu": SourceConfig(
        key="twtauu",
        source=SOURCE_TWTAUU,
        path="/rwd/zh/reducation/TWTAUU",
        cache_dir="twtauu",
        first_date=FIRST_TWSE_DATE,
    ),
    "exdailyq": SourceConfig(
        key="exdailyq",
        source=SOURCE_EXDAILYQ,
        path="/bulletin/exDailyQ",
        cache_dir="exdailyq",
        first_date=FIRST_EXDAILYQ_DATE,
        market=MARKET_TPEX,
        base_url=TPEX_BASE_URL,
        payload_shape=PAYLOAD_TABLES,
        date_param_format=DATE_PARAM_SLASHED,
    ),
    "revivt": SourceConfig(
        key="revivt",
        source=SOURCE_REVIVT,
        path="/bulletin/revivt",
        cache_dir="revivt",
        first_date=FIRST_REVIVT_DATE,
        market=MARKET_TPEX,
        base_url=TPEX_BASE_URL,
        payload_shape=PAYLOAD_TABLES,
        date_param_format=DATE_PARAM_SLASHED,
    ),
    # Registered but unobserved: both respond with the correct header and zero rows
    # across their whole available window. Per the spec, that is not evidence the
    # event never happens, so they stay registered and keep being queried.
    "etfsplit": SourceConfig(
        key="etfsplit",
        source=SOURCE_ETFSPLIT,
        path="/bulletin/etfSplitRslt",
        cache_dir="etfsplit",
        first_date=FIRST_TPEX_ETF_DATE,
        market=MARKET_TPEX,
        base_url=TPEX_BASE_URL,
        payload_shape=PAYLOAD_TABLES,
        date_param_format=DATE_PARAM_SLASHED,
        implied_event_type=EVENT_ETF_SPLIT,
    ),
    "etfrvs": SourceConfig(
        key="etfrvs",
        source=SOURCE_ETFRVS,
        path="/bulletin/etfRvsRslt",
        cache_dir="etfrvs",
        first_date=FIRST_TPEX_ETF_DATE,
        market=MARKET_TPEX,
        base_url=TPEX_BASE_URL,
        payload_shape=PAYLOAD_TABLES,
        date_param_format=DATE_PARAM_SLASHED,
        implied_event_type=EVENT_ETF_REVERSE_SPLIT,
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and index TWSE corporate action data.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser("fetch", help="Fetch TWSE source data.")
    fetch_parser.add_argument(
        "--source",
        choices=sorted(SOURCE_CONFIGS),
        default="twt49u",
        help="TWSE source to fetch.",
    )
    fetch_parser.add_argument("--start-year", type=int)
    fetch_parser.add_argument("--end-year", type=int)
    fetch_parser.add_argument("--start-date")
    fetch_parser.add_argument("--end-date")
    fetch_parser.add_argument("--sleep", type=float, default=1.5)
    fetch_parser.add_argument("--retries", type=int, default=3)
    fetch_parser.add_argument("--retry-sleep", type=float, default=5.0)
    fetch_parser.add_argument("--force", action="store_true")
    fetch_parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    fetch_parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)

    status_parser = subparsers.add_parser("status", help="Show local data coverage.")
    status_parser.add_argument("--code")
    status_parser.add_argument("--event-type")
    status_parser.add_argument("--limit", type=int, default=20)
    status_parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)

    args = parser.parse_args()
    if args.command == "fetch":
        run_fetch(args)
    elif args.command == "status":
        run_status(args)


def run_fetch(args: argparse.Namespace) -> None:
    source_config = SOURCE_CONFIGS[args.source]
    ranges = build_fetch_ranges(
        start_year=args.start_year,
        end_year=args.end_year,
        start_date=args.start_date,
        end_date=args.end_date,
        first_date=source_config.first_date,
    )
    init_db(args.db_path)
    raw_dir = args.raw_dir / source_config.market.lower() / source_config.cache_dir
    raw_dir.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(args.db_path) as conn:
        for index, fetch_range in enumerate(ranges, start=1):
            if is_range_cached(conn, source_config, fetch_range) and not args.force:
                logger.info(
                    "skip cached %s %s-%s (%d/%d)",
                    source_config.key,
                    fetch_range.start_date,
                    fetch_range.end_date,
                    index,
                    len(ranges),
                )
                continue

            payload, url = fetch_source_range(
                source_config,
                fetch_range,
                retries=args.retries,
                retry_sleep=args.retry_sleep,
            )
            raw_path = raw_dir / fetch_range.cache_name
            raw_text = json.dumps(payload, ensure_ascii=False, indent=2)
            raw_path.write_text(raw_text, encoding="utf-8")
            sha256 = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

            rows = normalize_payload(source_config, payload)
            upsert_rows(conn, rows)
            upsert_raw_range(
                conn=conn,
                source_config=source_config,
                fetch_range=fetch_range,
                url=url,
                raw_path=raw_path,
                sha256=sha256,
                status=str(payload.get("stat", "")),
                row_count=len(rows),
            )
            conn.commit()
            logger.info(
                "fetched %s %s-%s: %d rows (%d/%d)",
                source_config.key,
                fetch_range.start_date,
                fetch_range.end_date,
                len(rows),
                index,
                len(ranges),
            )
            if index < len(ranges) and args.sleep > 0:
                time.sleep(args.sleep)


def run_status(args: argparse.Namespace) -> None:
    init_db(args.db_path)
    with sqlite3.connect(args.db_path) as conn:
        conn.row_factory = sqlite3.Row
        overview = conn.execute(
            """
            SELECT
                COUNT(*) AS rows,
                COUNT(DISTINCT code) AS codes,
                MIN(ex_date) AS min_date,
                MAX(ex_date) AS max_date
            FROM corporate_actions
            """
        ).fetchone()
        raw_ranges = conn.execute("SELECT COUNT(*) AS ranges FROM corporate_action_raw_ranges").fetchone()["ranges"]
        logger.info("db_path: %s", args.db_path)
        logger.info("raw_ranges: %s", raw_ranges)
        logger.info("corporate_action_rows: %s", overview["rows"])
        logger.info("distinct_codes: %s", overview["codes"])
        logger.info("date_range: %s to %s", overview["min_date"], overview["max_date"])

        event_counts = conn.execute(
            """
            SELECT event_type, COUNT(*) AS rows
            FROM corporate_actions
            GROUP BY event_type
            ORDER BY rows DESC
            """
        ).fetchall()
        logger.info("event_types:")
        for row in event_counts:
            logger.info("  %s: %s", row["event_type"], row["rows"])

        source_counts = conn.execute(
            """
            SELECT source, COUNT(*) AS rows
            FROM corporate_actions
            GROUP BY source
            ORDER BY rows DESC
            """
        ).fetchall()
        logger.info("sources:")
        for row in source_counts:
            logger.info("  %s: %s", row["source"], row["rows"])

        yearly = conn.execute(
            """
            SELECT
                substr(ex_date, 1, 4) AS year,
                COUNT(*) AS rows,
                COUNT(DISTINCT code) AS codes,
                MIN(ex_date) AS min_date,
                MAX(ex_date) AS max_date
            FROM corporate_actions
            GROUP BY substr(ex_date, 1, 4)
            ORDER BY year DESC
            LIMIT 12
            """
        ).fetchall()
        if yearly:
            logger.info("yearly_coverage:")
            for row in yearly:
                logger.info(
                    "  %s: rows=%s, codes=%s, %s..%s",
                    row["year"],
                    row["rows"],
                    row["codes"],
                    row["min_date"],
                    row["max_date"],
                )

        if args.code or args.event_type:
            filters = []
            params: list[Any] = []
            if args.code:
                filters.append("code = ?")
                params.append(args.code)
            if args.event_type:
                filters.append("event_type = ?")
                params.append(args.event_type)
            params.append(args.limit)
            rows = conn.execute(
                f"""
                SELECT ex_date, code, name, event_type, action_type, previous_close,
                       reference_price, value_plus_dividend, cash_dividend_estimate,
                       price_factor, share_factor, cash_per_share, evidence_status
                FROM corporate_actions
                WHERE {" AND ".join(filters)}
                ORDER BY ex_date DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            query_label = args.code or "all_codes"
            logger.info("events_%s:", query_label)
            for row in rows:
                logger.info(
                    "  %s %s %s event=%s raw=%s prev=%s ref=%s value=%s cash_est=%s price_factor=%s share_factor=%s cash_per_share=%s evidence=%s",
                    row["ex_date"],
                    row["code"],
                    row["name"],
                    row["event_type"],
                    row["action_type"],
                    row["previous_close"],
                    row["reference_price"],
                    row["value_plus_dividend"],
                    row["cash_dividend_estimate"],
                    row["price_factor"],
                    row["share_factor"],
                    row["cash_per_share"],
                    row["evidence_status"],
                )


def build_fetch_ranges(
    *,
    start_year: int | None,
    end_year: int | None,
    start_date: str | None,
    end_date: str | None,
    first_date: str,
) -> list[FetchRange]:
    if start_date or end_date:
        if not start_date or not end_date:
            raise SystemExit("--start-date and --end-date must be provided together.")
        return [FetchRange(normalize_yyyymmdd(start_date), normalize_yyyymmdd(end_date))]

    current_year = date.today().year
    # Each source has its own floor (TWSE 2003-05-05, exDailyQ 2008-01-02, revivt
    # 2013-01-01, the TPEx ETF bulletins 2021-06-01). Deriving the first year from
    # `first_date` rather than a hardcoded 2003 keeps a no-argument run from asking
    # every endpoint for years it cannot answer for.
    first_year = int(first_date[:4])
    start_year = start_year or first_year
    end_year = end_year or current_year
    if start_year > end_year:
        raise SystemExit("--start-year cannot be greater than --end-year.")

    ranges: list[FetchRange] = []
    for year in range(start_year, end_year + 1):
        start = f"{year}0101"
        end = f"{year}1231"
        if start < first_date:
            start = first_date
        if end < first_date:
            continue
        if year == current_year:
            end = date.today().strftime("%Y%m%d")
        ranges.append(FetchRange(start, end))
    return ranges


def normalize_yyyymmdd(value: str) -> str:
    compact = value.replace("-", "").replace("/", "").strip()
    if len(compact) != 8 or not compact.isdigit():
        raise SystemExit(f"Expected YYYYMMDD date, got: {value}")
    return compact


def rename_legacy_raw_ranges(conn: sqlite3.Connection) -> None:
    """Move `twse_twt49u_raw_ranges` to `corporate_action_raw_ranges`.

    The old name was wrong twice over once TPEx arrived: not TWSE-only, and not
    `twt49u`-only (it has held `twtcau` and `twtauu` rows since those sources were
    added). ALTER TABLE RENAME moves the rows atomically and destroys nothing --
    `.claude/rules/common/data-structures.md` rules out any migration containing a
    DROP. The key does not change: `source` already carries a market prefix, so
    `tpex_exdailyq` coexists with `twse_twt49u` without collision.
    """
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "twse_twt49u_raw_ranges" in names and "corporate_action_raw_ranges" not in names:
        conn.execute("ALTER TABLE twse_twt49u_raw_ranges RENAME TO corporate_action_raw_ranges")
        logger.info("renamed twse_twt49u_raw_ranges to corporate_action_raw_ranges")


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        rename_legacy_raw_ranges(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS corporate_actions (
                source TEXT NOT NULL,
                market TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT,
                ex_date TEXT NOT NULL,
                action_type TEXT,
                event_type TEXT NOT NULL DEFAULT 'UNKNOWN',
                previous_close REAL,
                reference_price REAL,
                value_plus_dividend REAL,
                limit_up_price REAL,
                limit_down_price REAL,
                auction_base_price REAL,
                ex_dividend_reference_price REAL,
                detail_key TEXT,
                cash_dividend_estimate REAL,
                price_adjustment_factor REAL,
                price_factor REAL,
                share_factor REAL,
                cash_per_share REAL,
                evidence_status TEXT,
                factor_method TEXT,
                raw_fields_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (source, market, code, ex_date)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS corporate_action_raw_ranges (
                source TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                url TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                status TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                raw_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                PRIMARY KEY (source, start_date, end_date)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_corporate_actions_code_date
            ON corporate_actions (code, ex_date)
            """
        )
        ensure_column(
            conn,
            "corporate_actions",
            "event_type",
            "TEXT NOT NULL DEFAULT 'UNKNOWN'",
        )
        ensure_column(conn, "corporate_actions", "price_adjustment_factor", "REAL")
        ensure_column(conn, "corporate_actions", "price_factor", "REAL")
        ensure_column(conn, "corporate_actions", "share_factor", "REAL")
        ensure_column(conn, "corporate_actions", "cash_per_share", "REAL")
        ensure_column(conn, "corporate_actions", "evidence_status", "TEXT")
        ensure_column(conn, "corporate_actions", "factor_method", "TEXT")
        backfill_derived_fields(conn)
        create_views(conn)


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def backfill_derived_fields(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE corporate_actions
        SET event_type = CASE
                WHEN source = ? AND action_type = '息' THEN ?
                WHEN source = ? AND action_type = '權' THEN ?
                WHEN source = ? AND action_type = '權息' THEN ?
                WHEN source = ? AND action_type = '分割' THEN ?
                WHEN source = ? AND action_type = '反分割' THEN ?
                WHEN source = ? AND action_type = '退還股款' THEN ?
                WHEN source = ? AND action_type = '彌補虧損' THEN ?
                WHEN source = ? THEN ?
                ELSE event_type
            END,
            price_adjustment_factor = CASE
                WHEN previous_close > 0 AND reference_price > 0
                    THEN reference_price / previous_close
                ELSE NULL
            END,
            price_factor = CASE
                WHEN previous_close > 0 AND reference_price > 0
                    THEN reference_price / previous_close
                ELSE NULL
            END,
            evidence_status = CASE
                WHEN source IN (?, ?) THEN ?
                WHEN source = ? AND action_type != '息' THEN ?
                WHEN evidence_status IS NULL THEN ?
                ELSE evidence_status
            END,
            factor_method = CASE
                WHEN previous_close > 0 AND reference_price > 0 THEN ?
                ELSE NULL
            END
        """,
        (
            SOURCE_TWT49U,
            EVENT_CASH_DIVIDEND,
            SOURCE_TWT49U,
            EVENT_EX_RIGHT,
            SOURCE_TWT49U,
            EVENT_EX_RIGHT_AND_DIVIDEND,
            SOURCE_TWTCAU,
            EVENT_ETF_SPLIT,
            SOURCE_TWTCAU,
            EVENT_ETF_REVERSE_SPLIT,
            SOURCE_TWTAUU,
            EVENT_CASH_CAPITAL_REDUCTION,
            SOURCE_TWTAUU,
            EVENT_LOSS_OFFSET_CAPITAL_REDUCTION,
            SOURCE_TWTAUU,
            EVENT_CAPITAL_REDUCTION,
            SOURCE_TWTCAU,
            SOURCE_TWTAUU,
            EVIDENCE_REFERENCE_PRICE_ONLY,
            SOURCE_TWT49U,
            EVIDENCE_REFERENCE_PRICE_ONLY,
            EVIDENCE_FULL,
            FACTOR_METHOD,
        ),
    )


def create_views(conn: sqlite3.Connection) -> None:
    conn.execute("DROP VIEW IF EXISTS cash_dividend_events")
    conn.execute("DROP VIEW IF EXISTS price_adjustment_factors")
    conn.execute("DROP VIEW IF EXISTS split_events")
    conn.execute(
        """
        CREATE VIEW cash_dividend_events AS
        SELECT *
        FROM corporate_actions
        WHERE event_type = 'CASH_DIVIDEND'
        """
    )
    conn.execute(
        """
        CREATE VIEW price_adjustment_factors AS
        SELECT source, market, code, name, ex_date, event_type,
               price_factor, factor_method, evidence_status
        FROM corporate_actions
        WHERE price_factor IS NOT NULL
        """
    )
    conn.execute(
        """
        CREATE VIEW split_events AS
        SELECT *
        FROM corporate_actions
        WHERE event_type IN ('ETF_SPLIT', 'ETF_REVERSE_SPLIT')
        """
    )


def is_range_cached(conn: sqlite3.Connection, source_config: SourceConfig, fetch_range: FetchRange) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM corporate_action_raw_ranges
        WHERE source = ? AND start_date = ? AND end_date = ?
        """,
        (source_config.source, fetch_range.start_date, fetch_range.end_date),
    ).fetchone()
    return row is not None


def format_date_param(value: str, date_param_format: str) -> str:
    """`20260819` -> `20260819` (TWSE) or `2026/08/19` (TPEx)."""
    if date_param_format == DATE_PARAM_SLASHED:
        return f"{value[:4]}/{value[4:6]}/{value[6:8]}"
    return value


def fetch_source_range(
    source_config: SourceConfig,
    fetch_range: FetchRange,
    *,
    retries: int,
    retry_sleep: float,
) -> tuple[dict[str, Any], str]:
    """Fetch one date range from a source, returning (payload, resolved URL).

    Retry and backoff live in the shared official-API helper so every fetcher in
    this repository gets the same behaviour from one implementation.
    """
    params = {
        "response": "json",
        "startDate": format_date_param(fetch_range.start_date, source_config.date_param_format),
        "endDate": format_date_param(fetch_range.end_date, source_config.date_param_format),
    }
    try:
        return fetch_json_with_retry_url(
            f"{source_config.base_url}{source_config.path}",
            params=params,
            retries=retries,
            retry_sleep=retry_sleep,
        )
    except RuntimeError as exc:
        raise RuntimeError(f"Failed to fetch {source_config.key} {fetch_range.start_date}-{fetch_range.end_date}: {exc}") from exc


def extract_fields_and_data(source_config: SourceConfig, payload: dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
    """Pull (fields, data) out of whichever envelope this source uses (design.md F6).

    Rows are always read by field *name* afterwards, never by position, so a source
    with more than one schema era parses without a per-era mapping table -- which is
    what makes `exDailyQ`'s 22-field and 21-field eras both work.
    """
    if source_config.payload_shape == PAYLOAD_TABLES:
        tables = payload.get("tables") or []
        if not tables:
            return [], []
        table = tables[0]
        return table.get("fields") or [], table.get("data") or []
    return payload.get("fields") or [], payload.get("data") or []


def normalize_payload(source_config: SourceConfig, payload: dict[str, Any]) -> list[dict[str, Any]]:
    if source_config.key == "twt49u":
        return normalize_twt49u_payload(source_config, payload)
    if source_config.key == "twtcau":
        return normalize_twtcau_payload(source_config, payload)
    if source_config.key == "twtauu":
        return normalize_twtauu_payload(source_config, payload)
    if source_config.key == "exdailyq":
        return normalize_exdailyq_payload(source_config, payload)
    if source_config.key == "revivt":
        return normalize_revivt_payload(source_config, payload)
    if source_config.key in ("etfsplit", "etfrvs"):
        return normalize_tpex_etf_payload(source_config, payload)
    raise ValueError(f"Unsupported source: {source_config.key}")


def normalize_twt49u_payload(source_config: SourceConfig, payload: dict[str, Any]) -> list[dict[str, Any]]:
    fields, data = extract_fields_and_data(source_config, payload)
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    rows = []
    for raw_row in data:
        raw = dict(zip(fields, raw_row, strict=False))
        code = str(raw.get("股票代號", "")).strip()
        ex_date = parse_twse_date(str(raw.get("資料日期", "")).strip())
        if not code or not ex_date:
            continue
        value_plus_dividend = parse_number(raw.get("權值+息值"))
        action_type = str(raw.get("權/息", "")).strip() or None
        previous_close = parse_number(raw.get("除權息前收盤價"))
        reference_price = parse_number(raw.get("除權息參考價"))
        price_factor = calculate_price_factor(previous_close, reference_price)
        event_type = classify_twt49u_event(action_type)
        rows.append({
            "source": source_config.source,
            "market": source_config.market,
            "code": code,
            "name": clean_text(raw.get("股票名稱")),
            "ex_date": ex_date,
            "action_type": action_type,
            "event_type": event_type,
            "previous_close": previous_close,
            "reference_price": reference_price,
            "value_plus_dividend": value_plus_dividend,
            "limit_up_price": parse_number(raw.get("漲停價格")),
            "limit_down_price": parse_number(raw.get("跌停價格")),
            "auction_base_price": parse_number(raw.get("開盤競價基準")),
            "ex_dividend_reference_price": parse_number(raw.get("減除股利參考價")),
            "detail_key": clean_text(raw.get("詳細資料")),
            "cash_dividend_estimate": estimate_cash_dividend(action_type, value_plus_dividend),
            "price_adjustment_factor": price_factor,
            "price_factor": price_factor,
            "share_factor": None,
            "cash_per_share": None,
            "evidence_status": (EVIDENCE_FULL if event_type == EVENT_CASH_DIVIDEND else EVIDENCE_REFERENCE_PRICE_ONLY),
            "factor_method": FACTOR_METHOD if price_factor is not None else None,
            "raw_fields_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
            "fetched_at": fetched_at,
        })
    return rows


def normalize_twtcau_payload(source_config: SourceConfig, payload: dict[str, Any]) -> list[dict[str, Any]]:
    fields, data = extract_fields_and_data(source_config, payload)
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    rows = []
    for raw_row in data:
        raw = dict(zip(fields, raw_row, strict=False))
        code = str(raw.get("ETF代號", "")).strip()
        ex_date = parse_twse_date(str(raw.get("恢復買賣日期", "")).strip())
        if not code or not ex_date:
            continue
        previous_close = parse_number(raw.get("停止買賣前收盤價格"))
        reference_price = parse_number(raw.get("恢復買賣參考價"))
        price_factor = calculate_price_factor(previous_close, reference_price)
        action_type = clean_text(raw.get("分割(反分割)"))
        rows.append({
            "source": source_config.source,
            "market": source_config.market,
            "code": code,
            "name": clean_text(raw.get("名稱")),
            "ex_date": ex_date,
            "action_type": action_type,
            "event_type": classify_etf_split_event(action_type),
            "previous_close": previous_close,
            "reference_price": reference_price,
            "value_plus_dividend": None,
            "limit_up_price": parse_number(raw.get("漲停價格")),
            "limit_down_price": parse_number(raw.get("跌停價格")),
            "auction_base_price": parse_number(raw.get("開盤競價基準")),
            "ex_dividend_reference_price": None,
            "detail_key": None,
            "cash_dividend_estimate": None,
            "price_adjustment_factor": price_factor,
            "price_factor": price_factor,
            "share_factor": None,
            "cash_per_share": None,
            "evidence_status": EVIDENCE_REFERENCE_PRICE_ONLY,
            "factor_method": FACTOR_METHOD if price_factor is not None else None,
            "raw_fields_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
            "fetched_at": fetched_at,
        })
    return rows


def normalize_twtauu_payload(source_config: SourceConfig, payload: dict[str, Any]) -> list[dict[str, Any]]:
    fields, data = extract_fields_and_data(source_config, payload)
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    rows = []
    for raw_row in data:
        raw = dict(zip(fields, raw_row, strict=False))
        code = str(raw.get("股票代號", "")).strip()
        ex_date = parse_twse_date(str(raw.get("恢復買賣日期", "")).strip())
        if not code or not ex_date:
            continue
        previous_close = parse_number(raw.get("停止買賣前收盤價格"))
        reference_price = parse_number(raw.get("恢復買賣參考價"))
        price_factor = calculate_price_factor(previous_close, reference_price)
        action_type = clean_text(raw.get("減資原因"))
        rows.append({
            "source": source_config.source,
            "market": source_config.market,
            "code": code,
            "name": clean_text(raw.get("名稱")),
            "ex_date": ex_date,
            "action_type": action_type,
            "event_type": classify_capital_reduction_event(action_type),
            "previous_close": previous_close,
            "reference_price": reference_price,
            "value_plus_dividend": None,
            "limit_up_price": parse_number(raw.get("漲停價格")),
            "limit_down_price": parse_number(raw.get("跌停價格")),
            "auction_base_price": parse_number(raw.get("開盤競價基準")),
            "ex_dividend_reference_price": parse_number(raw.get("除權參考價")),
            "detail_key": clean_text(raw.get("詳細資料")),
            "cash_dividend_estimate": None,
            "price_adjustment_factor": price_factor,
            "price_factor": price_factor,
            "share_factor": None,
            "cash_per_share": None,
            "evidence_status": EVIDENCE_REFERENCE_PRICE_ONLY,
            "factor_method": FACTOR_METHOD if price_factor is not None else None,
            "raw_fields_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
            "fetched_at": fetched_at,
        })
    return rows


def normalize_exdailyq_payload(source_config: SourceConfig, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """TPEx ex-rights / ex-dividend calculation results (`bulletin/exDailyQ`).

    The TPEx counterpart of `twt49u`. Same events, same factor method, different
    column names throughout (`代號` not `股票代號`, `漲停價` not `漲停價格`), which is
    why this is a separate normalizer rather than a shared one.

    Two schema eras exist -- 22 fields including `員工紅利轉增資` through about 2015,
    21 without it after -- and both are read here without a mapping table, because
    every field is addressed by name. Nothing in this function may key off the field
    count (design.md F4).
    """
    fields, data = extract_fields_and_data(source_config, payload)
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    rows = []
    for raw_row in data:
        raw = dict(zip(fields, raw_row, strict=False))
        code = str(raw.get("代號", "")).strip()
        ex_date = parse_twse_date(str(raw.get("除權息日期", "")).strip())
        if not code or not ex_date:
            continue
        previous_close = parse_number(raw.get("除權息前收盤價"))
        reference_price = parse_number(raw.get("除權息參考價"))
        price_factor = calculate_price_factor(previous_close, reference_price)
        action_type = str(raw.get("權/息", "")).strip() or None
        event_type = classify_twt49u_event(action_type)
        rows.append({
            "source": source_config.source,
            "market": source_config.market,
            "code": code,
            "name": clean_text(raw.get("名稱")),
            "ex_date": ex_date,
            "action_type": action_type,
            "event_type": event_type,
            "previous_close": previous_close,
            "reference_price": reference_price,
            "value_plus_dividend": parse_number(raw.get("權值+息值")),
            "limit_up_price": parse_number(raw.get("漲停價")),
            "limit_down_price": parse_number(raw.get("跌停價")),
            "auction_base_price": parse_number(raw.get("開始交易基準價")),
            "ex_dividend_reference_price": parse_number(raw.get("減除股利參考價")),
            "detail_key": None,
            # TPEx publishes the cash dividend outright, so this is a measured value
            # rather than the inference TWSE forces. `evidence_status` deliberately
            # keeps TWSE's rule anyway: promoting these rows to FULL would make the
            # column mean something different depending on the market, and that is
            # worth its own change rather than a side effect of adding one.
            "cash_dividend_estimate": parse_number(raw.get("現金股利")),
            "price_adjustment_factor": price_factor,
            "price_factor": price_factor,
            "share_factor": None,
            "cash_per_share": None,
            "evidence_status": (EVIDENCE_FULL if event_type == EVENT_CASH_DIVIDEND else EVIDENCE_REFERENCE_PRICE_ONLY),
            "factor_method": FACTOR_METHOD if price_factor is not None else None,
            "raw_fields_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
            "fetched_at": fetched_at,
        })
    return rows


def normalize_revivt_payload(source_config: SourceConfig, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """TPEx capital reduction, resumed-trading reference price (`bulletin/revivt`).

    The TPEx counterpart of `twtauu`. `詳細資料` is an embedded HTML table rather than
    a value, so only the named columns are parsed -- it is kept verbatim in
    `raw_fields_json` and as `detail_key`, but nothing reads inside it.
    """
    fields, data = extract_fields_and_data(source_config, payload)
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    rows = []
    for raw_row in data:
        raw = dict(zip(fields, raw_row, strict=False))
        code = str(raw.get("股票代號", "")).strip()
        ex_date = parse_twse_date(str(raw.get("恢復買賣日期", "")).strip())
        if not code or not ex_date:
            continue
        previous_close = parse_number(raw.get("最後交易日之收盤價格"))
        reference_price = parse_number(raw.get("減資恢復買賣開始日參考價格"))
        price_factor = calculate_price_factor(previous_close, reference_price)
        action_type = clean_text(raw.get("減資原因"))
        rows.append({
            "source": source_config.source,
            "market": source_config.market,
            "code": code,
            "name": clean_text(raw.get("名稱")),
            "ex_date": ex_date,
            "action_type": action_type,
            "event_type": classify_capital_reduction_event(action_type),
            "previous_close": previous_close,
            "reference_price": reference_price,
            "value_plus_dividend": None,
            "limit_up_price": parse_number(raw.get("漲停價格")),
            "limit_down_price": parse_number(raw.get("跌停價格")),
            "auction_base_price": parse_number(raw.get("開始交易基準價")),
            "ex_dividend_reference_price": parse_number(raw.get("除權參考價")),
            "detail_key": clean_text(raw.get("詳細資料")),
            "cash_dividend_estimate": None,
            "price_adjustment_factor": price_factor,
            "price_factor": price_factor,
            "share_factor": None,
            "cash_per_share": None,
            "evidence_status": EVIDENCE_REFERENCE_PRICE_ONLY,
            "factor_method": FACTOR_METHOD if price_factor is not None else None,
            "raw_fields_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
            "fetched_at": fetched_at,
        })
    return rows


def normalize_tpex_etf_payload(source_config: SourceConfig, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """TPEx ETF split / reverse split (`bulletin/etfSplitRslt`, `bulletin/etfRvsRslt`).

    Both bulletins share one 9-field header and, unlike TWSE's `twtcau`, carry no
    column saying which direction the event was -- the endpoint is the event type, so
    it comes from `source_config.implied_event_type`.

    Neither has returned a row across its whole available window. Per the spec that is
    not evidence the events do not occur, so these stay registered and queried, and
    this normalizer is written against the observed header rather than left unwritten.
    It is therefore untested against real rows, which is exactly why `action_type` is
    left None rather than invented: there is no field to put in it.
    """
    fields, data = extract_fields_and_data(source_config, payload)
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    rows = []
    for raw_row in data:
        raw = dict(zip(fields, raw_row, strict=False))
        code = str(raw.get("證券代號", "")).strip()
        ex_date = parse_twse_date(str(raw.get("恢復買賣日期", "")).strip())
        if not code or not ex_date:
            continue
        previous_close = parse_number(raw.get("最後交易日之收盤價格"))
        reference_price = parse_number(raw.get("恢復買賣開始參考價"))
        price_factor = calculate_price_factor(previous_close, reference_price)
        rows.append({
            "source": source_config.source,
            "market": source_config.market,
            "code": code,
            "name": clean_text(raw.get("證券名稱")),
            "ex_date": ex_date,
            "action_type": None,
            "event_type": source_config.implied_event_type or EVENT_UNKNOWN,
            "previous_close": previous_close,
            "reference_price": reference_price,
            "value_plus_dividend": None,
            "limit_up_price": parse_number(raw.get("漲停價格")),
            "limit_down_price": parse_number(raw.get("跌停價格")),
            "auction_base_price": parse_number(raw.get("開始交易基準價")),
            "ex_dividend_reference_price": None,
            "detail_key": clean_text(raw.get("詳細資料")),
            "cash_dividend_estimate": None,
            "price_adjustment_factor": price_factor,
            "price_factor": price_factor,
            "share_factor": None,
            "cash_per_share": None,
            "evidence_status": EVIDENCE_REFERENCE_PRICE_ONLY,
            "factor_method": FACTOR_METHOD if price_factor is not None else None,
            "raw_fields_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
            "fetched_at": fetched_at,
        })
    return rows


def upsert_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO corporate_actions (
            source, market, code, name, ex_date, action_type, event_type,
            previous_close, reference_price, value_plus_dividend, limit_up_price,
            limit_down_price, auction_base_price, ex_dividend_reference_price,
            detail_key, cash_dividend_estimate, price_adjustment_factor,
            price_factor, share_factor, cash_per_share, evidence_status,
            factor_method, raw_fields_json, fetched_at
        )
        VALUES (
            :source, :market, :code, :name, :ex_date, :action_type, :event_type,
            :previous_close, :reference_price, :value_plus_dividend,
            :limit_up_price, :limit_down_price, :auction_base_price,
            :ex_dividend_reference_price, :detail_key, :cash_dividend_estimate,
            :price_adjustment_factor, :price_factor, :share_factor,
            :cash_per_share, :evidence_status, :factor_method, :raw_fields_json,
            :fetched_at
        )
        ON CONFLICT(source, market, code, ex_date) DO UPDATE SET
            name = excluded.name,
            action_type = excluded.action_type,
            event_type = excluded.event_type,
            previous_close = excluded.previous_close,
            reference_price = excluded.reference_price,
            value_plus_dividend = excluded.value_plus_dividend,
            limit_up_price = excluded.limit_up_price,
            limit_down_price = excluded.limit_down_price,
            auction_base_price = excluded.auction_base_price,
            ex_dividend_reference_price = excluded.ex_dividend_reference_price,
            detail_key = excluded.detail_key,
            cash_dividend_estimate = excluded.cash_dividend_estimate,
            price_adjustment_factor = excluded.price_adjustment_factor,
            price_factor = excluded.price_factor,
            share_factor = excluded.share_factor,
            cash_per_share = excluded.cash_per_share,
            evidence_status = excluded.evidence_status,
            factor_method = excluded.factor_method,
            raw_fields_json = excluded.raw_fields_json,
            fetched_at = excluded.fetched_at
        """,
        rows,
    )


def upsert_raw_range(
    *,
    conn: sqlite3.Connection,
    source_config: SourceConfig,
    fetch_range: FetchRange,
    url: str,
    raw_path: Path,
    sha256: str,
    status: str,
    row_count: int,
) -> None:
    conn.execute(
        """
        INSERT INTO corporate_action_raw_ranges (
            source, start_date, end_date, url, fetched_at, status,
            row_count, raw_path, sha256
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, start_date, end_date) DO UPDATE SET
            url = excluded.url,
            fetched_at = excluded.fetched_at,
            status = excluded.status,
            row_count = excluded.row_count,
            raw_path = excluded.raw_path,
            sha256 = excluded.sha256
        """,
        (
            source_config.source,
            fetch_range.start_date,
            fetch_range.end_date,
            url,
            datetime.now(UTC).isoformat(timespec="seconds"),
            status,
            row_count,
            str(raw_path),
            sha256,
        ),
    )


def parse_twse_date(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    if "年" in value and "月" in value and "日" in value:
        year_text, rest = value.split("年", 1)
        month_text, day_text = rest.split("月", 1)
        day_text = day_text.replace("日", "")
        year = int(year_text) + 1911
        return f"{year:04d}-{int(month_text):02d}-{int(day_text):02d}"
    if "/" in value:
        parts = value.split("/")
        if len(parts) == 3 and all(part.isdigit() for part in parts):
            year = int(parts[0])
            if year < 1911:
                year += 1911
            return f"{year:04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    compact = value.replace("-", "").replace("/", "").strip()
    if not compact.isdigit():
        return None
    # TPEx serves compact ROC dates (`1020116`, `1140113`). ROC years since 2011 are
    # three digits, so 7 digits is unambiguously ROC and 8 is Gregorian YYYYMMDD.
    # This used to fall through to `normalize_yyyymmdd`, which raises SystemExit --
    # not an Exception, so one bad row would tear down the whole daily run instead of
    # being skipped. Returning None lets the caller's `if not ex_date: continue` work.
    if len(compact) == 7:
        return f"{int(compact[:3]) + 1911:04d}-{compact[3:5]}-{compact[5:7]}"
    if len(compact) == 8:
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
    return None


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text or text in {"--", "-", "N/A"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def estimate_cash_dividend(action_type: str | None, value_plus_dividend: float | None) -> float | None:
    if value_plus_dividend is None:
        return None
    # `除息` is TPEx's spelling of TWSE's `息` -- accepted here for the same reason the
    # classifiers accept both (design.md F2), even though the TPEx normalizer prefers
    # the published 現金股利 column over this estimate.
    if action_type in ("息", "除息"):
        return value_plus_dividend
    return None


def classify_twt49u_event(action_type: str | None) -> str:
    """Classify an ex-rights / ex-dividend row from either market.

    The two exchanges publish the same three events under different words: TWSE's
    `權/息` column says `息`, TPEx's says `除息`. Reusing the TWSE-only map against
    TPEx sent all 3,096 observed rows to UNKNOWN (design.md F2). Both vocabularies
    live here so the same event yields the same `event_type` whichever market it
    came from; the TWSE spellings must stay, or the 1,023 already-covered listed
    symbols would be silently reclassified.
    """
    return {
        "息": EVENT_CASH_DIVIDEND,
        "權": EVENT_EX_RIGHT,
        "權息": EVENT_EX_RIGHT_AND_DIVIDEND,
        "除息": EVENT_CASH_DIVIDEND,
        "除權": EVENT_EX_RIGHT,
        "除權息": EVENT_EX_RIGHT_AND_DIVIDEND,
    }.get(action_type or "", EVENT_UNKNOWN)


def classify_etf_split_event(action_type: str | None) -> str:
    if action_type == "分割":
        return EVENT_ETF_SPLIT
    if action_type == "反分割":
        return EVENT_ETF_REVERSE_SPLIT
    return EVENT_UNKNOWN


def classify_capital_reduction_event(action_type: str | None) -> str:
    """Classify a capital reduction from either market.

    `現金減資` is TPEx's word for what TWSE calls `退還股款` -- the same event, a cash
    capital reduction. Without it, 85 of the 281 observed TPEx reductions (30%) fall
    through to the generic `CAPITAL_REDUCTION` (design.md F1). `彌補虧損` is spelled
    identically by both exchanges. An unrecognized non-empty reason still degrades to
    the generic type rather than UNKNOWN: it is definitely a reduction, just not one
    whose subtype is known.
    """
    if action_type in ("退還股款", "現金減資"):
        return EVENT_CASH_CAPITAL_REDUCTION
    if action_type == "彌補虧損":
        return EVENT_LOSS_OFFSET_CAPITAL_REDUCTION
    if action_type:
        return EVENT_CAPITAL_REDUCTION
    return EVENT_UNKNOWN


def calculate_price_factor(previous_close: float | None, reference_price: float | None) -> float | None:
    if previous_close is None or previous_close <= 0 or reference_price is None:
        return None
    return reference_price / previous_close


if __name__ == "__main__":
    setup_logging("fetch_corporate_actions.log")
    main()
