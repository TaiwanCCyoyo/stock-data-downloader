"""Fetch and normalize daily TWSE/TPEx institutional-investor reports.

The generated ``data/institutional.sqlite`` artifact is point-in-time safe:
``published_at`` stays null while the sources expose no authoritative
publication timestamp, and ``fetched_at`` records when this pipeline first
observed each response. See ``docs/institutional-sources.md`` for the source
and schema research behind the signature tables below.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import time
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode

from config import config
from shp_utils.institutional_schema import (
    MARKET_TWSE,
    MARKETS,
    ROW_COLUMNS,
    _utc_now,
    normalize_tpex_payload,
    normalize_twse_payload,
)
from shp_utils.logging_utils import setup_logging
from shp_utils.official_api import fetch_json_with_retry_url

logger = logging.getLogger(__name__)

TWSE_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
# The only hosts a response for these reports may come back from. Checked by
# exact equality against the URL the response carries, so a cross-host
# redirect cannot file someone else's content as official evidence.
TWSE_HOST = "www.twse.com.tw"
TPEX_HOST = "www.tpex.org.tw"
FIRST_TWSE_DATE = date(2012, 5, 2)
FIRST_TPEX_DATE = date(2014, 7, 1)
DEFAULT_DB_PATH = config.DATA_DIR / "institutional.sqlite"
DEFAULT_RAW_DIR = config.DATA_DIR / "raw" / "institutional"
DEFAULT_SYMBOL_META_PATH = config.DATA_DIR / "symbol_meta.sqlite"
DEFAULT_PRICE_PARQUET_PATH = config.DATA_DIR / "price_daily.parquet"
DEFAULT_THROTTLE_SECONDS = 2.5
DEFAULT_MAX_REQUESTS = 100
DEFAULT_DAILY_LOOKBACK_DAYS = 7
DEFAULT_BUSY_TIMEOUT_MS = 30_000
# The shape of this artifact, recorded inside it. Two additive migrations have
# already landed silently, so a consumer holding a copy could only tell which
# generation it was by inspecting `PRAGMA table_info` and knowing the history.
#
#   1  the original tables
#   2  + institutional_fetch_log.error_detail   (a failure leaves evidence)
#   3  + institutional_fetch_log.final_url      (which host actually answered)
#
# Bump this whenever a migration changes those tables, and add the line above.
SCHEMA_VERSION = 3
SCHEMA_VERSION_KEY = "schema_version"
# A unit whose checkpoint carries one of these has been answered for; anything
# else is still owed. ``empty`` is settled because a non-trading day genuinely
# has no report, while ``partial`` marks a day that should have had one.
SETTLED_STATUSES = ("ok", "empty")
STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_PARTIAL = "partial"
STATUS_ERROR = "error"
# Backfill defaults. TPEx answers a burst of daily requests in about half a
# second each off-peak, then stops answering entirely for minutes at a time when
# its origin is loaded, so the retry ladder has to be measured in minutes: at
# these settings one unit is retried at roughly 15s, 30s, 60s, 120s and 240s
# before it is recorded as an error and the run moves on.
DEFAULT_BACKFILL_RETRIES = 5
DEFAULT_BACKFILL_RETRY_SLEEP = 15.0
DEFAULT_BACKFILL_MAX_RETRY_SLEEP = 300.0
# After this many units fail back to back, sleep rather than keep asking. A
# server that has stopped answering is not helped by a steady stream of further
# requests, and neither is the chance of finishing the run.
DEFAULT_COOLDOWN_AFTER_FAILURES = 2
DEFAULT_COOLDOWN_SECONDS = 600.0
DEFAULT_STOP_AFTER_CONSECUTIVE_FAILURES = 6


@dataclass(frozen=True)
class FetchUnit:
    market: str
    report_date: date


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(f"PRAGMA busy_timeout = {DEFAULT_BUSY_TIMEOUT_MS}")
    return conn


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS institutional_daily (
                market TEXT NOT NULL, date TEXT NOT NULL, code TEXT NOT NULL,
                name TEXT, instrument_type TEXT NOT NULL DEFAULT 'unknown',
                foreign_total_buy INTEGER, foreign_total_sell INTEGER, foreign_total_net INTEGER,
                foreign_excl_dealer_buy INTEGER, foreign_excl_dealer_sell INTEGER,
                foreign_excl_dealer_net INTEGER, foreign_dealer_buy INTEGER,
                foreign_dealer_sell INTEGER, foreign_dealer_net INTEGER,
                trust_buy INTEGER, trust_sell INTEGER, trust_net INTEGER,
                dealer_total_buy INTEGER, dealer_total_sell INTEGER, dealer_total_net INTEGER,
                dealer_self_buy INTEGER, dealer_self_sell INTEGER, dealer_self_net INTEGER,
                dealer_hedge_buy INTEGER, dealer_hedge_sell INTEGER, dealer_hedge_net INTEGER,
                three_institutions_net INTEGER, source TEXT NOT NULL,
                schema_signature TEXT NOT NULL, published_at TEXT, fetched_at TEXT NOT NULL,
                raw_fields_json TEXT NOT NULL, PRIMARY KEY (date, code)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS institutional_fetch_log (
                market TEXT NOT NULL, date TEXT NOT NULL, source_url TEXT NOT NULL,
                status TEXT NOT NULL, row_count INTEGER NOT NULL, schema_signature TEXT NOT NULL,
                published_at TEXT, fetched_at TEXT NOT NULL, raw_path TEXT NOT NULL,
                sha256 TEXT NOT NULL, error_detail TEXT, PRIMARY KEY (market, date)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
            )
            """
        )
        _add_missing_column(conn, "institutional_fetch_log", "error_detail", "TEXT")
        _add_missing_column(conn, "institutional_fetch_log", "final_url", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_institutional_daily_code_date ON institutional_daily (code, date)")
        _stamp_schema_version(conn)


def _stamp_schema_version(conn: sqlite3.Connection) -> None:
    """Record the shape this artifact now has, after the migrations above ran.

    Only when it actually changed. ``init_db`` runs on every daily fetch and
    every backfill invocation, so re-stamping ``updated_at`` unconditionally
    would dirty a page and change the file's bytes on every run with no schema
    change at all -- and a consumer freezing a snapshot hashes this file. The
    ``WHERE`` makes a repeat call a true no-op.
    """
    conn.execute(
        """
        INSERT INTO schema_meta (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        WHERE schema_meta.value <> excluded.value
        """,
        (SCHEMA_VERSION_KEY, str(SCHEMA_VERSION), _utc_now()),
    )


def read_schema_version(conn: sqlite3.Connection) -> int | None:
    """Which generation of this artifact the caller is holding.

    ``None`` means the database predates the meta table entirely, which is not
    an error -- every artifact built before 2026-09-03 is in that state until
    ``init_db`` next runs against it.

    **This describes columns, not rows.** A version 3 database that was migrated
    rather than built fresh still holds successes written before ``final_url``
    existed, and those carry no value in it. Use :func:`is_unit_cached`, which
    asks per row, rather than reading a version stamp as an assurance about
    content -- that inference is exactly the kind this artifact's provenance
    rules exist to prevent.
    """
    # Ask whether the table exists rather than catching OperationalError, which
    # also covers "database is locked" and I/O failures -- reporting those as
    # "this artifact predates the meta table" would let a manifest record a
    # version 3 artifact as having none.
    has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_meta'").fetchone()
    if has_table is None:
        return None
    row = conn.execute("SELECT value FROM schema_meta WHERE key = ?", (SCHEMA_VERSION_KEY,)).fetchone()
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def _add_missing_column(conn: sqlite3.Connection, table: str, column: str, declared_type: str) -> None:
    """Add a column to an existing artifact without touching its rows.

    Databases built before the column existed are the common case here, and the
    cache they hold is expensive to reacquire, so the migration has to be purely
    additive.
    """
    existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declared_type}")


def load_instrument_types(symbol_meta_path: Path = DEFAULT_SYMBOL_META_PATH) -> dict[str, str]:
    if not symbol_meta_path.is_file():
        return {}
    try:
        with sqlite3.connect(f"file:{symbol_meta_path.as_posix()}?mode=ro", uri=True) as conn:
            rows = conn.execute("SELECT code, is_etf, security_category FROM symbol_meta").fetchall()
    except sqlite3.Error:
        logger.warning("could not read symbol metadata from %s", symbol_meta_path, exc_info=True)
        return {}
    result: dict[str, str] = {}
    for code, is_etf, security_category in rows:
        if is_etf:
            result[str(code)] = "etf"
        elif str(security_category).strip() in {"股票", "創新板"}:
            result[str(code)] = "stock"
        else:
            result[str(code)] = "other"
    return result


def upsert_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = ", ".join(ROW_COLUMNS)
    placeholders = ", ".join(f":{column}" for column in ROW_COLUMNS)
    updates = ", ".join(f"{column} = excluded.{column}" for column in ROW_COLUMNS if column not in {"date", "code"})
    conn.executemany(
        f"""
        INSERT INTO institutional_daily ({columns})
        VALUES ({placeholders})
        ON CONFLICT(date, code) DO UPDATE SET {updates}
        """,
        rows,
    )


def is_unit_cached(conn: sqlite3.Connection, unit: FetchUnit) -> bool:
    """Whether this unit has been answered in a way a consumer can verify.

    Not the same question as :data:`SETTLED_STATUSES`, which guards against
    demoting a checkpoint that holds data. A row can be settled for that purpose
    and still be owed here: ``final_url`` was added after these artifacts were
    built, the migration only adds the column as NULL, and a status check alone
    would leave every pre-existing success without the one field that proves
    which host actually answered. Those units are re-fetched, which writes the
    real value; nothing back-fills a presumed URL from ``source_url``, because
    the requested URL is exactly what cannot stand in for the response's.

    ``empty`` settles without one on purpose: a non-trading day has no response
    body to have provenance about.
    """
    row = conn.execute(
        """
        SELECT 1 FROM institutional_fetch_log
        WHERE market = ? AND date = ?
          AND (
                status = ?
                OR (status = ? AND final_url IS NOT NULL AND trim(final_url, ' ' || char(9) || char(10) || char(13)) <> '')
              )
        """,
        (unit.market, unit.report_date.isoformat(), STATUS_EMPTY, STATUS_OK),
    ).fetchone()
    return row is not None


def load_trading_dates(parquet_path: Path = DEFAULT_PRICE_PARQUET_PATH, *, required: bool = False) -> frozenset[date] | None:
    """The dates the market actually opened, read from a price parquet.

    Two different contracts, because the two callers want opposite failures.

    The daily run takes whatever the canonical cache happens to hold, so a
    calendar it cannot read returns ``None`` and every caller reads that as "ask
    about every day": redundant requests, never silently fetching nothing and
    reporting success.

    ``required=True`` is for a caller that named a specific file -- a study
    pinning a frozen artifact so its acquisition matches the calendar it
    preregistered. Falling back to every calendar day there would quietly
    acquire against a different calendar than the one that was pinned, so it
    raises instead.

    ``pyarrow`` is imported here rather than at module scope so the fetcher
    stays importable without it.
    """
    try:
        import pyarrow.parquet as pq

        column = pq.read_table(parquet_path, columns=["Date"])["Date"]
        days = frozenset(value.date() for value in column.unique().to_pylist() if value is not None)
    except Exception as exc:
        if required:
            raise ValueError(f"could not read the trading calendar from {parquet_path}: {exc}") from exc
        logger.warning("could not read the trading calendar from %s (%s); every day will be requested", parquet_path, exc)
        return None
    if not days:
        if required:
            raise ValueError(f"the trading calendar in {parquet_path} is empty")
        logger.warning("the trading calendar in %s is empty; every day will be requested", parquet_path)
        return None
    return days


def build_units(
    start_date: date,
    end_date: date,
    markets: Iterable[str] = MARKETS,
    *,
    trading_dates: Collection[date] | None = None,
) -> list[FetchUnit]:
    """Ordered ``(market, date)`` work units for a closed date range.

    ``trading_dates``, when given, is the calendar the caller considers real:
    dates outside it produce no unit at all. Without it every calendar day is
    asked for, which is how weekends came to sit permanently at ``partial`` --
    the endpoints answer a Saturday with a successful empty body, so each daily
    run spent four requests re-asking a question that has no answer.

    Passing ``None`` keeps the every-day behaviour on purpose. A caller whose
    calendar failed to load is better off making redundant requests than
    silently fetching nothing and reporting success.
    """
    if start_date > end_date:
        raise ValueError("start_date cannot be after end_date")
    market_list = tuple(markets)
    unknown = set(market_list) - set(MARKETS)
    if unknown:
        raise ValueError(f"unknown markets: {sorted(unknown)}")
    units: list[FetchUnit] = []
    current = start_date
    while current <= end_date:
        if trading_dates is None or current in trading_dates:
            for market in market_list:
                floor = FIRST_TWSE_DATE if market == MARKET_TWSE else FIRST_TPEX_DATE
                if current >= floor:
                    units.append(FetchUnit(market, current))
        current += timedelta(days=1)
    return units


def _fetch_report(
    base_url: str,
    params: dict[str, Any],
    *,
    expected_host: str,
    retries: int,
    retry_sleep: float,
    max_retry_sleep: float,
) -> tuple[dict[str, Any], str, str]:
    """Fetch one report, returning the payload, the requested URL and the URL
    the response came back from.

    A transport failure carries no response, so the exception has no URL of its
    own and the error checkpoint recorded an empty ``source_url`` -- for the 520
    that is by far the most common failure, and the one the checkpoint exists to
    explain. The requested URL is known here, so attach it on the way out.
    """
    source_url = _url(base_url, params)
    try:
        payload, final_url = fetch_json_with_retry_url(
            base_url,
            params=params,
            retries=retries,
            retry_sleep=retry_sleep,
            max_retry_sleep=max_retry_sleep,
            expected_host=expected_host,
        )
    except Exception as exc:
        if not getattr(exc, "source_url", ""):
            exc.source_url = source_url  # type: ignore[attr-defined]
        raise
    return payload, source_url, final_url


def fetch_twse_report(
    report_date: date,
    *,
    retries: int,
    retry_sleep: float,
    max_retry_sleep: float = DEFAULT_BACKFILL_MAX_RETRY_SLEEP,
) -> tuple[dict[str, Any], str, str]:
    params = {
        "date": report_date.strftime("%Y%m%d"),
        "selectType": "ALLBUT0999",
        "response": "json",
    }
    return _fetch_report(
        TWSE_URL,
        params,
        expected_host=TWSE_HOST,
        retries=retries,
        retry_sleep=retry_sleep,
        max_retry_sleep=max_retry_sleep,
    )


def fetch_tpex_report(
    report_date: date,
    *,
    retries: int,
    retry_sleep: float,
    max_retry_sleep: float = DEFAULT_BACKFILL_MAX_RETRY_SLEEP,
) -> tuple[dict[str, Any], str, str]:
    params = {
        "type": "Daily",
        "sect": "EW",
        "date": report_date.strftime("%Y/%m/%d"),
        "id": "",
        "response": "json",
    }
    return _fetch_report(
        TPEX_URL,
        params,
        expected_host=TPEX_HOST,
        retries=retries,
        retry_sleep=retry_sleep,
        max_retry_sleep=max_retry_sleep,
    )


def fetch_unit(
    conn: sqlite3.Connection,
    unit: FetchUnit,
    *,
    raw_dir: Path,
    instrument_types: dict[str, str],
    retries: int,
    retry_sleep: float,
    retry_empty_since: date,
    artifact_root: Path,
    max_retry_sleep: float = DEFAULT_BACKFILL_MAX_RETRY_SLEEP,
    trading_dates: Collection[date] | None = None,
) -> int:
    fetch_fn = fetch_twse_report if unit.market == MARKET_TWSE else fetch_tpex_report
    normalize_fn = normalize_twse_payload if unit.market == MARKET_TWSE else normalize_tpex_payload
    payload, source_url, final_url = fetch_fn(
        unit.report_date,
        retries=retries,
        retry_sleep=retry_sleep,
        max_retry_sleep=max_retry_sleep,
    )
    fetched_at = _utc_now(microseconds=True)
    raw_path, sha256 = write_raw_snapshot(raw_dir, unit, payload, fetched_at)
    stored_raw_path = raw_path.resolve().relative_to(artifact_root).as_posix()
    try:
        rows, signature = normalize_fn(
            payload,
            unit.report_date,
            instrument_types=instrument_types,
            fetched_at=fetched_at,
        )
    except Exception as exc:
        raise ResponseNotUnderstoodError(
            f"{unit.market} {unit.report_date.isoformat()} response could not be parsed: {exc}",
            source_url=source_url,
            final_url=final_url,
            raw_path=stored_raw_path,
            sha256=sha256,
        ) from exc
    status = (
        _empty_response_status(
            unit.report_date,
            retry_empty_since=retry_empty_since,
            trading_dates=trading_dates,
        )
        if not rows
        else STATUS_OK
    )
    with conn:
        # Inside the transaction, not before it. Reading the row count outside
        # would let another writer land between the check and the delete, and
        # the delete would then run on a stale answer. Here SQLite has to
        # upgrade this transaction's read lock to write, which fails loudly if
        # anything committed in between instead of proceeding on stale state.
        if not rows:
            _refuse_to_erase_on_empty(
                conn,
                unit,
                source_url=source_url,
                final_url=final_url,
                raw_path=stored_raw_path,
                sha256=sha256,
            )
        conn.execute(
            "DELETE FROM institutional_daily WHERE market = ? AND date = ?",
            (unit.market, unit.report_date.isoformat()),
        )
        upsert_rows(conn, rows)
        conn.execute(
            """
            INSERT INTO institutional_fetch_log (
                market, date, source_url, status, row_count, schema_signature,
                published_at, fetched_at, raw_path, sha256, final_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(market, date) DO UPDATE SET
                source_url = excluded.source_url,
                final_url = excluded.final_url,
                status = excluded.status,
                row_count = excluded.row_count,
                schema_signature = excluded.schema_signature,
                published_at = excluded.published_at,
                fetched_at = excluded.fetched_at,
                raw_path = excluded.raw_path,
                sha256 = excluded.sha256,
                error_detail = NULL
            """,
            (
                unit.market,
                unit.report_date.isoformat(),
                source_url,
                status,
                len(rows),
                signature,
                None,
                fetched_at,
                stored_raw_path,
                sha256,
                final_url,
            ),
        )
    return len(rows)


class EmptyResponseWouldEraseDataError(RuntimeError):
    """A successful but empty response for a unit that already holds rows.

    Distinct from :class:`ResponseNotUnderstoodError`: this response parsed
    fine, it just carried nothing. Its own class so the checkpoint's
    ``error_detail`` says what actually happened.
    """

    def __init__(
        self,
        message: str,
        *,
        source_url: str,
        final_url: str,
        raw_path: str,
        sha256: str,
        stored_rows: int,
    ) -> None:
        super().__init__(message)
        self.source_url = source_url
        self.final_url = final_url
        self.raw_path = raw_path
        self.sha256 = sha256
        self.stored_rows = stored_rows


def _stored_row_count(conn: sqlite3.Connection, unit: FetchUnit) -> int:
    row = conn.execute(
        "SELECT count(*) FROM institutional_daily WHERE market = ? AND date = ?",
        (unit.market, unit.report_date.isoformat()),
    ).fetchone()
    return int(row[0]) if row else 0


def _has_populated_success(conn: sqlite3.Connection, unit: FetchUnit) -> bool:
    row = conn.execute(
        "SELECT 1 FROM institutional_fetch_log WHERE market = ? AND date = ? AND status = ? AND row_count > 0",
        (unit.market, unit.report_date.isoformat(), STATUS_OK),
    ).fetchone()
    return row is not None


def _refuse_to_erase_on_empty(
    conn: sqlite3.Connection,
    unit: FetchUnit,
    *,
    source_url: str,
    final_url: str,
    raw_path: str,
    sha256: str,
) -> None:
    """Stop before an empty response can delete rows that are already stored.

    The write below is a delete-then-insert, so a response that parsed to zero
    rows would remove every stored row for the unit and rewrite its checkpoint
    with ``row_count=0``. ``record_failure``'s guard does not cover this: no
    exception occurred, so that path never runs.

    These endpoints answer "not published yet" with a successful empty body,
    indistinguishable from a genuine one, which is the same ambiguity
    ``_empty_response_status`` exists for -- but one level worse, because here
    the ambiguity is resolved by destroying evidence rather than by settling a
    status. An acquisition tool must never do that on the strength of an empty
    body, so the unit is failed and left owed instead.

    The consequence, stated plainly: a day whose report is genuinely withdrawn
    can never be reduced to zero rows by this pipeline. That is deliberate. It
    needs a human decision and a separate deliberate action, not a silent
    delete during a routine re-fetch.
    """
    stored_rows = _stored_row_count(conn, unit)
    if not stored_rows and not _has_populated_success(conn, unit):
        return
    raise EmptyResponseWouldEraseDataError(
        f"{unit.market} {unit.report_date.isoformat()} answered with no rows while {stored_rows} "
        f"row(s) are already stored; refusing to erase them. The response was kept at {raw_path} "
        f"(sha256 {sha256}) for diagnosis.",
        source_url=source_url,
        final_url=final_url,
        raw_path=raw_path,
        sha256=sha256,
        stored_rows=stored_rows,
    )


class ResponseNotUnderstoodError(RuntimeError):
    """A response that was stored but could not be parsed.

    The snapshot is written before parsing, so a fail-closed unknown schema
    leaves the one artifact needed to diagnose it on disk with nothing pointing
    at it. Carrying the pointer means the error checkpoint can cite the file.
    """

    def __init__(self, message: str, *, source_url: str, final_url: str, raw_path: str, sha256: str) -> None:
        super().__init__(message)
        self.source_url = source_url
        self.final_url = final_url
        self.raw_path = raw_path
        self.sha256 = sha256


def _empty_response_status(
    report_date: date,
    *,
    retry_empty_since: date,
    trading_dates: Collection[date] | None,
) -> str:
    """Classify a successful response that carried no rows.

    These endpoints answer "not published yet" with a successful empty body,
    indistinguishable from a day that genuinely has no report. Settling that as
    ``ok`` -- which is what a historical backfill used to do, because
    ``retry_empty_since`` defaults to today -- strands the day permanently on
    whatever else wrote it, and records a fetch that returned nothing as a
    success. When the caller supplies the trading calendar the two cases separate
    exactly; without it, only recent dates stay retryable.
    """
    if trading_dates is not None:
        return STATUS_PARTIAL if report_date in trading_dates else STATUS_EMPTY
    return STATUS_PARTIAL if report_date >= retry_empty_since else STATUS_EMPTY


def record_failure(conn: sqlite3.Connection, unit: FetchUnit, error: BaseException) -> None:
    """Persist why a unit could not be fetched.

    Nothing used to be written at all, so a failed run left no evidence beyond
    a log line: a rate-limited 520 and a read timeout were indistinguishable
    afterwards, and diagnosing a stalled backfill meant reproducing it. The
    ``WHERE`` clause keeps this from ever demoting a checkpoint that already
    holds data.
    """
    status_code = getattr(error, "status_code", None)
    detail = f"{type(error).__name__}: {error}"[:500]
    source_url = str(getattr(error, "source_url", "") or getattr(error, "requested_url", "") or "")
    final_url = str(getattr(error, "final_url", "") or "")
    raw_path = str(getattr(error, "raw_path", "") or "")
    sha256 = str(getattr(error, "sha256", "") or "")
    with conn:
        conn.execute(
            """
            INSERT INTO institutional_fetch_log (
                market, date, source_url, status, row_count, schema_signature,
                published_at, fetched_at, raw_path, sha256, final_url, error_detail
            ) VALUES (?, ?, ?, ?, 0, ?, NULL, ?, ?, ?, ?, ?)
            ON CONFLICT(market, date) DO UPDATE SET
                source_url = excluded.source_url,
                final_url = excluded.final_url,
                status = excluded.status,
                row_count = excluded.row_count,
                schema_signature = excluded.schema_signature,
                fetched_at = excluded.fetched_at,
                raw_path = excluded.raw_path,
                sha256 = excluded.sha256,
                error_detail = excluded.error_detail
            WHERE institutional_fetch_log.status NOT IN (?, ?)
            """,
            (
                unit.market,
                unit.report_date.isoformat(),
                source_url,
                STATUS_ERROR,
                f"error-{status_code}" if status_code is not None else "error",
                _utc_now(microseconds=True),
                raw_path,
                sha256,
                final_url,
                detail,
                *SETTLED_STATUSES,
            ),
        )


def write_raw_snapshot(raw_dir: Path, unit: FetchUnit, payload: dict[str, Any], fetched_at: str) -> tuple[Path, str]:
    market_dir = raw_dir / unit.market.lower()
    market_dir.mkdir(parents=True, exist_ok=True)
    stamp = fetched_at.replace("-", "").replace(":", "").replace("+00:00", "Z").replace(".", "")
    raw_path = market_dir / f"{unit.report_date.isoformat()}_{stamp}.json"
    raw_bytes = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    raw_path.write_bytes(raw_bytes)
    return raw_path, hashlib.sha256(raw_bytes).hexdigest()


def run_collection(
    *,
    start_date: date,
    end_date: date,
    markets: Iterable[str] = MARKETS,
    db_path: Path = DEFAULT_DB_PATH,
    raw_dir: Path = DEFAULT_RAW_DIR,
    symbol_meta_path: Path = DEFAULT_SYMBOL_META_PATH,
    throttle_seconds: float = DEFAULT_THROTTLE_SECONDS,
    retries: int = 3,
    retry_sleep: float = 5.0,
    max_retry_sleep: float = DEFAULT_BACKFILL_MAX_RETRY_SLEEP,
    max_requests: int | None = None,
    max_seconds: float | None = None,
    force: bool = False,
    today: date | None = None,
    retry_empty_since: date | None = None,
    trading_dates: Collection[date] | None = None,
    cooldown_seconds: float = 0.0,
    cooldown_after_failures: int = DEFAULT_COOLDOWN_AFTER_FAILURES,
    stop_after_consecutive_failures: int | None = None,
) -> dict[str, int]:
    today = today or date.today()
    retry_empty_since = retry_empty_since or today
    artifact_root = db_path.resolve().parent
    try:
        raw_dir.resolve().relative_to(artifact_root)
    except ValueError as exc:
        raise ValueError("raw_dir must be inside the institutional database directory") from exc
    units = build_units(start_date, end_date, markets, trading_dates=trading_dates)
    init_db(db_path)
    instrument_types = load_instrument_types(symbol_meta_path)
    stats = {
        "attempted": 0,
        "fetched": 0,
        "skipped_cached": 0,
        "failed": 0,
        "rows": 0,
        "refused_to_erase": 0,
        "stopped_early": 0,
    }
    started = time.monotonic()
    # Per market, not per run. Units are interleaved TWSE-then-TPEx within each
    # date, so one global counter let a healthy TWSE reset TPEx's streak on
    # every single date: TPEx could fail on hundreds of consecutive dates and
    # never reach the stop threshold, which is exactly the shape the 2019-2023
    # backfill hit.
    consecutive_failures = dict.fromkeys(tuple(markets), 0)
    with connect(db_path) as conn:
        for unit in units:
            if max_requests is not None and stats["attempted"] >= max_requests:
                break
            if max_seconds is not None and time.monotonic() - started >= max_seconds:
                break
            if not force and is_unit_cached(conn, unit):
                stats["skipped_cached"] += 1
                continue
            if stats["attempted"] and throttle_seconds > 0:
                time.sleep(throttle_seconds)
            stats["attempted"] += 1
            try:
                row_count = fetch_unit(
                    conn,
                    unit,
                    raw_dir=raw_dir,
                    instrument_types=instrument_types,
                    retries=retries,
                    retry_sleep=retry_sleep,
                    retry_empty_since=retry_empty_since,
                    artifact_root=artifact_root,
                    max_retry_sleep=max_retry_sleep,
                    trading_dates=trading_dates,
                )
                stats["fetched"] += 1
                stats["rows"] += row_count
                consecutive_failures[unit.market] = 0
                logger.info(
                    "fetched institutional %s %s: %d rows",
                    unit.market,
                    unit.report_date,
                    row_count,
                )
            except Exception as exc:
                stats["failed"] += 1
                if isinstance(exc, EmptyResponseWouldEraseDataError):
                    # Counted separately so a run that protected data does not
                    # read like a run that lost a request to a 520.
                    stats["refused_to_erase"] += 1
                streak = consecutive_failures[unit.market] + 1
                consecutive_failures[unit.market] = streak
                record_failure(conn, unit, exc)
                logger.exception(
                    "failed institutional fetch %s %s (status=%s)",
                    unit.market,
                    unit.report_date,
                    getattr(exc, "status_code", None),
                )
                if stop_after_consecutive_failures is not None and streak >= stop_after_consecutive_failures:
                    stats["stopped_early"] = 1
                    logger.error(
                        "stopping institutional collection after %d consecutive %s failures; settled units are checkpointed and the run can resume later",
                        streak,
                        unit.market,
                    )
                    break
                if cooldown_seconds > 0 and streak >= cooldown_after_failures:
                    logger.warning(
                        "cooling down %.0fs after %d consecutive %s institutional failures",
                        cooldown_seconds,
                        streak,
                        unit.market,
                    )
                    time.sleep(cooldown_seconds)
    return stats


class _Unset:
    """Distinguishes "caller said None" from "caller said nothing"."""


_UNSET = _Unset()


def _daily_window(start_date: date, end_date: date, trading_dates: Collection[date] | None) -> frozenset[date] | None:
    """Which days of a rolling window are worth a request.

    Weekends answer with a successful empty body, so before the calendar was
    consulted they sat at ``partial`` and cost four requests every single run,
    for ever. Days newer than anything the calendar knows about are kept
    regardless: the price cache is rebuilt earlier in the same pipeline run and
    can lag, and a stale calendar must not be able to suppress today.
    """
    if trading_dates is None:
        return None
    known = frozenset(trading_dates)
    if not known:
        return None
    newest = max(known)
    window = {start_date + timedelta(days=offset) for offset in range((end_date - start_date).days + 1)}
    return frozenset(day for day in window if day in known or day > newest)


def run_daily_fetch(
    *,
    target_date: date | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    raw_dir: Path = DEFAULT_RAW_DIR,
    symbol_meta_path: Path = DEFAULT_SYMBOL_META_PATH,
    throttle_seconds: float = DEFAULT_THROTTLE_SECONDS,
    retries: int = 3,
    retry_sleep: float = 5.0,
    max_retry_sleep: float = DEFAULT_BACKFILL_MAX_RETRY_SLEEP,
    lookback_days: int = DEFAULT_DAILY_LOOKBACK_DAYS,
    trading_dates: Collection[date] | None | _Unset = _UNSET,
) -> dict[str, int]:
    target_date = target_date or date.today()
    if lookback_days < 1:
        raise ValueError("lookback_days must be at least 1")
    start_date = target_date - timedelta(days=lookback_days - 1)
    if isinstance(trading_dates, _Unset):
        trading_dates = load_trading_dates()
    window = _daily_window(start_date, target_date, trading_dates)
    units = build_units(start_date, target_date, trading_dates=window)
    stats = run_collection(
        start_date=start_date,
        end_date=target_date,
        db_path=db_path,
        raw_dir=raw_dir,
        symbol_meta_path=symbol_meta_path,
        throttle_seconds=throttle_seconds,
        retries=retries,
        retry_sleep=retry_sleep,
        max_retry_sleep=max_retry_sleep,
        max_requests=len(units),
        today=target_date,
        retry_empty_since=start_date,
        trading_dates=window,
    )
    if stats["failed"]:
        raise RuntimeError(f"institutional fetch failed for {stats['failed']} market/date unit(s)")
    return stats


def _url(base: str, params: dict[str, Any]) -> str:
    return f"{base}?{urlencode(params)}"


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from exc


def _backfill_calendar(*, all_calendar_days: bool, trading_calendar_path: Path | None) -> Collection[date] | None:
    """Which calendar a backfill run should walk.

    The two flags are mutually exclusive at the parser, so only one of them is
    ever set here. A named path is required to load; the default is allowed to
    degrade to every calendar day.
    """
    if all_calendar_days:
        return None
    if trading_calendar_path is not None:
        return load_trading_dates(trading_calendar_path, required=True)
    return load_trading_dates()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch official TWSE/TPEx daily institutional flows.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    daily = subparsers.add_parser(
        "daily",
        help="Refresh a rolling window for both markets (ending today by default).",
    )
    daily.add_argument("--date", type=_parse_iso_date, default=None)
    daily.add_argument("--lookback-days", type=int, default=DEFAULT_DAILY_LOOKBACK_DAYS)
    backfill = subparsers.add_parser("backfill", help="Fetch an inclusive historical date range.")
    backfill.add_argument("--start-date", type=_parse_iso_date, required=True)
    backfill.add_argument("--end-date", type=_parse_iso_date, required=True)
    backfill.add_argument("--markets", nargs="+", choices=MARKETS, default=list(MARKETS))
    backfill.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    backfill.add_argument("--max-seconds", type=float, default=None)
    backfill.add_argument("--force", action="store_true")
    # Two opposite intents about the same thing, so let the parser reject the
    # pair rather than have one of them silently win.
    calendar_source = backfill.add_mutually_exclusive_group()
    calendar_source.add_argument(
        "--all-calendar-days",
        action="store_true",
        help="Request every calendar day instead of only the trading days in the price cache.",
    )
    calendar_source.add_argument(
        "--trading-calendar-path",
        type=Path,
        default=None,
        help=(
            "Read the trading calendar from this parquet instead of the canonical price cache. "
            "Unlike the default, an unreadable or empty file here is an error, not a fallback: "
            "a caller that names a specific artifact wants that calendar or none."
        ),
    )
    backfill.add_argument("--retries", type=int, default=DEFAULT_BACKFILL_RETRIES)
    backfill.add_argument("--retry-sleep", type=float, default=DEFAULT_BACKFILL_RETRY_SLEEP)
    backfill.add_argument("--cooldown-seconds", type=float, default=DEFAULT_COOLDOWN_SECONDS)
    backfill.add_argument(
        "--stop-after-consecutive-failures",
        type=int,
        default=DEFAULT_STOP_AFTER_CONSECUTIVE_FAILURES,
    )
    daily.add_argument("--retries", type=int, default=3)
    daily.add_argument("--retry-sleep", type=float, default=5.0)
    for command in (daily, backfill):
        command.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
        command.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
        command.add_argument("--symbol-meta-path", type=Path, default=DEFAULT_SYMBOL_META_PATH)
        command.add_argument("--throttle-seconds", type=float, default=DEFAULT_THROTTLE_SECONDS)
        command.add_argument("--max-retry-sleep", type=float, default=DEFAULT_BACKFILL_MAX_RETRY_SLEEP)
    args = parser.parse_args()
    common = {
        "db_path": args.db_path,
        "raw_dir": args.raw_dir,
        "symbol_meta_path": args.symbol_meta_path,
        "throttle_seconds": args.throttle_seconds,
        "retries": args.retries,
        "retry_sleep": args.retry_sleep,
        "max_retry_sleep": args.max_retry_sleep,
    }
    if args.command == "daily":
        stats = run_daily_fetch(target_date=args.date, lookback_days=args.lookback_days, **common)
    else:
        stats = run_collection(
            start_date=args.start_date,
            end_date=args.end_date,
            markets=args.markets,
            trading_dates=_backfill_calendar(
                all_calendar_days=args.all_calendar_days,
                trading_calendar_path=args.trading_calendar_path,
            ),
            max_requests=args.max_requests,
            max_seconds=args.max_seconds,
            force=args.force,
            cooldown_seconds=args.cooldown_seconds,
            stop_after_consecutive_failures=args.stop_after_consecutive_failures,
            **common,
        )
        if stats["failed"]:
            raise SystemExit(1)
    logger.info("institutional_fetch_stats: %s", stats)


if __name__ == "__main__":
    setup_logging("fetch_institutional.log")
    main()
