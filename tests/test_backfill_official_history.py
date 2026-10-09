"""Offline parser coverage with self-authored synthetic financial values.
Schema and dates exercise validation; rows are not acquired market observations.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import backfill_official_history as backfill
import pytest
from backfill_official_history import (
    MARKET_TPEX,
    MARKET_TWSE,
    BackfillAlreadyRunningError,
    CrawlUnit,
    acquire_lock,
    build_crawl_units,
    build_tpex_crawl_units,
    init_history_raw_ranges_table,
    is_month_complete,
    is_unit_cached,
    last_completed_year_month,
    load_tpex_dates_to_fetch,
    load_twse_backfill_universe,
    month_range,
    normalize_tpex_market_day_payload,
    normalize_twse_history_payload,
    release_lock,
    resolve_auto_budget,
    resolve_auto_crawl_plan,
    run_backfill,
)
from fetch_official_daily_price import init_db, upsert_rows


@pytest.fixture(autouse=True)
def _isolated_default_lock_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Every run_backfill() call in this suite that omits lock_path would otherwise
    # fall back to the real data/.official_backfill.lock -- touching the actual
    # submodule data directory from a test run and risking a spurious
    # BackfillAlreadyRunningError if a real backfill happens to be running.
    monkeypatch.setattr(backfill, "DEFAULT_LOCK_PATH", tmp_path / ".official_backfill.lock")


def _official_row(market: str, code: str, trade_date: str) -> dict[str, Any]:
    return {
        "market": market,
        "code": code,
        "date": trade_date,
        "name": None,
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "volume": None,
        "value": None,
        "transactions": None,
        "close_missing": True,
        "raw_fields_json": "{}",
        "fetched_at": "2026-07-01T00:00:00+00:00",
    }


# Synthetic TWSE STOCK_DAY history; two rows exercise month handling.
TWSE_HISTORY_FIXTURE: dict[str, Any] = {
    "stat": "OK",
    "fields": ["日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌價差", "成交筆數", "註記"],
    "data": [
        ["115/07/01", "11,000", "12,000", "103.00", "106.00", "105.00", "106.00", "+107.00", "18,000", ""],
        ["115/07/02", "19,000", "20,000", "111.00", "112.00", "113.00", "114.00", "-115.00", "26,000", ""],
    ],
}


def test_month_range_is_inclusive_of_start_and_end():
    assert month_range("2026-05", "2026-07") == ["2026-05", "2026-06", "2026-07"]


def test_month_range_handles_single_month():
    assert month_range("2026-07", "2026-07") == ["2026-07"]


def test_month_range_crosses_year_boundary():
    assert month_range("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]


def test_build_crawl_units_is_cross_product_of_codes_and_months():
    units = build_crawl_units(["9007", "0050"], "2026-06", "2026-07")
    assert units == [
        CrawlUnit(market=MARKET_TWSE, code="9007", period="2026-06"),
        CrawlUnit(market=MARKET_TWSE, code="9007", period="2026-07"),
        CrawlUnit(market=MARKET_TWSE, code="0050", period="2026-06"),
        CrawlUnit(market=MARKET_TWSE, code="0050", period="2026-07"),
    ]


def test_normalize_twse_history_payload_parses_rows():
    rows = normalize_twse_history_payload("9007", TWSE_HISTORY_FIXTURE)
    assert len(rows) == 2
    first = rows[0]
    assert first["market"] == "TWSE"
    assert first["code"] == "9007"
    assert first["date"] == "2026-07-01"
    assert first["close"] == 106.0
    assert first["volume"] == 11000
    assert first["close_missing"] is False


def test_normalize_twse_history_payload_skips_rows_with_no_date():
    payload = {**TWSE_HISTORY_FIXTURE, "data": [["", "0", "0", "0", "0", "0", "---", "0.00", "0", ""]]}
    assert normalize_twse_history_payload("9007", payload) == []


def test_is_month_complete_for_past_and_current_month():
    today = date(2026, 7, 27)
    assert is_month_complete("2026-06", today=today) is True
    assert is_month_complete("2026-07", today=today) is False


def test_last_completed_year_month_is_previous_calendar_month():
    assert last_completed_year_month(today=date(2026, 7, 27)) == "2026-06"


def test_last_completed_year_month_crosses_year_boundary():
    assert last_completed_year_month(today=date(2026, 1, 15)) == "2025-12"


def test_run_backfill_checkpoints_current_month_partial_and_past_month_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # A month that hasn't ended yet must never be checkpointed as 'ok' -- otherwise no
    # later run ever revisits it and the rest of that month's data is silently lost.
    monkeypatch.setattr(backfill, "fetch_twse_symbol_month", lambda code, year_month, **_kw: TWSE_HISTORY_FIXTURE)
    monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)
    db_path = tmp_path / "official_daily.sqlite"

    run_backfill(
        codes=["9007"],
        start_year_month="2026-06",
        end_year_month="2026-07",
        db_path=db_path,
        today=date(2026, 7, 27),
    )

    with sqlite3.connect(db_path) as conn:
        statuses = dict(conn.execute("SELECT period, status FROM official_history_raw_ranges WHERE code = '9007'").fetchall())
    assert statuses["2026-06"] == "ok"
    assert statuses["2026-07"] == "partial"


def test_partial_month_is_refetched_on_rerun_while_ok_month_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def fake_fetch(code: str, year_month: str, **_kwargs) -> dict[str, Any]:
        calls.append(year_month)
        return TWSE_HISTORY_FIXTURE

    monkeypatch.setattr(backfill, "fetch_twse_symbol_month", fake_fetch)
    monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)
    db_path = tmp_path / "official_daily.sqlite"
    kwargs: dict[str, Any] = dict(codes=["9007"], start_year_month="2026-06", end_year_month="2026-07", db_path=db_path, today=date(2026, 7, 27))

    run_backfill(**kwargs)
    calls.clear()
    stats = run_backfill(**kwargs)

    assert calls == ["2026-07"]
    assert stats == {"fetched": 1, "skipped_cached": 1, "failed": 0}


def test_run_backfill_fetches_all_units_on_first_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(backfill, "fetch_twse_symbol_month", lambda code, year_month, **_kw: TWSE_HISTORY_FIXTURE)
    monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)
    db_path = tmp_path / "official_daily.sqlite"

    stats = run_backfill(codes=["9007"], start_year_month="2026-07", end_year_month="2026-07", db_path=db_path)

    assert stats == {"fetched": 1, "skipped_cached": 0, "failed": 0}
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM official_daily_price WHERE code = '9007'").fetchone()[0]
    assert count == 2


def test_run_backfill_skips_cached_units_on_second_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls = []

    def fake_fetch(code: str, year_month: str, **_kwargs) -> dict[str, Any]:
        calls.append((code, year_month))
        return TWSE_HISTORY_FIXTURE

    monkeypatch.setattr(backfill, "fetch_twse_symbol_month", fake_fetch)
    monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)
    db_path = tmp_path / "official_daily.sqlite"
    # Pin "today" a month after the fetched period so it's a completed month and
    # eligible to be cached -- otherwise this test's own run date could land on the
    # fetched month and make it correctly non-cacheable (see is_month_complete()).
    kwargs: dict[str, Any] = dict(codes=["9007"], start_year_month="2026-07", end_year_month="2026-07", db_path=db_path, today=date(2026, 8, 1))

    run_backfill(**kwargs)
    stats = run_backfill(**kwargs)

    assert stats == {"fetched": 0, "skipped_cached": 1, "failed": 0}
    assert len(calls) == 1


def test_run_backfill_is_idempotent_no_duplicate_rows_across_reruns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(backfill, "fetch_twse_symbol_month", lambda code, year_month, **_kw: TWSE_HISTORY_FIXTURE)
    monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)
    db_path = tmp_path / "official_daily.sqlite"

    run_backfill(codes=["9007"], start_year_month="2026-07", end_year_month="2026-07", db_path=db_path)
    run_backfill(codes=["9007"], start_year_month="2026-07", end_year_month="2026-07", db_path=db_path, force=True)

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM official_daily_price WHERE code = '9007'").fetchone()[0]
    assert count == 2


def test_run_backfill_stops_after_max_requests_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls = []

    def fake_fetch(code: str, year_month: str, **_kwargs) -> dict[str, Any]:
        calls.append((code, year_month))
        return TWSE_HISTORY_FIXTURE

    monkeypatch.setattr(backfill, "fetch_twse_symbol_month", fake_fetch)
    monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)
    db_path = tmp_path / "official_daily.sqlite"

    stats = run_backfill(
        codes=["9007", "0050"],
        start_year_month="2026-06",
        end_year_month="2026-07",
        db_path=db_path,
        max_requests=2,
    )

    assert stats["fetched"] == 2
    assert len(calls) == 2


def test_run_backfill_logs_start_line_with_unit_count_and_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    monkeypatch.setattr(backfill, "fetch_twse_symbol_month", lambda code, year_month, **_kw: TWSE_HISTORY_FIXTURE)
    monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)
    db_path = tmp_path / "official_daily.sqlite"

    with caplog.at_level(logging.INFO, logger="backfill_official_history"):
        run_backfill(codes=["9007", "0050"], start_year_month="2026-07", end_year_month="2026-07", db_path=db_path, max_seconds=120.0, today=date(2026, 8, 1))

    start_lines = [r.message for r in caplog.records if "backfill run starting" in r.message]
    assert len(start_lines) == 1
    assert "units=2" in start_lines[0]
    assert "budget=120s" in start_lines[0]


def test_run_backfill_logs_finish_reason_when_budget_reached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    monkeypatch.setattr(backfill, "fetch_twse_symbol_month", lambda code, year_month, **_kw: TWSE_HISTORY_FIXTURE)
    monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)
    db_path = tmp_path / "official_daily.sqlite"

    with caplog.at_level(logging.INFO, logger="backfill_official_history"):
        run_backfill(codes=["9007", "0050"], start_year_month="2026-06", end_year_month="2026-07", db_path=db_path, max_requests=1, today=date(2026, 8, 1))

    finish_lines = [r.message for r in caplog.records if "backfill run finished" in r.message]
    assert len(finish_lines) == 1
    assert "reason=max_requests budget reached" in finish_lines[0]


def test_run_backfill_logs_finish_reason_when_units_exhausted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    monkeypatch.setattr(backfill, "fetch_twse_symbol_month", lambda code, year_month, **_kw: TWSE_HISTORY_FIXTURE)
    monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)
    db_path = tmp_path / "official_daily.sqlite"

    with caplog.at_level(logging.INFO, logger="backfill_official_history"):
        run_backfill(codes=["9007"], start_year_month="2026-07", end_year_month="2026-07", db_path=db_path, today=date(2026, 8, 1))

    finish_lines = [r.message for r in caplog.records if "backfill run finished" in r.message]
    assert len(finish_lines) == 1
    assert "reason=units exhausted" in finish_lines[0]


def test_run_backfill_omits_finish_line_on_keyboard_interrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    # The interrupted-run summary (see the KeyboardInterrupt handler) already covers
    # this case; logging both would leave two different-shaped summaries for one run.
    def fetch_then_interrupt(code: str, year_month: str, **_kwargs) -> dict[str, Any]:
        if year_month == "2026-07":
            raise KeyboardInterrupt
        return TWSE_HISTORY_FIXTURE

    monkeypatch.setattr(backfill, "fetch_twse_symbol_month", fetch_then_interrupt)
    monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)
    db_path = tmp_path / "official_daily.sqlite"

    with caplog.at_level(logging.INFO, logger="backfill_official_history"):
        run_backfill(codes=["9007"], start_year_month="2026-06", end_year_month="2026-07", db_path=db_path, today=date(2026, 8, 1))

    assert not [r.message for r in caplog.records if "backfill run finished" in r.message]
    assert [r.message for r in caplog.records if "backfill interrupted" in r.message]


def test_run_backfill_logs_progress_heartbeat_periodically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    clock = {"t": 0.0}
    monkeypatch.setattr(backfill.time, "monotonic", lambda: clock["t"])

    def fake_fetch(code: str, year_month: str, **_kwargs) -> dict[str, Any]:
        # Each fetch advances the clock past two-thirds of the heartbeat interval, so
        # the heartbeat fires roughly every other unit rather than every one.
        clock["t"] += backfill.HEARTBEAT_INTERVAL_SECONDS * 2 / 3
        return TWSE_HISTORY_FIXTURE

    monkeypatch.setattr(backfill, "fetch_twse_symbol_month", fake_fetch)
    monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)
    db_path = tmp_path / "official_daily.sqlite"

    with caplog.at_level(logging.INFO, logger="backfill_official_history"):
        run_backfill(codes=["9007", "0050", "1101"], start_year_month="2026-07", end_year_month="2026-07", db_path=db_path, today=date(2026, 8, 1))

    heartbeat_lines = [r.message for r in caplog.records if "backfill progress" in r.message]
    assert len(heartbeat_lines) == 1


def test_run_backfill_records_failed_unit_and_does_not_mark_it_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def failing_fetch(code: str, year_month: str, **_kwargs) -> dict[str, Any]:
        raise RuntimeError("endpoint unavailable")

    monkeypatch.setattr(backfill, "fetch_twse_symbol_month", failing_fetch)
    monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)
    db_path = tmp_path / "official_daily.sqlite"

    stats = run_backfill(codes=["9007"], start_year_month="2026-07", end_year_month="2026-07", db_path=db_path)

    assert stats == {"fetched": 0, "skipped_cached": 0, "failed": 1}
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        assert is_unit_cached(conn, CrawlUnit(market=MARKET_TWSE, code="9007", period="2026-07")) is False


def test_run_backfill_backs_off_after_consecutive_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def failing_fetch(code: str, year_month: str, **_kwargs) -> dict[str, Any]:
        raise RuntimeError("endpoint unavailable")

    sleep_calls: list[float] = []
    monkeypatch.setattr(backfill, "fetch_twse_symbol_month", failing_fetch)
    monkeypatch.setattr(backfill.time, "sleep", sleep_calls.append)
    db_path = tmp_path / "official_daily.sqlite"

    run_backfill(
        codes=["9007"],
        start_year_month="2026-05",
        end_year_month="2026-07",
        db_path=db_path,
        throttle_seconds=1.0,
    )

    # Every attempted unit sleeps; consecutive failures increase backoff.
    assert len(sleep_calls) == 3
    assert sleep_calls[0] > 1.0
    assert sleep_calls[1] > sleep_calls[0]
    assert sleep_calls[2] > sleep_calls[1]


def test_run_backfill_stops_cleanly_on_keyboard_interrupt_and_keeps_completed_units(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def fetch_then_interrupt(code: str, year_month: str, **_kwargs) -> dict[str, Any]:
        calls.append(year_month)
        if year_month == "2026-07":
            raise KeyboardInterrupt
        return TWSE_HISTORY_FIXTURE

    monkeypatch.setattr(backfill, "fetch_twse_symbol_month", fetch_then_interrupt)
    monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)
    db_path = tmp_path / "official_daily.sqlite"
    lock_path = tmp_path / "backfill.lock"

    stats = run_backfill(
        codes=["9007"],
        start_year_month="2026-06",
        end_year_month="2026-07",
        db_path=db_path,
        lock_path=lock_path,
        today=date(2026, 8, 1),
    )

    # Ctrl+C does not propagate as a traceback -- it is caught, logged, and the
    # already-committed unit (2026-06) stays checkpointed for the interrupted run.
    assert calls == ["2026-06", "2026-07"]
    assert stats["fetched"] == 1
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        assert is_unit_cached(conn, CrawlUnit(market=MARKET_TWSE, code="9007", period="2026-06")) is True
    assert not lock_path.exists()


def test_run_backfill_releases_lock_after_normal_completion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(backfill, "fetch_twse_symbol_month", lambda code, year_month, **_kw: TWSE_HISTORY_FIXTURE)
    monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)
    db_path = tmp_path / "official_daily.sqlite"
    lock_path = tmp_path / "backfill.lock"

    run_backfill(codes=["9007"], start_year_month="2026-07", end_year_month="2026-07", db_path=db_path, lock_path=lock_path, today=date(2026, 8, 1))

    assert not lock_path.exists()


def test_run_backfill_raises_and_issues_no_request_when_lock_already_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def capture_fetch(code: str, year_month: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append(year_month)
        return TWSE_HISTORY_FIXTURE

    monkeypatch.setattr(backfill, "fetch_twse_symbol_month", capture_fetch)
    monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)
    db_path = tmp_path / "official_daily.sqlite"
    lock_path = tmp_path / "backfill.lock"
    acquire_lock(lock_path)

    try:
        with pytest.raises(BackfillAlreadyRunningError):
            run_backfill(codes=["9007"], start_year_month="2026-07", end_year_month="2026-07", db_path=db_path, lock_path=lock_path, today=date(2026, 8, 1))
    finally:
        release_lock(lock_path)

    assert calls == []


def test_pid_probe_does_not_interrupt_the_probed_process():
    script = (
        "import os, sys; "
        "sys.path.insert(0, 'scripts'); "
        "from backfill_official_history import _pid_is_running; "
        "assert _pid_is_running(os.getpid()); "
        "print('alive')"
    )
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        creationflags=creationflags,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "alive"


def test_acquire_lock_reclaims_a_stale_lock_left_by_a_dead_process(tmp_path: Path):
    lock_path = tmp_path / "backfill.lock"
    lock_path.write_text(json.dumps({"pid": 999_999_999, "started_at": "2020-01-01T00:00:00+00:00"}), encoding="utf-8")

    acquire_lock(lock_path)

    assert lock_path.is_file()
    release_lock(lock_path)
    assert not lock_path.exists()


def test_build_crawl_units_newest_first_groups_by_month_descending(tmp_path: Path):
    units = build_crawl_units(["9007", "0050"], "2026-06", "2026-07", newest_first=True)
    assert units == [
        CrawlUnit(market=MARKET_TWSE, code="9007", period="2026-07"),
        CrawlUnit(market=MARKET_TWSE, code="0050", period="2026-07"),
        CrawlUnit(market=MARKET_TWSE, code="9007", period="2026-06"),
        CrawlUnit(market=MARKET_TWSE, code="0050", period="2026-06"),
    ]


def test_build_crawl_units_default_order_is_unchanged_by_newest_first_flag():
    # Explicit-range callers that don't ask for newest_first keep the existing
    # code-major, ascending-month order.
    assert build_crawl_units(["9007", "0050"], "2026-06", "2026-07") == build_crawl_units(["9007", "0050"], "2026-06", "2026-07", newest_first=False)


def test_build_crawl_units_drops_months_earlier_than_the_supported_floor():
    # The endpoint itself refuses anything before 2010-01-04; even an explicit
    # range must never issue a request for an earlier period.
    units = build_crawl_units(["9007"], "2009-11", "2010-02")
    assert [u.period for u in units] == ["2010-01", "2010-02"]


def test_load_twse_backfill_universe_uses_newest_day_without_checkpoints(tmp_path: Path):
    db_path = tmp_path / "official_daily.sqlite"
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        upsert_rows(
            conn,
            [
                _official_row("TWSE", "9007", "2026-07-01"),
                _official_row("TWSE", "0050", "2026-07-01"),
                _official_row("TWSE", "9007", "2026-07-02"),
                _official_row("TPEx", "006201", "2026-07-01"),
            ],
        )
        conn.commit()

    assert load_twse_backfill_universe(db_path) == ["9007"]


def test_load_twse_backfill_universe_is_empty_when_no_official_data_yet(tmp_path: Path):
    db_path = tmp_path / "official_daily.sqlite"
    init_db(db_path)
    assert load_twse_backfill_universe(db_path) == []


def test_resolve_auto_crawl_plan_uses_floor_start_and_last_completed_end(tmp_path: Path):
    db_path = tmp_path / "official_daily.sqlite"
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        upsert_rows(conn, [_official_row("TWSE", "9007", "2026-07-01")])
        conn.commit()

    codes, start, end = resolve_auto_crawl_plan(db_path, today=date(2026, 7, 27))

    assert codes == ["9007"]
    assert start == backfill.EARLIEST_SUPPORTED_YEAR_MONTH
    assert end == "2026-06"


def test_resolve_auto_crawl_plan_returns_empty_codes_when_universe_is_empty(tmp_path: Path):
    db_path = tmp_path / "official_daily.sqlite"
    init_db(db_path)

    codes, _start, _end = resolve_auto_crawl_plan(db_path, today=date(2026, 7, 27))

    assert codes == []


def test_resolve_auto_budget_defaults_to_one_hour_when_unset():
    assert resolve_auto_budget(None, None) == backfill.DEFAULT_AUTO_MAX_SECONDS


def test_resolve_auto_budget_respects_explicit_max_seconds():
    assert resolve_auto_budget(None, 120.0) == 120.0


def test_resolve_auto_budget_leaves_max_seconds_none_when_max_requests_given():
    # An explicit --max-requests is itself a deliberate bound; forcing a default
    # --max-seconds on top of it would silently double-constrain the run.
    assert resolve_auto_budget(5, None) is None


def test_main_with_no_args_runs_auto_mode_against_the_official_universe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "official_daily.sqlite"
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        upsert_rows(conn, [_official_row("TWSE", "9007", "2026-07-01")])
        conn.commit()

    captured: dict[str, Any] = {}

    def fake_run_backfill(**kwargs: Any) -> dict[str, int]:
        captured.update(kwargs)
        return {"fetched": 0, "skipped_cached": 0, "failed": 0}

    monkeypatch.setattr(backfill, "run_backfill", fake_run_backfill)
    monkeypatch.setattr(sys, "argv", ["backfill_official_history.py", "--db-path", str(db_path)])

    backfill.main()

    assert captured["codes"] == ["9007"]
    assert captured["start_year_month"] == backfill.EARLIEST_SUPPORTED_YEAR_MONTH
    assert captured["newest_first"] is True
    assert captured["max_seconds"] == backfill.DEFAULT_AUTO_MAX_SECONDS


def test_main_with_explicit_range_does_not_use_auto_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "official_daily.sqlite"
    captured: dict[str, Any] = {}

    def fake_run_backfill(**kwargs: Any) -> dict[str, int]:
        captured.update(kwargs)
        return {"fetched": 0, "skipped_cached": 0, "failed": 0}

    monkeypatch.setattr(backfill, "run_backfill", fake_run_backfill)
    monkeypatch.setattr(
        sys,
        "argv",
        ["backfill_official_history.py", "--codes", "9007", "--start-year-month", "2026-06", "--end-year-month", "2026-07", "--db-path", str(db_path)],
    )

    backfill.main()

    assert captured["codes"] == ["9007"]
    assert captured["start_year_month"] == "2026-06"
    assert captured["newest_first"] is False
    assert captured["max_seconds"] is None


def test_main_errors_when_only_some_explicit_args_given(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "argv", ["backfill_official_history.py", "--codes", "9007"])
    with pytest.raises(SystemExit):
        backfill.main()


# --- TPEx whole-market history ----------------------------------------------

# Synthetic afterTrading/otc response. Field names keep the padding and
# embedded markup the endpoint really sends, because normalizing them is the thing
# under test. Volumes are multiples of 1000: this endpoint counts the round-lot
# session only.
TPEX_FIXTURE_17_FIELD_CURRENT: dict[str, Any] = {
    "stat": "ok",
    "date": "20260819",
    "tables": [
        {
            "fields": [
                "代號",
                "名稱",
                "收盤 ",
                "漲跌",
                "開盤 ",
                "最高 ",
                "最低",
                "成交股數  ",
                " 成交金額(元)",
                " 成交筆數 ",
                "最後買價",
                "最後買量<br>(張數)",
                "最後賣價",
                "最後賣量<br>(張數)",
                "發行股數 ",
                "次日漲停價 ",
                "次日跌停價 ",
            ],
            "data": [
                [
                    "27000",
                    "穩懋",
                    "127.00",
                    "-119.00",
                    "120.00",
                    "121.00",
                    "122.00",
                    "33,000",
                    "34,000",
                    "35,000",
                    "126.00",
                    "12",
                    "127.00",
                    "8",
                    "38,000",
                    "129.00",
                    "130.00",
                ],
                ["41000", "沒成交", "---", "0.00", "---", "---", "---", "0", "0", "0", "---", "0", "---", "0", "42,000", "0.00", "0.00"],
            ],
        }
    ],
}

TPEX_FIXTURE_15_FIELD: dict[str, Any] = {
    "stat": "ok",
    "date": "20150310",
    "tables": [
        {
            "fields": [
                "代號",
                "名稱",
                "收盤 ",
                "漲跌",
                "開盤 ",
                "最高 ",
                "最低",
                "成交股數  ",
                " 成交金額(元)",
                " 成交筆數 ",
                "最後買價",
                "最後賣價",
                "發行股數 ",
                "次日漲停價 ",
                "次日跌停價 ",
            ],
            "data": [
                [
                    "43000",
                    "鮮活",
                    "136.00",
                    "+ 2.00",
                    "137.00",
                    "136.00",
                    "137.00",
                    "48,000",
                    "49,000",
                    "15",
                    "140.00",
                    "141.00",
                    "52,000",
                    "143.00",
                    "144.00",
                ],
            ],
        }
    ],
}


# The middle era is identical to the current one except for two column labels the
# parser never reads. Deriving the fixture states that relationship, instead of
# duplicating seventeen fields and hoping the two copies stay in step.
TPEX_FIXTURE_17_FIELD_MIDDLE: dict[str, Any] = json.loads(json.dumps(TPEX_FIXTURE_17_FIELD_CURRENT))
TPEX_FIXTURE_17_FIELD_MIDDLE["date"] = "20230406"
TPEX_FIXTURE_17_FIELD_MIDDLE["tables"][0]["fields"] = [field.replace("(張數)", "(千股)") for field in TPEX_FIXTURE_17_FIELD_MIDDLE["tables"][0]["fields"]]


def test_normalize_tpex_reads_the_middle_era_labelled_in_thousands_of_shares():
    # 2020-04-30 through 2025-01-09 label the last bid/ask size 千股 rather than 張數.
    # Only the labels move; 成交股數 stays in shares, which a live cross-check against
    # this repository's Shioaji bars confirmed to the share on three sampled days.
    rows = normalize_tpex_market_day_payload("2023-04-06", TPEX_FIXTURE_17_FIELD_MIDDLE)

    assert [row["code"] for row in rows] == ["27000", "41000"]
    assert rows[0]["close"] == 127.0
    assert rows[0]["volume"] == 33000
    assert rows[1]["close_missing"] is True


def test_normalize_tpex_current_era_payload_produces_official_rows():
    rows = normalize_tpex_market_day_payload("2026-08-19", TPEX_FIXTURE_17_FIELD_CURRENT)

    assert [row["code"] for row in rows] == ["27000", "41000"]
    traded = rows[0]
    assert traded["market"] == MARKET_TPEX
    assert traded["date"] == "2026-08-19"
    assert traded["close"] == 127.0
    assert traded["open"] == 120.0
    # Shares, not lots: the endpoint reports 張 x 1000 and the artifact stores shares.
    assert traded["volume"] == 33000
    assert traded["transactions"] == 35000
    assert traded["close_missing"] is False


def test_normalize_tpex_marks_a_no_trade_row_close_missing():
    rows = normalize_tpex_market_day_payload("2026-08-19", TPEX_FIXTURE_17_FIELD_CURRENT)

    no_trade = rows[1]
    assert no_trade["close_missing"] is True
    assert no_trade["close"] is None
    assert no_trade["volume"] == 0


def test_normalize_tpex_reads_the_older_fifteen_field_schema_by_name():
    # The two eras differ by two columns inserted in the middle. Reading by field
    # name rather than position is what keeps one normalizer correct for both.
    rows = normalize_tpex_market_day_payload("2015-03-10", TPEX_FIXTURE_15_FIELD)

    assert len(rows) == 1
    assert rows[0]["code"] == "43000"
    assert rows[0]["close"] == 136.0
    assert rows[0]["volume"] == 48000


def test_normalize_tpex_raises_on_an_unrecognized_field_signature():
    # Parsing an unknown schema positionally would mis-map columns silently, which is
    # worse than failing: the rows would look plausible and be wrong.
    mutated = json.loads(json.dumps(TPEX_FIXTURE_17_FIELD_CURRENT))
    mutated["tables"][0]["fields"].append("新欄位")

    with pytest.raises(ValueError, match="unrecognized TPEx field signature"):
        normalize_tpex_market_day_payload("2026-08-19", mutated)


def test_normalize_tpex_returns_nothing_for_an_empty_response():
    empty = {"stat": "ok", "tables": [{"fields": ["代號"], "data": []}]}

    assert normalize_tpex_market_day_payload("2026-08-09", empty) == []


def test_load_tpex_dates_to_fetch_returns_proven_trading_days_missing_tpex_rows(tmp_path: Path):
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        upsert_rows(
            conn,
            [
                _official_row(MARKET_TWSE, "9007", "2026-07-01"),
                _official_row(MARKET_TWSE, "9007", "2026-07-02"),
                _official_row(MARKET_TWSE, "9007", "2026-07-03"),
                _official_row(MARKET_TPEX, "27000", "2026-07-02"),
            ],
        )

    # 07-02 already has TPEx rows; the other two are gaps, newest first.
    assert load_tpex_dates_to_fetch(db_path) == ["2026-07-03", "2026-07-01"]


def test_load_tpex_dates_to_fetch_ignores_dates_before_the_endpoint_floor(tmp_path: Path):
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        upsert_rows(conn, [_official_row(MARKET_TWSE, "9007", "2005-01-03")])

    assert load_tpex_dates_to_fetch(db_path) == []


def test_build_tpex_crawl_units_carries_no_code():
    units = build_tpex_crawl_units(["2026-07-03", "2026-07-01"])

    assert units == [
        CrawlUnit(market=MARKET_TPEX, code="", period="2026-07-03"),
        CrawlUnit(market=MARKET_TPEX, code="", period="2026-07-01"),
    ]


def test_run_backfill_stores_a_tpex_market_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(backfill, "fetch_tpex_market_day", lambda day, **_kw: TPEX_FIXTURE_17_FIELD_CURRENT)
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)

    stats = run_backfill(
        codes=[],
        start_year_month="2026-07",
        end_year_month="2026-07",
        db_path=db_path,
        throttle_seconds=0,
        tpex_dates=["2026-08-19"],
    )

    assert stats["fetched"] == 1
    with sqlite3.connect(db_path) as conn:
        stored = conn.execute(
            "SELECT code, close FROM official_daily_price WHERE market = ? AND date = ? ORDER BY code",
            (MARKET_TPEX, "2026-08-19"),
        ).fetchall()
        status = conn.execute(
            "SELECT status FROM official_history_raw_ranges WHERE market = ? AND period = ?",
            (MARKET_TPEX, "2026-08-19"),
        ).fetchone()
    assert stored == [("27000", 127.0), ("41000", None)]
    assert status == ("ok",)


def test_tpex_empty_then_empty_then_success_recovers_without_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # An unpublished response remains retryable even after a long interruption.
    calls: list[str] = []

    def fake_fetch(day: str, **_kwargs) -> dict[str, Any]:
        calls.append(day)
        if len(calls) == 3:
            return TPEX_FIXTURE_17_FIELD_CURRENT
        return {"stat": "ok", "tables": [{"fields": ["代號"], "data": []}]}

    monkeypatch.setattr(backfill, "fetch_tpex_market_day", fake_fetch)
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    kwargs: dict[str, Any] = dict(
        codes=[],
        start_year_month="2026-07",
        end_year_month="2026-07",
        db_path=db_path,
        throttle_seconds=0,
        tpex_dates=["2011-06-15"],
    )

    for expected_status in ("partial", "partial", "ok"):
        result = run_backfill(**kwargs)
        assert result == {"fetched": 1, "skipped_cached": 0, "failed": 0}
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT status FROM official_history_raw_ranges WHERE market = ?", (MARKET_TPEX,)).fetchone() == (expected_status,)
            count = conn.execute("SELECT COUNT(*) FROM official_daily_price WHERE market = ? AND date = '2011-06-15'", (MARKET_TPEX,)).fetchone()[0]
        assert (count > 0) == (expected_status == "ok")
    assert calls == ["2011-06-15"] * 3
    assert run_backfill(**kwargs)["skipped_cached"] == 1


def test_tpex_and_twse_units_checkpoint_independently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(backfill, "fetch_tpex_market_day", lambda day, **_kw: TPEX_FIXTURE_17_FIELD_CURRENT)
    monkeypatch.setattr(backfill, "fetch_twse_symbol_month", lambda code, period, **_kw: TWSE_HISTORY_FIXTURE)
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)

    run_backfill(
        codes=["9007"],
        start_year_month="2026-07",
        end_year_month="2026-07",
        db_path=db_path,
        throttle_seconds=0,
        tpex_dates=["2026-08-19"],
        today=date(2026, 8, 21),
    )

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT market, code, period FROM official_history_raw_ranges ORDER BY market").fetchall()
    assert rows == [(MARKET_TPEX, "", "2026-08-19"), (MARKET_TWSE, "9007", "2026-07")]


def test_run_backfill_refetching_a_tpex_day_does_not_duplicate_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(backfill, "fetch_tpex_market_day", lambda day, **_kw: TPEX_FIXTURE_17_FIELD_CURRENT)
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    kwargs: dict[str, Any] = dict(
        codes=[],
        start_year_month="2026-07",
        end_year_month="2026-07",
        db_path=db_path,
        throttle_seconds=0,
        tpex_dates=["2026-08-19"],
        force=True,
    )

    run_backfill(**kwargs)
    run_backfill(**kwargs)

    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM official_daily_price WHERE market = ? AND date = ?",
            (MARKET_TPEX, "2026-08-19"),
        ).fetchone()[0]
    assert count == 2


def test_init_migrates_a_checkpoint_table_that_still_has_year_month(tmp_path: Path):
    # The column was named year_month while TWSE symbol-months were the only unit.
    # An existing database must migrate in place, keeping its rows.
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE official_history_raw_ranges (
                market TEXT NOT NULL,
                code TEXT NOT NULL,
                year_month TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                status TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                PRIMARY KEY (market, code, year_month)
            )
            """
        )
        conn.execute("INSERT INTO official_history_raw_ranges VALUES ('TWSE', '9007', '2026-07', 'x', 'ok', 20)")

    init_history_raw_ranges_table(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(official_history_raw_ranges)")}
        preserved = conn.execute("SELECT market, code, period, status FROM official_history_raw_ranges").fetchall()
    assert "period" in columns
    assert "year_month" not in columns
    assert preserved == [("TWSE", "9007", "2026-07", "ok")]


def test_a_bulk_written_tpex_day_is_selected_for_refetch(tmp_path: Path):
    # The nightly bulk and the history page do not publish the same quantity -- bulk
    # also counts odd-lot trading and runs ~1.8% higher -- and they share a primary
    # key, so whichever ran last wins. Selecting on gaps alone left every
    # bulk-written day on the wrong definition permanently, because a day that has
    # rows is not a gap. A settled checkpoint is what says this crawler wrote the day.
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    init_history_raw_ranges_table(db_path)
    with sqlite3.connect(db_path) as conn:
        upsert_rows(
            conn,
            [
                _official_row(MARKET_TWSE, "9007", "2026-08-06"),
                _official_row(MARKET_TWSE, "9007", "2026-08-07"),
                _official_row(MARKET_TPEX, "27000", "2026-08-06"),
                _official_row(MARKET_TPEX, "27000", "2026-08-07"),
            ],
        )
        # 08-06 was written by this crawler; 08-07 only ever by the nightly bulk.
        conn.execute(
            "INSERT INTO official_history_raw_ranges (market, code, period, fetched_at, status, row_count) "
            "VALUES (?, '', '2026-08-06', '2026-08-06T00:00:00+00:00', 'ok', 1)",
            (MARKET_TPEX,),
        )
        conn.commit()

    assert load_tpex_dates_to_fetch(db_path) == ["2026-08-07"]


def test_a_day_this_crawler_settled_as_empty_is_not_refetched(tmp_path: Path):
    # Preserve previously settled empties without a blanket historical repair.
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    init_history_raw_ranges_table(db_path)
    with sqlite3.connect(db_path) as conn:
        upsert_rows(conn, [_official_row(MARKET_TWSE, "9007", "2026-08-07"), _official_row(MARKET_TPEX, "27000", "2026-08-07")])
        conn.execute(
            "INSERT INTO official_history_raw_ranges (market, code, period, fetched_at, status, row_count) "
            "VALUES (?, '', '2026-08-07', '2026-08-07T00:00:00+00:00', 'empty', 0)",
            (MARKET_TPEX,),
        )
        conn.commit()

    assert load_tpex_dates_to_fetch(db_path) == []


@pytest.mark.parametrize("period_column", ["period", "year_month"])
@pytest.mark.parametrize("status", ["partial", "error"])
def test_tpex_checkpoint_only_old_day_resumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, period_column: str, status: str):
    db_path = tmp_path / "official.sqlite"
    day = "2011-06-15"
    init_db(db_path)
    init_history_raw_ranges_table(db_path)
    with sqlite3.connect(db_path) as conn:
        if period_column == "year_month":
            conn.execute("ALTER TABLE official_history_raw_ranges RENAME COLUMN period TO year_month")
        conn.execute(
            f"INSERT INTO official_history_raw_ranges (market, code, {period_column}, fetched_at, status, row_count) "
            "VALUES (?, '', ?, '2011-06-15T00:00:00+00:00', ?, 0)",
            (MARKET_TPEX, day, status),
        )
    assert load_tpex_dates_to_fetch(db_path, today=date(2026, 10, 4)) == [day]
    with sqlite3.connect(db_path) as conn:
        assert period_column in {row[1] for row in conn.execute("PRAGMA table_info(official_history_raw_ranges)")}
        assert conn.execute("SELECT COUNT(*) FROM official_daily_price").fetchone() == (0,)
    monkeypatch.setattr(backfill, "fetch_tpex_market_day", lambda *_args, **_kwargs: {"tables": []})
    kwargs: dict[str, Any] = dict(codes=[], start_year_month="2010-01", end_year_month="2010-01", auto_tpex_dates=True, db_path=db_path, throttle_seconds=0)
    assert run_backfill(**kwargs) == {"fetched": 1, "skipped_cached": 0, "failed": 0}
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT status FROM official_history_raw_ranges").fetchone() == ("partial",)
    assert load_tpex_dates_to_fetch(db_path) == [day]
    monkeypatch.setattr(backfill, "fetch_tpex_market_day", lambda *_args, **_kwargs: TPEX_FIXTURE_17_FIELD_CURRENT)
    assert run_backfill(**kwargs) == {"fetched": 1, "skipped_cached": 0, "failed": 0}
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT status FROM official_history_raw_ranges").fetchone() == ("ok",)
        assert conn.execute("SELECT COUNT(*) FROM official_daily_price WHERE market = ? AND date = ?", (MARKET_TPEX, day)).fetchone()[0] > 0
    assert load_tpex_dates_to_fetch(db_path) == []


@pytest.mark.parametrize("period_column", ["period", "year_month"])
def test_tpex_planner_filters_units_dates_and_preserves_settled(tmp_path: Path, period_column: str):
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    init_history_raw_ranges_table(db_path)
    checkpoints = [
        (MARKET_TWSE, "", "2011-06-01", "error"),
        (MARKET_TPEX, "27000", "2011-06-02", "partial"),
        (MARKET_TPEX, "", "2011-06", "partial"),
        (MARKET_TPEX, "", "2026-10-05", "error"),
        (MARKET_TPEX, "", "2006-12-31", "error"),
        (MARKET_TPEX, "", "2011-06-31", "partial"),
        (MARKET_TPEX, "", "20110603", "partial"),
        (MARKET_TPEX, "", "2011-W23-1", "error"),
        (MARKET_TPEX, "", "malformed", "error"),
        (MARKET_TPEX, "", "2011-06-04", "ok"),
        (MARKET_TPEX, "", "2011-06-05", "empty"),
        (MARKET_TPEX, "", "2011-06-06", "partial"),
        (MARKET_TPEX, "", "2011-06-07", "error"),
        (MARKET_TPEX, "27000", "2011-06-08", "ok"),
    ]
    with sqlite3.connect(db_path) as conn:
        if period_column == "year_month":
            conn.execute("ALTER TABLE official_history_raw_ranges RENAME COLUMN period TO year_month")
        conn.executemany(
            f"INSERT INTO official_history_raw_ranges (market, code, {period_column}, fetched_at, status, row_count) VALUES (?, ?, ?, 'x', ?, 0)", checkpoints
        )
        upsert_rows(
            conn,
            [
                _official_row(MARKET_TWSE, "9007", "2011-06-04"),
                _official_row(MARKET_TWSE, "9007", "2011-06-05"),
                _official_row(MARKET_TWSE, "9007", "2011-06-09"),
                _official_row(MARKET_TPEX, "27000", "2011-06-08"),
            ],
        )
    assert load_tpex_dates_to_fetch(db_path, today=date(2026, 10, 4)) == ["2011-06-09", "2011-06-08", "2011-06-07", "2011-06-06"]
    with sqlite3.connect(db_path) as conn:
        assert period_column in {row[1] for row in conn.execute("PRAGMA table_info(official_history_raw_ranges)")}
        assert conn.execute(f"SELECT market, code, {period_column}, status FROM official_history_raw_ranges").fetchall() == checkpoints


def test_tpex_planner_without_checkpoints_keeps_gaps_only(tmp_path: Path):
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        upsert_rows(conn, [_official_row(MARKET_TWSE, "9007", "2011-06-15"), _official_row(MARKET_TPEX, "27000", "2011-06-16")])
    assert load_tpex_dates_to_fetch(db_path) == ["2011-06-15"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'official_history_raw_ranges'").fetchone() is None


def test_an_empty_answer_does_not_settle_a_day_we_already_hold_rows_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # A re-fetch can legitimately run before the history page has published the day --
    # the daily backfill fires at 18:25. TPEx answers "not published yet" with a
    # successful empty, indistinguishable from anything else, so settling it would
    # strand the day on the bulk feed's odd-lot-inclusive figures forever. On today's
    # date. Every day. Which is exactly the case this selection exists to fix.
    monkeypatch.setattr(backfill, "fetch_tpex_market_day", lambda day, **_kw: {"stat": "ok", "tables": [{"fields": ["代號"], "data": []}]})
    monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    init_history_raw_ranges_table(db_path)
    with sqlite3.connect(db_path) as conn:
        upsert_rows(conn, [_official_row(MARKET_TPEX, "27000", "2026-08-07")])
        conn.commit()

    run_backfill(codes=[], start_year_month="2026-07", end_year_month="2026-07", tpex_dates=["2026-08-07"], db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        (status,) = conn.execute("SELECT status FROM official_history_raw_ranges WHERE market = ? AND period = '2026-08-07'", (MARKET_TPEX,)).fetchone()
    assert status == "partial", "an unpublished re-fetch must stay eligible, not settle"
    assert load_tpex_dates_to_fetch(db_path) == ["2026-08-07"]


def test_an_empty_answer_keeps_a_gap_day_retryable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(backfill, "fetch_tpex_market_day", lambda day, **_kw: {"stat": "ok", "tables": [{"fields": ["代號"], "data": []}]})
    monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    init_history_raw_ranges_table(db_path)
    with sqlite3.connect(db_path) as conn:
        upsert_rows(conn, [_official_row(MARKET_TWSE, "9007", "2026-08-07")])
        conn.commit()

    run_backfill(codes=[], start_year_month="2026-07", end_year_month="2026-07", tpex_dates=["2026-08-07"], db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        (status,) = conn.execute("SELECT status FROM official_history_raw_ranges WHERE market = ? AND period = '2026-08-07'", (MARKET_TPEX,)).fetchone()
    assert status == "partial"
    assert load_tpex_dates_to_fetch(db_path) == ["2026-08-07"]


def test_twse_days_prioritize_missing_both_exclude_settled_and_floor(tmp_path: Path):
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    init_history_raw_ranges_table(db_path)
    with sqlite3.connect(db_path) as conn:
        upsert_rows(
            conn,
            [_official_row(MARKET_TPEX, "1", "2010-01-04"), _official_row(MARKET_TPEX, "1", "2009-12-31"), _official_row(MARKET_TWSE, "9007", "2026-10-02")],
        )
        backfill.mark_unit_fetched(conn, CrawlUnit(MARKET_TWSE, "", "2026-10-01"), row_count=0)
    days = backfill.load_twse_dates_to_fetch(db_path, today=date(2026, 10, 4))
    assert "2026-09-30" in days
    assert "2010-01-04" in days
    assert "2009-12-31" not in days
    assert "2026-10-01" not in days
    assert "2026-10-03" not in days
    assert days.index("2026-09-30") < days.index("2026-10-02")


@pytest.mark.parametrize("period_column", ["period", "year_month"])
@pytest.mark.parametrize("status", ["partial", "error"])
def test_twse_old_checkpoint_without_prices_is_selected_and_resumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, period_column: str, status: str):
    from test_twse_market_day import FIXTURE

    db_path = tmp_path / "official.sqlite"
    day = "2011-06-15"
    today = date(2026, 10, 4)
    init_db(db_path)
    init_history_raw_ranges_table(db_path)
    with sqlite3.connect(db_path) as conn:
        if period_column == "year_month":
            conn.execute("ALTER TABLE official_history_raw_ranges RENAME COLUMN period TO year_month")
        conn.execute(
            f"INSERT INTO official_history_raw_ranges (market, code, {period_column}, fetched_at, status, row_count) VALUES (?, '', ?, 'x', ?, 0)",
            (MARKET_TWSE, day, status),
        )
        assert conn.execute("SELECT COUNT(*) FROM official_daily_price").fetchone() == (0,)

    days = backfill.load_twse_dates_to_fetch(db_path, today=today)
    assert day in days
    calls: list[str] = []

    def fetch(requested_day: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append(requested_day)
        return FIXTURE

    monkeypatch.setattr(backfill, "fetch_twse_market_day", fetch)
    stats = run_backfill(
        codes=[],
        start_year_month="2010-01",
        end_year_month="2010-01",
        twse_dates=[candidate for candidate in days if candidate == day],
        db_path=db_path,
        today=today,
        throttle_seconds=0,
    )
    assert calls == [day]
    assert stats == {"fetched": 1, "skipped_cached": 0, "failed": 0}
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT period, status FROM official_history_raw_ranges").fetchall() == [(day, "ok")]
        assert conn.execute("SELECT COUNT(*) FROM official_daily_price WHERE market = ? AND date = ?", (MARKET_TWSE, day)).fetchone()[0] > 0
    assert day not in backfill.load_twse_dates_to_fetch(db_path, today=today)


@pytest.mark.parametrize("period_column", ["period", "year_month"])
def test_twse_retry_candidates_exclude_other_units_invalid_days_and_settled(tmp_path: Path, period_column: str):
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    init_history_raw_ranges_table(db_path)
    excluded = [
        (MARKET_TPEX, "", "2011-06-01", "error"),
        (MARKET_TWSE, "9007", "2011-06-02", "partial"),
        (MARKET_TWSE, "", "2011-06", "error"),
        (MARKET_TWSE, "", "2026-10-05", "error"),
        (MARKET_TWSE, "", "2009-12-31", "partial"),
        (MARKET_TWSE, "", "2011-06-31", "error"),
        (MARKET_TWSE, "", "20110603", "partial"),
        (MARKET_TWSE, "", "2011-W23-1", "error"),
        (MARKET_TWSE, "", "malformed", "partial"),
        (MARKET_TWSE, "", "2011-06-04", "ok"),
        (MARKET_TWSE, "", "2011-06-05", "empty"),
    ]
    with sqlite3.connect(db_path) as conn:
        if period_column == "year_month":
            conn.execute("ALTER TABLE official_history_raw_ranges RENAME COLUMN period TO year_month")
        conn.executemany(
            f"INSERT INTO official_history_raw_ranges (market, code, {period_column}, fetched_at, status, row_count) VALUES (?, ?, ?, 'x', ?, 0)",
            [*excluded, (MARKET_TWSE, "", "2011-06-06", "partial"), (MARKET_TWSE, "", "2011-06-07", "error")],
        )
        upsert_rows(conn, [_official_row(MARKET_TWSE, "9007", "2026-10-02"), _official_row(MARKET_TPEX, "27000", "2011-06-04")])
    days = backfill.load_twse_dates_to_fetch(db_path, today=date(2026, 10, 4))
    assert all(period not in days for _market, _code, period, _status in excluded)
    assert days.index("2011-06-07") < days.index("2011-06-06") < days.index("2026-10-02")
    with sqlite3.connect(db_path) as conn:
        assert period_column in {row[1] for row in conn.execute("PRAGMA table_info(official_history_raw_ranges)")}


def test_twse_candidates_without_checkpoint_table_remain_readable(tmp_path: Path):
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        upsert_rows(conn, [_official_row(MARKET_TPEX, "27000", "2011-06-15")])
    assert "2011-06-15" in backfill.load_twse_dates_to_fetch(db_path, today=date(2026, 10, 4))
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'official_history_raw_ranges'").fetchone() is None


def test_month_universe_retains_checkpoints_but_not_old_day_only_codes(tmp_path: Path):
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    init_history_raw_ranges_table(db_path)
    with sqlite3.connect(db_path) as conn:
        upsert_rows(conn, [_official_row(MARKET_TWSE, "historical", "2011-06-15"), _official_row(MARKET_TWSE, "9007", "2026-10-02")])
        backfill.mark_unit_fetched(conn, CrawlUnit(MARKET_TWSE, "0050", "2011-06"), row_count=1)
        backfill.mark_unit_fetched(conn, CrawlUnit(MARKET_TWSE, "", "2011-06-15"), row_count=1)
    assert load_twse_backfill_universe(db_path) == ["0050", "9007"]


def test_universe_without_checkpoints_reads_newest_day_without_initializing(tmp_path: Path):
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        upsert_rows(conn, [_official_row(MARKET_TWSE, "historical", "2011-06-15"), _official_row(MARKET_TWSE, "9007", "2026-10-02")])
    assert load_twse_backfill_universe(db_path) == ["9007"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'official_history_raw_ranges'").fetchone() is None


@pytest.mark.parametrize(
    "day,existing,status", [("2026-10-02", False, "partial"), ("2026-09-27", False, "partial"), ("2026-09-26", False, "empty"), ("2011-06-15", True, "partial")]
)
def test_twse_empty_day_settlement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, day: str, existing: bool, status: str):
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    if existing:
        with sqlite3.connect(db_path) as conn:
            upsert_rows(conn, [_official_row(MARKET_TWSE, "9007", day)])
    monkeypatch.setattr(backfill, "fetch_twse_market_day", lambda *_args, **_kwargs: {"stat": "很抱歉，沒有符合條件的資料!"})
    run_backfill(codes=[], start_year_month="2010-01", end_year_month="2010-01", db_path=db_path, twse_dates=[day], today=date(2026, 10, 4), throttle_seconds=0)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT status FROM official_history_raw_ranges").fetchone()[0] == status


def test_twse_insert_only_and_day_before_month_and_same_run_tpex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    from test_twse_market_day import FIXTURE

    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    original = _official_row(MARKET_TWSE, "9008", "2011-06-15")
    with sqlite3.connect(db_path) as conn:
        upsert_rows(conn, [original])
    calls: list[str] = []

    def capture_twse(day: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append("twse")
        return FIXTURE

    def capture_tpex(day: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append("tpex:" + day)
        return {"tables": []}

    def capture_month(code: str, year_month: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append("month")
        return {"data": []}

    monkeypatch.setattr(backfill, "fetch_twse_market_day", capture_twse)
    monkeypatch.setattr(backfill, "fetch_tpex_market_day", capture_tpex)
    monkeypatch.setattr(backfill, "fetch_twse_symbol_month", capture_month)
    with caplog.at_level(logging.INFO, logger="backfill_official_history"):
        stats = run_backfill(
            codes=["9007"],
            start_year_month="2011-06",
            end_year_month="2011-06",
            twse_dates=["2011-06-15"],
            auto_tpex_dates=True,
            db_path=db_path,
            throttle_seconds=0,
        )
    assert calls == ["twse", "tpex:2011-06-15", "month"]
    assert stats["failed"] == 0
    assert "returned=1 inserted=0" in caplog.text
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT raw_fields_json, fetched_at FROM official_daily_price WHERE code='9008'").fetchone() == (
            original["raw_fields_json"],
            original["fetched_at"],
        )
        assert conn.execute("SELECT status FROM official_history_raw_ranges WHERE market='TWSE' AND code=''").fetchone() == ("ok",)


def test_new_twse_day_enables_tpex_in_same_run_without_preplanned_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from test_twse_market_day import FIXTURE

    calls: list[str] = []
    db_path = tmp_path / "official.sqlite"
    monkeypatch.setattr(backfill, "fetch_twse_market_day", lambda *_args, **_kwargs: FIXTURE)

    def capture_tpex(day: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append(day)
        if len(calls) == 2:
            return TPEX_FIXTURE_17_FIELD_CURRENT
        return {"tables": []}

    monkeypatch.setattr(backfill, "fetch_tpex_market_day", capture_tpex)
    run_backfill(
        codes=[], start_year_month="2010-01", end_year_month="2010-01", twse_dates=["2011-06-15"], auto_tpex_dates=True, db_path=db_path, throttle_seconds=0
    )
    assert calls == ["2011-06-15"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT name, volume FROM official_daily_price WHERE market='TWSE'").fetchone() == ("測試證券甲", 11000)
        assert conn.execute("SELECT status FROM official_history_raw_ranges WHERE market = ?", (MARKET_TPEX,)).fetchone() == ("partial",)
    result = run_backfill(codes=[], start_year_month="2010-01", end_year_month="2010-01", auto_tpex_dates=True, db_path=db_path, throttle_seconds=0)
    assert calls == ["2011-06-15", "2011-06-15"]
    assert result == {"fetched": 1, "skipped_cached": 0, "failed": 0}
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT status FROM official_history_raw_ranges WHERE market = ?", (MARKET_TPEX,)).fetchone() == ("ok",)
        assert conn.execute("SELECT COUNT(*) FROM official_daily_price WHERE market = ? AND date = '2011-06-15'", (MARKET_TPEX,)).fetchone()[0] > 0
    assert load_tpex_dates_to_fetch(db_path) == []


@pytest.mark.parametrize("market_flag", ["--twse-dates", "--tpex-dates"])
def test_main_explicit_market_dates_is_day_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, market_flag: str):
    captured: dict[str, Any] = {}

    def capture_run(**kwargs: Any) -> dict[str, int]:
        captured.update(kwargs)
        return {}

    monkeypatch.setattr(backfill, "run_backfill", capture_run)
    monkeypatch.setattr(backfill, "resolve_auto_crawl_plan", lambda *_args: pytest.fail("day-only must not plan months"))
    monkeypatch.setattr(sys, "argv", ["backfill_official_history.py", market_flag, "2011-06-15", "--db-path", str(tmp_path / "official.sqlite")])
    backfill.main()
    assert captured["codes"] == []
    assert captured["twse_dates"] == (["2011-06-15"] if market_flag == "--twse-dates" else [])
    assert captured["tpex_dates"] == (["2011-06-15"] if market_flag == "--tpex-dates" else [])
    assert captured["auto_tpex_dates"] is False
    assert captured["max_seconds"] == backfill.DEFAULT_AUTO_MAX_SECONDS


@pytest.mark.parametrize("market_flag", ["--twse-dates", "--tpex-dates"])
def test_main_day_only_respects_request_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, market_flag: str):
    captured: dict[str, Any] = {}

    def capture_run(**kwargs: Any) -> dict[str, int]:
        captured.update(kwargs)
        return {}

    monkeypatch.setattr(backfill, "run_backfill", capture_run)
    monkeypatch.setattr(backfill, "resolve_auto_crawl_plan", lambda *_args: pytest.fail("day-only must not plan months"))
    monkeypatch.setattr(backfill, "load_twse_dates_to_fetch", lambda *_args: pytest.fail("explicit day selection must not plan automatic TWSE days"))
    monkeypatch.setattr(
        sys, "argv", ["backfill_official_history.py", market_flag, "2026-08-19", "--max-requests", "1", "--db-path", str(tmp_path / "official.sqlite")]
    )
    backfill.main()
    assert captured["max_requests"] == 1
    assert captured["max_seconds"] is None
    assert captured["codes"] == []
    assert captured["twse_dates"] == (["2026-08-19"] if market_flag == "--twse-dates" else [])
    assert captured["tpex_dates"] == (["2026-08-19"] if market_flag == "--tpex-dates" else [])
    assert captured["auto_tpex_dates"] is False


def test_twse_dates_loaded_once_and_success_updates_membership(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from test_twse_market_day import FIXTURE

    db_path = tmp_path / "official.sqlite"
    queries: list[str] = []
    original_connect = backfill.connect

    def traced_connect(path: Path) -> sqlite3.Connection:
        conn = original_connect(path)
        conn.set_trace_callback(queries.append)
        return conn

    # Revisit a day within one invocation to prove the in-memory dates advance.
    # Force bypasses its first settled checkpoint; normal planning deduplicates it.
    unit = CrawlUnit(MARKET_TWSE, "", "2011-06-15")
    monkeypatch.setattr(backfill, "build_twse_day_crawl_units", lambda _dates: [unit, unit])
    payloads = iter([FIXTURE, {"stat": "很抱歉，沒有符合條件的資料!"}])
    monkeypatch.setattr(backfill, "fetch_twse_market_day", lambda *_args, **_kwargs: next(payloads))
    monkeypatch.setattr(backfill, "market_day_has_rows", lambda *_args: pytest.fail("TWSE day lookup must use the date set"))
    monkeypatch.setattr(backfill, "connect", traced_connect)
    stats = run_backfill(
        codes=[], start_year_month="2010-01", end_year_month="2010-01", twse_dates=[unit.period], db_path=db_path, force=True, throttle_seconds=0
    )
    assert stats == {"fetched": 2, "skipped_cached": 0, "failed": 0}
    day_queries = [query for query in queries if query.startswith("SELECT DISTINCT date FROM official_daily_price WHERE market =")]
    assert len(day_queries) == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT status FROM official_history_raw_ranges").fetchone() == ("partial",)


def test_twse_day_insert_failure_rolls_back_all_new_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from test_twse_market_day import FIXTURE

    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    original = _official_row(MARKET_TWSE, "9007", "2011-06-15")
    with sqlite3.connect(db_path) as conn:
        upsert_rows(conn, [original])

    real_insert_rows = backfill.insert_rows

    def fail_after_first_insert(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
        # The second row violates NOT NULL after executemany inserts the first.
        invalid = {**rows[0], "code": "invalid", "raw_fields_json": None}
        return real_insert_rows(conn, [rows[0], invalid])

    monkeypatch.setattr(backfill, "fetch_twse_market_day", lambda *_args, **_kwargs: FIXTURE)
    monkeypatch.setattr(backfill, "insert_rows", fail_after_first_insert)
    stats = run_backfill(codes=[], start_year_month="2010-01", end_year_month="2010-01", twse_dates=["2011-06-15"], db_path=db_path, throttle_seconds=0)
    assert stats == {"fetched": 0, "skipped_cached": 0, "failed": 1}
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT code, raw_fields_json, fetched_at FROM official_daily_price").fetchall() == [
            ("9007", original["raw_fields_json"], original["fetched_at"])
        ]
        assert conn.execute("SELECT status FROM official_history_raw_ranges").fetchone() == ("error",)


def test_lock_default_derives_from_database_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(backfill, "DEFAULT_LOCK_PATH", backfill._ORIGINAL_DEFAULT_LOCK_PATH)
    acquired: list[Path] = []
    monkeypatch.setattr(backfill, "acquire_lock", lambda path: acquired.append(path))
    run_backfill(codes=[], start_year_month="2010-01", end_year_month="2010-01", db_path=tmp_path / "scratch" / "official.sqlite")
    assert acquired == [tmp_path / "scratch" / ".official_backfill.lock"]


def test_failure_limit_checkpoints_preserves_rows_and_resumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    from test_twse_market_day import FIXTURE

    db_path = tmp_path / "official.sqlite"
    lock_path = tmp_path / "repair.lock"
    days = ["2011-06-13", "2011-06-14", "2011-06-15"]
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        upsert_rows(conn, [_official_row(MARKET_TWSE, "9007", days[0])])
        original = conn.execute("SELECT * FROM official_daily_price").fetchall()
    calls: list[str] = []
    sleeps: list[float] = []

    def fail(day: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append(day)
        raise RuntimeError("maintenance")

    monkeypatch.setattr(backfill, "fetch_twse_market_day", fail)
    monkeypatch.setattr(backfill.time, "sleep", sleeps.append)
    kwargs: dict[str, Any] = dict(
        codes=[],
        start_year_month="2010-01",
        end_year_month="2010-01",
        db_path=db_path,
        lock_path=lock_path,
        twse_dates=days,
        throttle_seconds=1,
        max_consecutive_failures=2,
    )
    with caplog.at_level(logging.INFO, logger="backfill_official_history"):
        stats = run_backfill(**kwargs)
    assert stats == {"fetched": 0, "skipped_cached": 0, "failed": 2}
    assert calls == days[:2]
    assert sleeps == [2]
    assert "reason=max_consecutive_failures reached (2)" in caplog.text
    assert not lock_path.exists()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT * FROM official_daily_price").fetchall() == original
        assert conn.execute("SELECT period, status FROM official_history_raw_ranges ORDER BY period").fetchall() == [(day, "error") for day in days[:2]]

    calls.clear()

    def healthy(day: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append(day)
        return {**FIXTURE, "date": day.replace("-", "")}

    monkeypatch.setattr(backfill, "fetch_twse_market_day", healthy)
    assert run_backfill(**kwargs) == {"fetched": 3, "skipped_cached": 0, "failed": 0}
    assert calls == days
    assert not lock_path.exists()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT * FROM official_daily_price WHERE code='9007'").fetchall() == original
        assert conn.execute("SELECT period, status FROM official_history_raw_ranges ORDER BY period").fetchall() == [(day, "ok") for day in days]


def test_success_resets_failure_streak_but_cached_unit_does_not(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    days = ["2011-06-13", "2011-06-14", "2011-06-15", "2011-06-16", "2011-06-17", "2011-06-20"]
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    init_history_raw_ranges_table(db_path)
    with sqlite3.connect(db_path) as conn:
        backfill.mark_unit_fetched(conn, CrawlUnit(MARKET_TWSE, "", days[4]), row_count=0)
    calls: list[str] = []

    def fetch(day: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append(day)
        if day == days[1]:
            return {"stat": "很抱歉，沒有符合條件的資料!"}
        raise RuntimeError("maintenance")

    monkeypatch.setattr(backfill, "fetch_twse_market_day", fetch)
    # First failure, success, then two failures stops before the final day.
    stats = run_backfill(
        codes=[],
        start_year_month="2010-01",
        end_year_month="2010-01",
        db_path=db_path,
        twse_dates=[days[0], days[1], days[2], days[4], days[3], days[5]],
        throttle_seconds=0,
        max_consecutive_failures=2,
    )
    assert calls == days[:4]
    assert stats == {"fetched": 1, "skipped_cached": 1, "failed": 3}


def test_omitted_failure_limit_continues_after_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []
    days = ["2011-06-13", "2011-06-14", "2011-06-15"]

    def fail(day: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append(day)
        raise RuntimeError("maintenance")

    monkeypatch.setattr(backfill, "fetch_twse_market_day", fail)
    stats = run_backfill(
        codes=[],
        start_year_month="2010-01",
        end_year_month="2010-01",
        db_path=tmp_path / "official.sqlite",
        twse_dates=days,
        throttle_seconds=0,
        max_requests=1,
    )
    assert calls == days
    assert stats == {"fetched": 0, "skipped_cached": 0, "failed": 3}


@pytest.mark.parametrize("limit", [0, -1])
def test_api_rejects_nonpositive_failure_limit_before_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: int):
    db_path = tmp_path / "new" / "official.sqlite"
    lock_path = tmp_path / "new" / "repair.lock"
    monkeypatch.setattr(backfill, "acquire_lock", lambda *_args: pytest.fail("invalid limit must not acquire lock"))
    monkeypatch.setattr(backfill, "init_db", lambda *_args: pytest.fail("invalid limit must not initialize DB"))
    with pytest.raises(ValueError, match="positive integer"):
        run_backfill(
            codes=[],
            start_year_month="2010-01",
            end_year_month="2010-01",
            db_path=db_path,
            lock_path=lock_path,
            max_consecutive_failures=limit,
        )
    assert not db_path.parent.exists()


@pytest.mark.parametrize("limit", [0, -1])
def test_cli_rejects_nonpositive_failure_limit_before_planning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: int):
    db_path = tmp_path / "new" / "official.sqlite"
    monkeypatch.setattr(backfill, "resolve_auto_crawl_plan", lambda *_args: pytest.fail("invalid limit must not plan"))
    monkeypatch.setattr(backfill, "run_backfill", lambda **_kwargs: pytest.fail("invalid limit must not run"))
    monkeypatch.setattr(sys, "argv", ["backfill_official_history.py", "--max-consecutive-failures", str(limit), "--db-path", str(db_path)])
    with pytest.raises(SystemExit) as exc:
        backfill.main()
    assert exc.value.code == 2
    assert not db_path.parent.exists()


@pytest.mark.parametrize("market_flag", ["--twse-dates", "--tpex-dates"])
def test_cli_forwards_failure_limit_in_day_only_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, market_flag: str):
    captured: dict[str, Any] = {}

    def capture(**kwargs: Any) -> dict[str, int]:
        captured.update(kwargs)
        return {}

    monkeypatch.setattr(backfill, "run_backfill", capture)
    monkeypatch.setattr(backfill, "resolve_auto_crawl_plan", lambda *_args: pytest.fail("day-only must not plan months"))
    monkeypatch.setattr(
        sys,
        "argv",
        ["backfill_official_history.py", market_flag, "2011-06-15", "--max-consecutive-failures", "3", "--db-path", str(tmp_path / "official.sqlite")],
    )
    backfill.main()
    assert captured["max_consecutive_failures"] == 3
    assert captured["codes"] == []
    assert captured["auto_tpex_dates"] is False
