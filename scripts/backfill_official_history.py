"""Backfill official daily-price history for past periods, gradually ("slowly
crawl it down") without tripping either endpoint's soft rate limit.

The two markets publish history at different granularities, so a unit of work
means different things:

- TWSE MI_INDEX answers for a whole market day, with no code; STOCK_DAY
  additionally answers per symbol and month, so a unit is (code, YYYY-MM).
- TPEx answers for the whole market on one day, so a unit is (YYYY-MM-DD) with
  no code -- one request covers every OTC instrument that day.

TWSE day candidates combine observed market dates with recent weekdays, placing
missing TWSE dates first. TPEx candidates come from observed TWSE gaps and TPEx
days without settled history checkpoints; they are recomputed after TWSE days
commit so one invocation can recover both markets.

Writes into the same `official_daily_price` table as the bulk fetcher (same row
shape). TWSE MI_INDEX inserts only missing keys and retains existing provenance;
symbol-month and TPEx writes retain the existing idempotent upsert. One caveat:
TPEx history counts the round-lot session only, while the nightly bulk fetch
includes odd-lot trading, so a backfilled TPEx volume reads a few percent below a
nightly one for the same symbol and day. Both are in shares.

Progress is checkpointed per (market, code, period) in
`official_history_raw_ranges` so an interrupted run resumes without re-fetching
completed units.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from fetch_official_daily_price import DEFAULT_DB_PATH, MARKET_TPEX, MARKET_TWSE, connect, init_db, insert_rows, upsert_rows

from config import config
from shp_utils.logging_utils import setup_logging
from shp_utils.official_api import fetch_json_with_retry, parse_int, parse_number
from shp_utils.twse_market_day import TWSE_EARLIEST_DATE, fetch_twse_market_day, normalize_twse_market_day_payload

logger = logging.getLogger(__name__)

TWSE_HISTORY_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"

# TPEx publishes dated history on its own site rather than in its OpenAPI catalogue,
# whole-market for one day at a time. `type=AL` is the filter that returns every
# instrument; without any `type` the response is a successful empty, not an error.
# See docs/tpex-history-sources.md for the probe evidence behind both facts.
TPEX_HISTORY_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc"
TPEX_ALL_SECURITIES = "AL"

# The endpoint's own floor: 2007-07-01 per its date picker, with 2007-07-02 the first
# trading day that returns rows. Nothing earlier is obtainable here.
TPEX_EARLIEST_DATE = "2007-07-02"

# Every known response schema, as a normalized field-name signature. Rows are read by
# field name rather than by position, so a mapping table per era is unnecessary --
# these exist to recognize a schema, and to fail loudly on one that is none of them.
#
# The two 17-field eras differ only in how they label a column this parser never
# reads. That is exactly why the third era was missed at first: the research probes
# recorded field *counts*, and a count cannot tell two eras apart. Boundaries below
# are the observed ones, from a crawl that failed on every date in the middle era.
TPEX_FIELD_SIGNATURES: dict[tuple[str, ...], str] = {
    (
        "代號",
        "名稱",
        "收盤",
        "漲跌",
        "開盤",
        "最高",
        "最低",
        "成交股數",
        "成交金額(元)",
        "成交筆數",
        "最後買價",
        "最後賣價",
        "發行股數",
        "次日漲停價",
        "次日跌停價",
    ): "15-field, through 2020-04-29",
    (
        "代號",
        "名稱",
        "收盤",
        "漲跌",
        "開盤",
        "最高",
        "最低",
        "成交股數",
        "成交金額(元)",
        "成交筆數",
        "最後買價",
        "最後買量(千股)",
        "最後賣價",
        "最後賣量(千股)",
        "發行股數",
        "次日漲停價",
        "次日跌停價",
    ): "17-field, 2020-04-30 through 2025-01-09",
    (
        "代號",
        "名稱",
        "收盤",
        "漲跌",
        "開盤",
        "最高",
        "最低",
        "成交股數",
        "成交金額(元)",
        "成交筆數",
        "最後買價",
        "最後買量(張數)",
        "最後賣價",
        "最後賣量(張數)",
        "發行股數",
        "次日漲停價",
        "次日跌停價",
    ): "17-field, 2025-01-10 onward",
}

# Lowered from 2.5s on 2026-08-08 to increase crawl throughput (observed 0
# failures/hour at 2.5s across many hourly runs -- see
# logs/backfill_official_history_*.log -- suggesting some headroom existed).
# If a future run's logs show a sustained rise in `failed` counts, or
# `mark_unit_failed` backoff-sleep log lines recurring across units, TWSE's
# soft rate limit is likely being tripped: revert this constant to 2.5 (or
# pass --throttle-seconds 2.5 to a specific invocation) rather than pushing
# it lower.
DEFAULT_THROTTLE_SECONDS = 2.0
MAX_BACKOFF_MULTIPLIER = 5

# A hard kill (OS reboot, Task Scheduler stop) leaves nothing in the log beyond
# whatever was written before the process died -- these heartbeats bound how much
# progress can go unlogged, so a run diagnosed after the fact ("how far did it get
# before it was killed?") doesn't require querying official_history_raw_ranges.
HEARTBEAT_INTERVAL_SECONDS = 300.0

# The TWSE STOCK_DAY endpoint rejects any query before this month (verified live:
# 2009-12 returns "查詢日期小於99年1月4日" / 0 rows; 2010-01 returns "OK"). No earlier
# STOCK_DAY history is obtainable there. MI_INDEX repair is separately scoped to
# 2010-01-04 onward; this does not claim earlier whole-market data is unavailable.
EARLIEST_SUPPORTED_YEAR_MONTH = "2010-01"

# The no-argument auto mode must default to a bounded slice, not an unbounded crawl
# of the full ~271,000-request floor-to-now backlog.
DEFAULT_AUTO_MAX_SECONDS = 3600.0

# Not a data-integrity concern (the sqlite upsert is already idempotent under
# concurrent writers, see fetch_official_daily_price.connect()'s WAL/busy_timeout) --
# this guards against two backfills doubling the request rate and risking a TWSE
# IP block. run_daily.py's own fetch deliberately does not check this lock.
DEFAULT_LOCK_PATH = config.DATA_DIR / ".official_backfill.lock"
_ORIGINAL_DEFAULT_LOCK_PATH = DEFAULT_LOCK_PATH


class BackfillAlreadyRunningError(RuntimeError):
    """Raised when another backfill process already holds the advisory lock."""


def acquire_lock(lock_path: Path = DEFAULT_LOCK_PATH) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.is_file():
        existing_pid = None
        try:
            existing_pid = json.loads(lock_path.read_text(encoding="utf-8")).get("pid")
        except (json.JSONDecodeError, OSError, ValueError):
            pass
        if existing_pid is not None and _pid_is_running(existing_pid):
            raise BackfillAlreadyRunningError(
                f"another backfill appears to be running (pid={existing_pid}, lock={lock_path}); "
                f"if no backfill is actually running (e.g. the pid was reused by an unrelated "
                f"process), delete this lock file and re-run"
            )
        logger.warning("reclaiming stale backfill lock at %s (pid %s not running)", lock_path, existing_pid)
    lock_path.write_text(
        json.dumps({"pid": os.getpid(), "started_at": datetime.now(UTC).isoformat(timespec="seconds")}),
        encoding="utf-8",
    )


def release_lock(lock_path: Path = DEFAULT_LOCK_PATH) -> None:
    lock_path.unlink(missing_ok=True)


def _pid_is_running(pid: int) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_is_running(pid)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_pid_is_running(pid: int) -> bool:
    """Query a Windows process without sending it a console control event."""
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    error_access_denied = 5
    still_active = 259

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    get_exit_code_process = kernel32.GetExitCodeProcess
    get_exit_code_process.argtypes = [wintypes.HANDLE, wintypes.LPDWORD]
    get_exit_code_process.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = open_process(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied

    try:
        exit_code = wintypes.DWORD()
        if not get_exit_code_process(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        close_handle(handle)


@dataclass(frozen=True)
class CrawlUnit:
    """One unit of crawlable history.

    The two markets publish history at different granularities, so a unit means
    different things per market and `period` holds whichever applies:

    - TWSE answers per symbol and month: code="2330", period="2026-07".
    - TWSE MI_INDEX answers for a whole day: code="", period="2026-07-01".
    - TPEx answers for the whole market on one day: code="", period="2026-07-01".

    An empty `code` is what marks a whole-market unit. No real code is empty, so it
    cannot collide, and the checkpoint's primary key keeps working unchanged.
    """

    market: str
    code: str
    period: str


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill official TWSE daily-price history. "
        "Omit --codes/--start-year-month/--end-year-month entirely to run the "
        "no-argument auto mode: crawls every TWSE code the official artifact already "
        "knows about, newest month first, down to the endpoint's earliest supported "
        "date, resumable across runs via its checkpoint."
    )
    parser.add_argument("--codes", nargs="+", default=None)
    parser.add_argument("--start-year-month", default=None, help="YYYY-MM")
    parser.add_argument("--end-year-month", default=None, help="YYYY-MM")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--throttle-seconds", type=float, default=DEFAULT_THROTTLE_SECONDS)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-sleep", type=float, default=5.0)
    parser.add_argument("--max-requests", type=int, default=None)
    parser.add_argument("--max-seconds", type=float, default=None)
    parser.add_argument("--max-consecutive-failures", type=int, default=None, help="Stop after this many consecutive failed units (positive integer).")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--twse-dates", nargs="+", default=None, help="Explicit TWSE market days (YYYY-MM-DD); alone selects day-only repair.")
    parser.add_argument(
        "--tpex-dates",
        nargs="+",
        default=None,
        help="Explicit TPEx trading days (YYYY-MM-DD) to backfill. Omit in auto mode to "
        "crawl every day TWSE history proves was a trading day and TPEx has no rows for.",
    )
    args = parser.parse_args()
    logger.info("data directory: %s (%s)", config.DATA_DIR, config.DATA_DIR_SOURCE)
    if args.max_consecutive_failures is not None and args.max_consecutive_failures <= 0:
        parser.error("--max-consecutive-failures must be a positive integer")

    explicit_args_given = [args.codes is not None, args.start_year_month is not None, args.end_year_month is not None]
    if any(explicit_args_given) and not all(explicit_args_given):
        parser.error("--codes, --start-year-month, and --end-year-month must all be given together, or all omitted for auto mode")

    auto_mode = not any(explicit_args_given) and args.twse_dates is None and args.tpex_dates is None
    if auto_mode:
        codes, start_year_month, end_year_month = resolve_auto_crawl_plan(args.db_path)
        tpex_dates = args.tpex_dates if args.tpex_dates is not None else load_tpex_dates_to_fetch(args.db_path)
        twse_dates = load_twse_dates_to_fetch(args.db_path)
        logger.info("auto plan: %d TWSE codes, %d TWSE day(s), %d TPEx day(s)", len(codes), len(twse_dates), len(tpex_dates))
        newest_first = True
        max_seconds = resolve_auto_budget(args.max_requests, args.max_seconds)
    else:
        codes = args.codes or []
        start_year_month = args.start_year_month or EARLIEST_SUPPORTED_YEAR_MONTH
        end_year_month = args.end_year_month or start_year_month
        tpex_dates = args.tpex_dates or []
        twse_dates = args.twse_dates or []
        newest_first = False
        max_seconds = args.max_seconds if any(explicit_args_given) else resolve_auto_budget(args.max_requests, args.max_seconds)

    stats = run_backfill(
        codes=codes,
        start_year_month=start_year_month,
        end_year_month=end_year_month,
        db_path=args.db_path,
        throttle_seconds=args.throttle_seconds,
        retries=args.retries,
        retry_sleep=args.retry_sleep,
        max_requests=args.max_requests,
        max_seconds=max_seconds,
        max_consecutive_failures=args.max_consecutive_failures,
        force=args.force,
        newest_first=newest_first,
        tpex_dates=tpex_dates,
        twse_dates=twse_dates,
        auto_tpex_dates=auto_mode and args.tpex_dates is None,
    )
    logger.info("backfill_stats: %s", stats)


def run_backfill(
    *,
    codes: list[str],
    start_year_month: str,
    end_year_month: str,
    db_path: Path = DEFAULT_DB_PATH,
    throttle_seconds: float = DEFAULT_THROTTLE_SECONDS,
    retries: int = 3,
    retry_sleep: float = 5.0,
    max_requests: int | None = None,
    max_seconds: float | None = None,
    max_consecutive_failures: int | None = None,
    force: bool = False,
    today: date | None = None,
    lock_path: Path | None = None,
    newest_first: bool = False,
    tpex_dates: list[str] | None = None,
    twse_dates: list[str] | None = None,
    auto_tpex_dates: bool = False,
) -> dict[str, int]:
    if max_consecutive_failures is not None and max_consecutive_failures <= 0:
        raise ValueError("max_consecutive_failures must be a positive integer")
    # Resolved at call time (not bound as a parameter default) so tests can isolate
    # the lock location by monkeypatching the module-level constant, without every
    # call site needing to pass one explicitly.
    if lock_path is None:
        lock_path = DEFAULT_LOCK_PATH if DEFAULT_LOCK_PATH != _ORIGINAL_DEFAULT_LOCK_PATH else db_path.parent / ".official_backfill.lock"
    acquire_lock(lock_path)
    try:
        init_db(db_path)
        init_history_raw_ranges_table(db_path)
        twse_units = build_twse_day_crawl_units(twse_dates or [])
        units = twse_units + build_tpex_crawl_units(tpex_dates or [])
        month_units = build_crawl_units(codes, start_year_month, end_year_month, newest_first=newest_first)
        units += month_units
        stats = {"fetched": 0, "skipped_cached": 0, "failed": 0}
        consecutive_failures = 0
        start_time = time.monotonic()
        last_heartbeat = start_time
        stop_reason = "units exhausted"
        logger.info(
            "backfill run starting: pid=%d units=%d throttle=%.1fs budget=%s",
            os.getpid(),
            len(units),
            throttle_seconds,
            f"{max_seconds:.0f}s" if max_seconds is not None else "unbounded",
        )

        with connect(db_path) as conn:
            # The primary key is (market, code, date), so market/date lookups would
            # repeatedly scan the market's history. Load dates once for this run.
            existing_twse_dates = (
                {row[0] for row in conn.execute("SELECT DISTINCT date FROM official_daily_price WHERE market = ?", (MARKET_TWSE,))} if twse_units else set()
            )

            def planned_units() -> Iterator[CrawlUnit]:
                yield from twse_units
                selected_tpex_dates = load_tpex_dates_to_fetch(db_path, today=today) if auto_tpex_dates else (tpex_dates or [])
                yield from build_tpex_crawl_units(selected_tpex_dates)
                yield from month_units

            try:
                for unit in planned_units():
                    if max_requests is not None and stats["fetched"] >= max_requests:
                        stop_reason = "max_requests budget reached"
                        break
                    if max_seconds is not None and time.monotonic() - start_time >= max_seconds:
                        stop_reason = "max_seconds budget reached"
                        break
                    if not force and is_unit_cached(conn, unit):
                        stats["skipped_cached"] += 1
                        continue

                    sleep_seconds = throttle_seconds
                    try:
                        if unit.market == MARKET_TWSE and not unit.code:
                            already_had_rows = unit.period in existing_twse_dates
                            payload = fetch_twse_market_day(unit.period, retries=retries, retry_sleep=retry_sleep)
                            rows = normalize_twse_market_day_payload(unit.period, payload)
                            recent = date.fromisoformat(unit.period) >= (today or date.today()) - timedelta(days=7)
                            complete = bool(rows) or (not already_had_rows and not recent)
                            inserted = insert_rows(conn, rows)
                            logger.info("TWSE day %s returned=%d inserted=%d", unit.period, len(rows), inserted)
                        elif unit.market == MARKET_TPEX:
                            payload = fetch_tpex_market_day(unit.period, retries=retries, retry_sleep=retry_sleep)
                            rows = normalize_tpex_market_day_payload(unit.period, payload)
                            # An empty response cannot distinguish an unpublished day
                            # from a genuine empty, even after a long interruption.
                            complete = bool(rows)
                            logger.info("TPEx day %s returned=%d complete=%s", unit.period, len(rows), complete)
                        else:
                            payload = fetch_twse_symbol_month(unit.code, unit.period, retries=retries, retry_sleep=retry_sleep)
                            rows = normalize_twse_history_payload(unit.code, payload)
                            complete = is_month_complete(unit.period, today=today)
                        if unit.market != MARKET_TWSE or unit.code:
                            upsert_rows(conn, rows)
                        mark_unit_fetched(conn, unit, row_count=len(rows), complete=complete)
                        conn.commit()
                        if unit.market == MARKET_TWSE and not unit.code and rows:
                            existing_twse_dates.add(unit.period)
                        stats["fetched"] += 1
                        consecutive_failures = 0
                    except Exception:
                        logger.exception("failed to fetch %s %s %s history", unit.market, unit.code or "(whole market)", unit.period)
                        conn.rollback()
                        mark_unit_failed(conn, unit)
                        conn.commit()
                        stats["failed"] += 1
                        consecutive_failures += 1
                        if max_consecutive_failures is not None and consecutive_failures >= max_consecutive_failures:
                            stop_reason = f"max_consecutive_failures reached ({consecutive_failures})"
                            break
                        sleep_seconds = throttle_seconds * (2 ** min(consecutive_failures, MAX_BACKOFF_MULTIPLIER))

                    now = time.monotonic()
                    if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                        logger.info(
                            "backfill progress: %s (elapsed=%.0fs, last=%s %s %s)",
                            stats,
                            now - start_time,
                            unit.market,
                            unit.code or "(whole market)",
                            unit.period,
                        )
                        last_heartbeat = now

                    if max_seconds is not None:
                        sleep_seconds = min(sleep_seconds, max(0.0, max_seconds - (time.monotonic() - start_time)))
                    if sleep_seconds > 0:
                        time.sleep(sleep_seconds)
                logger.info("backfill run finished: reason=%s elapsed=%.0fs stats=%s", stop_reason, time.monotonic() - start_time, stats)
            except KeyboardInterrupt:
                # Each unit commits before the next starts, so the checkpoint table
                # already reflects an accurate resume point -- re-running the same
                # command continues from here. Caught here (not left to propagate) so
                # an operator sees a clean summary instead of a traceback.
                logger.warning(
                    "backfill interrupted after %d fetched / %d skipped / %d failed; re-run the same command to resume",
                    stats["fetched"],
                    stats["skipped_cached"],
                    stats["failed"],
                )

        return stats
    finally:
        release_lock(lock_path)


def build_crawl_units(codes: list[str], start_year_month: str, end_year_month: str, *, newest_first: bool = False) -> list[CrawlUnit]:
    months = [ym for ym in month_range(start_year_month, end_year_month) if ym >= EARLIEST_SUPPORTED_YEAR_MONTH]
    if newest_first:
        # Month-major (not code-major) so one unchanging command naturally finishes
        # every code's newest month before it touches an older one.
        return [CrawlUnit(market=MARKET_TWSE, code=code, period=month) for month in reversed(months) for code in codes]
    return [CrawlUnit(market=MARKET_TWSE, code=code, period=month) for code in codes for month in months]


def build_tpex_crawl_units(dates: list[str]) -> list[CrawlUnit]:
    """One unit per TPEx trading day. A whole-market unit carries no code."""
    return [CrawlUnit(market=MARKET_TPEX, code="", period=day) for day in dates if day >= TPEX_EARLIEST_DATE]


def build_twse_day_crawl_units(dates: list[str]) -> list[CrawlUnit]:
    units = []
    for day in dict.fromkeys(dates):
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError(f"invalid TWSE day: {day}")
        if day < TWSE_EARLIEST_DATE:
            raise ValueError(f"unsupported TWSE day: {day}")
        units.append(CrawlUnit(MARKET_TWSE, "", day))
    return units


def load_twse_dates_to_fetch(db_path: Path, *, today: date | None = None) -> list[str]:
    """Observed, retryable and recent days, missing TWSE first then newest first."""
    today = today or date.today()
    candidates = {(today - timedelta(days=offset)).isoformat() for offset in range(45) if (today - timedelta(days=offset)).weekday() < 5}
    existing: set[str] = set()
    settled: set[str] = set()
    if db_path.is_file():
        with connect(db_path) as conn:
            candidates.update(
                row[0] for row in conn.execute("SELECT DISTINCT date FROM official_daily_price WHERE market IN (?, ?)", (MARKET_TWSE, MARKET_TPEX))
            )
            existing = {row[0] for row in conn.execute("SELECT DISTINCT date FROM official_daily_price WHERE market = ?", (MARKET_TWSE,))}
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'official_history_raw_ranges' AND type = 'table'").fetchone():
                columns = {row[1] for row in conn.execute("PRAGMA table_info(official_history_raw_ranges)")}
                period_column = "period" if "period" in columns else "year_month"
                for (period,) in conn.execute(
                    f"SELECT {period_column} FROM official_history_raw_ranges WHERE market = ? AND code = '' AND status IN ('partial', 'error')", (MARKET_TWSE,)
                ):
                    try:
                        if date.fromisoformat(period).isoformat() == period:
                            candidates.add(period)
                    except (TypeError, ValueError):
                        continue
                settled = {
                    row[0]
                    for row in conn.execute(
                        f"SELECT {period_column} FROM official_history_raw_ranges WHERE market = ? AND code = '' AND status IN ('ok', 'empty')", (MARKET_TWSE,)
                    )
                }
    eligible = [day for day in candidates - settled if TWSE_EARLIEST_DATE <= day <= today.isoformat()]
    return sorted(eligible, key=lambda day: (day not in existing, day), reverse=True)


def load_twse_backfill_universe(db_path: Path) -> list[str]:
    """Established symbol checkpoints plus codes on the newest TWSE day."""
    if not db_path.is_file():
        return []
    with connect(db_path) as conn:
        codes = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT code FROM official_daily_price WHERE market = ? AND date = (SELECT MAX(date) FROM official_daily_price WHERE market = ?)",
                (MARKET_TWSE, MARKET_TWSE),
            )
        }
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'official_history_raw_ranges' AND type = 'table'").fetchone():
            codes.update(
                row[0] for row in conn.execute("SELECT DISTINCT code FROM official_history_raw_ranges WHERE market = ? AND code != ''", (MARKET_TWSE,))
            )
    return sorted(codes)


def resolve_auto_crawl_plan(db_path: Path, *, today: date | None = None) -> tuple[list[str], str, str]:
    """(codes, start_year_month, end_year_month) for the no-argument auto mode."""
    codes = load_twse_backfill_universe(db_path)
    return codes, EARLIEST_SUPPORTED_YEAR_MONTH, last_completed_year_month(today=today)


def resolve_auto_budget(max_requests: int | None, max_seconds: float | None) -> float | None:
    """Auto mode defaults to a bounded slice unless the caller already bounded it."""
    if max_requests is None and max_seconds is None:
        return DEFAULT_AUTO_MAX_SECONDS
    return max_seconds


def last_completed_year_month(*, today: date | None = None) -> str:
    """The most recent calendar month that has fully ended, e.g. 2026-07-27 -> "2026-06"."""
    today = today or date.today()
    year, month = today.year, today.month - 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year:04d}-{month:02d}"


def is_month_complete(year_month: str, *, today: date | None = None) -> bool:
    """A period is complete once it is no later than the last fully-ended month.

    The current, in-progress month must never be treated as complete: only part of
    it has happened yet, so checkpointing it as done would permanently hide the
    rest of that month from future runs.
    """
    return year_month <= last_completed_year_month(today=today)


def month_range(start_year_month: str, end_year_month: str) -> list[str]:
    start_year, start_month = (int(part) for part in start_year_month.split("-"))
    end_year, end_month = (int(part) for part in end_year_month.split("-"))
    months = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            month = 1
            year += 1
    return months


def fetch_twse_symbol_month(code: str, year_month: str, *, retries: int = 3, retry_sleep: float = 5.0) -> Any:
    params = {"date": f"{year_month.replace('-', '')}01", "stockNo": code, "response": "json"}
    return fetch_json_with_retry(TWSE_HISTORY_URL, params=params, retries=retries, retry_sleep=retry_sleep)


def normalize_twse_history_payload(code: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    fields = payload.get("fields") or []
    data = payload.get("data") or []
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    rows = []
    for raw_row in data:
        raw = dict(zip(fields, raw_row, strict=False))
        trade_date = parse_roc_slash_date(str(raw.get("日期", "")).strip())
        if not trade_date:
            continue
        close = parse_number(raw.get("收盤價"))
        volume = parse_int(raw.get("成交股數"))
        close_missing = close is None or not volume
        rows.append({
            "market": MARKET_TWSE,
            "code": code,
            "date": trade_date,
            "name": None,
            "open": parse_number(raw.get("開盤價")),
            "high": parse_number(raw.get("最高價")),
            "low": parse_number(raw.get("最低價")),
            "close": None if close_missing else close,
            "volume": volume,
            "value": parse_number(raw.get("成交金額")),
            "transactions": parse_int(raw.get("成交筆數")),
            "close_missing": close_missing,
            "raw_fields_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
            "fetched_at": fetched_at,
        })
    return rows


def fetch_tpex_market_day(day: str, *, retries: int = 3, retry_sleep: float = 5.0) -> Any:
    """Fetch one whole TPEx trading day. `day` is ISO (`2026-07-01`)."""
    params = {"date": day.replace("-", "/"), "type": TPEX_ALL_SECURITIES, "response": "json"}
    return fetch_json_with_retry(TPEX_HISTORY_URL, params=params, retries=retries, retry_sleep=retry_sleep)


def normalize_tpex_field_name(value: str) -> str:
    """Strip the presentation noise TPEx field names carry.

    Names arrive with stray padding and embedded markup -- `"收盤 "`,
    `"最後買量<br>(張數)"` -- so they are normalized before any signature comparison
    or lookup, or an invisible space decides whether a schema is recognized.
    """
    return str(value).replace("<br>", "").replace(" ", "").replace("\u3000", "").strip()


def normalize_tpex_market_day_payload(day: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize one TPEx market-day response into official_daily_price rows.

    Raises on an unrecognized field-name signature rather than guessing: both known
    schemas name every column, so a signature that matches neither means the vendor
    changed something, and parsing it positionally would mis-map columns silently.

    Volume here counts the round-lot session only, unlike the nightly OpenAPI fetch
    which includes odd-lot trading -- see design.md Decision 1 of
    add-tpex-price-history. Both are in shares; they count different things.
    """
    tables = payload.get("tables") or []
    table = tables[0] if tables else {}
    fields = tuple(normalize_tpex_field_name(name) for name in (table.get("fields") or []))
    data = table.get("data") or []

    if not data:
        return []
    if fields not in TPEX_FIELD_SIGNATURES:
        raise ValueError(f"unrecognized TPEx field signature for {day}: {fields}")

    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    rows = []
    for raw_row in data:
        raw = {name: value for name, value in zip(fields, raw_row, strict=False)}
        code = str(raw.get("代號", "")).strip()
        if not code:
            continue
        close = parse_number(raw.get("收盤"))
        volume = parse_int(raw.get("成交股數"))
        close_missing = close is None or not volume
        rows.append({
            "market": MARKET_TPEX,
            "code": code,
            "date": day,
            "name": str(raw.get("名稱", "")).strip() or None,
            "open": parse_number(raw.get("開盤")),
            "high": parse_number(raw.get("最高")),
            "low": parse_number(raw.get("最低")),
            "close": None if close_missing else close,
            "volume": volume,
            "value": parse_number(raw.get("成交金額(元)")),
            "transactions": parse_int(raw.get("成交筆數")),
            "close_missing": close_missing,
            "raw_fields_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
            "fetched_at": fetched_at,
        })
    return rows


def load_tpex_dates_to_fetch(db_path: Path, *, today: date | None = None) -> list[str]:
    """TPEx gaps, bulk-written days and unfinished whole-market checkpoints.

    **Gaps.** Dates the artifact proves were trading days but holds no TPEx rows for.
    TPEx answers a holiday and a date before its data floor identically -- a
    successful response with no rows and no distinguishing message -- so a crawler
    working from a calendar cannot tell "nothing traded" from "nothing published".
    Driving off TWSE's stored dates sidesteps that entirely: those are days the
    market demonstrably was open, so a TPEx gap on one of them is a real gap.

    The cost is that TPEx coverage reaches back only as far as TWSE history does,
    not to the TPEx endpoint's own 2007 floor. Explicit date arguments still reach
    the rest.

    **Days only the nightly bulk fetch ever wrote.** The two feeds do not publish the
    same quantity: the history page serves the round-lot session and matches the
    Shioaji-derived cache share for share, while the nightly bulk also counts odd-lot
    trading and runs about 1.8% higher. They share a primary key, so whichever ran
    last wins the row.

    Selecting on gaps alone made that permanent. A day the nightly fetch populated is
    not a gap, so it was never re-fetched: TPEx rows were history-written through
    2026-08-06 and bulk-written every day after, putting a one-day volume step on the
    live edge of the artifact that a volume-surge rule reads as a market-wide event.

    **Unfinished checkpoints.** Retry partial/error days even without price rows.
    A settled checkpoint is what says "this crawler wrote this day". It is a cleaner
    signal than the stored response's field signature -- no JSON parsing, no schema
    change, and it does not break when the endpoint changes shape again.
    """
    if not db_path.is_file():
        return []
    today = today or date.today()
    with connect(db_path) as conn:
        gaps = {
            row[0]
            for row in conn.execute(
                """
                SELECT DISTINCT date FROM official_daily_price WHERE market = ?
                EXCEPT
                SELECT DISTINCT date FROM official_daily_price WHERE market = ?
                """,
                (MARKET_TWSE, MARKET_TPEX),
            )
        }
        # On a database the crawler has never touched the checkpoint table does not
        # exist yet. Every TPEx day would then look bulk-written, which is true but
        # would queue a full re-crawl off a bare existence check; fall back to gaps
        # only and let the first real run establish checkpoints.
        has_checkpoints = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'official_history_raw_ranges'").fetchone()
        bulk_written = set()
        unfinished = set()
        settled = set()
        if has_checkpoints:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(official_history_raw_ranges)")}
            period_column = "period" if "period" in columns else "year_month"
            bulk_written = {row[0] for row in conn.execute("SELECT DISTINCT date FROM official_daily_price WHERE market = ?", (MARKET_TPEX,))}
            for period, status in conn.execute(
                f"SELECT {period_column}, status FROM official_history_raw_ranges "
                "WHERE market = ? AND code = '' AND status IN ('partial', 'error', 'ok', 'empty')",
                (MARKET_TPEX,),
            ):
                if status in {"ok", "empty"}:
                    settled.add(period)
                    continue
                try:
                    parsed = date.fromisoformat(period)
                    if parsed.isoformat() == period and TPEX_EARLIEST_DATE <= period <= today.isoformat():
                        unfinished.add(period)
                except (TypeError, ValueError):
                    continue
    return sorted((d for d in (gaps | bulk_written | unfinished) - settled if d >= TPEX_EARLIEST_DATE), reverse=True)


def tpex_day_has_rows(conn: sqlite3.Connection, day: str) -> bool:
    return market_day_has_rows(conn, MARKET_TPEX, day)


def market_day_has_rows(conn: sqlite3.Connection, market: str, day: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM official_daily_price WHERE market = ? AND date = ? LIMIT 1",
        (market, day),
    ).fetchone()
    return row is not None


def parse_roc_slash_date(value: str) -> str | None:
    """Parse a slash-separated ROC-year date, e.g. "115/07/01" -> "2026-07-01"."""
    parts = value.split("/")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    year = int(parts[0]) + 1911
    month = int(parts[1])
    day = int(parts[2])
    return f"{year:04d}-{month:02d}-{day:02d}"


def init_history_raw_ranges_table(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS official_history_raw_ranges (
                market TEXT NOT NULL,
                code TEXT NOT NULL,
                period TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                status TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                PRIMARY KEY (market, code, period)
            )
            """
        )
        # The column was `year_month` while TWSE symbol-months were the only unit. TPEx
        # units are single days, so the name became a misnomer. RENAME COLUMN is
        # metadata-only -- it does not rewrite the multi-GB database -- and runs here so
        # that whichever entry point touches the table first migrates it, exactly once.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(official_history_raw_ranges)")}
        if "year_month" in columns and "period" not in columns:
            logger.info("migrating official_history_raw_ranges: year_month -> period")
            conn.execute("ALTER TABLE official_history_raw_ranges RENAME COLUMN year_month TO period")


def is_unit_cached(conn: sqlite3.Connection, unit: CrawlUnit) -> bool:
    """Whether this unit has already been fetched with a settled result.

    Preserve legacy `empty` checkpoints as settled. New ambiguous TPEx empty
    responses remain partial; explicit `--force` can re-attempt settled units.
    """
    row = conn.execute(
        """
        SELECT 1 FROM official_history_raw_ranges
        WHERE market = ? AND code = ? AND period = ? AND status IN ('ok', 'empty')
        """,
        (unit.market, unit.code, unit.period),
    ).fetchone()
    return row is not None


def mark_unit_fetched(conn: sqlite3.Connection, unit: CrawlUnit, *, row_count: int, complete: bool = True) -> None:
    # A period still in progress (the current calendar month) is checkpointed as
    # 'partial', not 'ok', so is_unit_cached() does not skip it on the next run.
    # A completed fetch that returned nothing is 'empty': asked, answered, nothing stored.
    if not complete:
        status = "partial"
    else:
        status = "ok" if row_count else "empty"
    _upsert_unit_status(conn, unit, status=status, row_count=row_count)


def mark_unit_failed(conn: sqlite3.Connection, unit: CrawlUnit) -> None:
    _upsert_unit_status(conn, unit, status="error", row_count=0)


def _upsert_unit_status(conn: sqlite3.Connection, unit: CrawlUnit, *, status: str, row_count: int) -> None:
    conn.execute(
        """
        INSERT INTO official_history_raw_ranges (market, code, period, fetched_at, status, row_count)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(market, code, period) DO UPDATE SET
            fetched_at = excluded.fetched_at,
            status = excluded.status,
            row_count = excluded.row_count
        """,
        (unit.market, unit.code, unit.period, datetime.now(UTC).isoformat(timespec="seconds"), status, row_count),
    )


if __name__ == "__main__":
    setup_logging("backfill_official_history.log")
    main()
