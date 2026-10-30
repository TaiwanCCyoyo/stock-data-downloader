"""Planning queries must return exactly what the original full-scan implementations returned.

The ``_reference_*`` functions below are the pre-change queries, kept verbatim as the oracle.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import backfill_official_history as backfill
import pytest
from backfill_official_history import (
    MARKET_TPEX,
    MARKET_TWSE,
    TPEX_EARLIEST_DATE,
    CrawlUnit,
    build_crawl_units,
    init_history_raw_ranges_table,
    load_listing_months,
    load_tpex_dates_to_fetch,
    load_twse_backfill_universe,
    load_twse_dates_to_fetch,
    mark_unit_failed,
    mark_unit_fetched,
    run_backfill,
)
from fetch_official_daily_price import connect, init_db, upsert_rows

from shp_utils.twse_market_day import TWSE_EARLIEST_DATE

TODAY = date(2026, 10, 30)


@pytest.fixture(autouse=True)
def _isolated_default_lock_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backfill, "DEFAULT_LOCK_PATH", tmp_path / ".official_backfill.lock")


def _reference_load_twse_dates_to_fetch(db_path: Path, *, today: date | None = None) -> list[str]:
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


def _reference_load_twse_backfill_universe(db_path: Path) -> list[str]:
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


def _reference_load_tpex_dates_to_fetch(db_path: Path, *, today: date | None = None) -> list[str]:
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


def _row(market: str, code: str, day: str) -> dict[str, Any]:
    return {
        "market": market,
        "code": code,
        "date": day,
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


def _build_db(path: Path, *, checkpoints: bool) -> None:
    init_db(path)
    rows = []
    for day in ("2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29"):
        rows += [_row("TWSE", "1101", day), _row("TWSE", "2330", day)]
    # 9998 stops earlier, so it is not on the newest TWSE day.
    rows += [_row("TWSE", "9998", "2026-10-20"), _row("TWSE", "0050", "2026-10-29")]
    for day in ("2026-10-27", "2026-10-29", "2026-10-30"):
        rows += [_row("TPEx", "6488", day), _row("TPEx", "8069", day)]
    with connect(path) as conn:
        upsert_rows(conn, rows)
        conn.commit()
    if checkpoints:
        init_history_raw_ranges_table(path)
        with connect(path) as conn:
            mark_unit_fetched(conn, CrawlUnit(MARKET_TWSE, "1101", "2026-09"), row_count=5)
            mark_unit_fetched(conn, CrawlUnit(MARKET_TWSE, "5555", "2026-09"), row_count=0)
            mark_unit_fetched(conn, CrawlUnit(MARKET_TWSE, "", "2026-10-28"), row_count=9)
            mark_unit_fetched(conn, CrawlUnit(MARKET_TWSE, "", "2026-10-21"), row_count=0)
            mark_unit_failed(conn, CrawlUnit(MARKET_TWSE, "", "2026-10-22"))
            mark_unit_fetched(conn, CrawlUnit(MARKET_TWSE, "", "2026-10-30"), row_count=3, complete=False)
            mark_unit_fetched(conn, CrawlUnit(MARKET_TPEX, "", "2026-10-29"), row_count=40)
            mark_unit_fetched(conn, CrawlUnit(MARKET_TPEX, "", "2026-10-27"), row_count=0)
            mark_unit_failed(conn, CrawlUnit(MARKET_TPEX, "", "2026-10-26"))
            mark_unit_fetched(conn, CrawlUnit(MARKET_TPEX, "", "2026-10-23"), row_count=4, complete=False)
            conn.commit()


@pytest.fixture(params=[True, False], ids=["with-checkpoints", "no-checkpoint-table"])
def planning_db(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    path = tmp_path / "official_daily.sqlite"
    _build_db(path, checkpoints=request.param)
    return path


def test_twse_dates_match_the_original_query(planning_db: Path):
    result = load_twse_dates_to_fetch(planning_db, today=TODAY)

    assert result == _reference_load_twse_dates_to_fetch(planning_db, today=TODAY)
    assert result


def test_tpex_dates_match_the_original_query(planning_db: Path):
    result = load_tpex_dates_to_fetch(planning_db, today=TODAY)

    assert result == _reference_load_tpex_dates_to_fetch(planning_db, today=TODAY)
    assert result


def test_universe_matches_the_original_query(planning_db: Path):
    result = load_twse_backfill_universe(planning_db)

    assert result == _reference_load_twse_backfill_universe(planning_db)
    assert {"1101", "2330", "0050"} <= set(result)


@pytest.mark.parametrize("variant", ["empty", "twse-only", "tpex-only", "multi-code-ties"])
def test_planning_matches_the_original_on_edge_databases(tmp_path: Path, variant: str):
    path = tmp_path / "official_daily.sqlite"
    init_db(path)
    rows: list[dict[str, Any]] = []
    if variant == "twse-only":
        rows = [_row("TWSE", "2330", "2026-10-29")]
    elif variant == "tpex-only":
        rows = [_row("TPEx", "6488", "2026-10-29")]
    elif variant == "multi-code-ties":
        rows = [_row("TWSE", code, "2026-10-29") for code in ("00400A", "0050", "1101", "9958")] + [_row("TWSE", "1101", "2026-10-28")]
    if rows:
        with connect(path) as conn:
            upsert_rows(conn, rows)
            conn.commit()

    assert load_twse_dates_to_fetch(path, today=TODAY) == _reference_load_twse_dates_to_fetch(path, today=TODAY)
    assert load_tpex_dates_to_fetch(path, today=TODAY) == _reference_load_tpex_dates_to_fetch(path, today=TODAY)
    assert load_twse_backfill_universe(path) == _reference_load_twse_backfill_universe(path)


def test_planning_matches_the_original_on_a_longer_history(tmp_path: Path):
    path = tmp_path / "official_daily.sqlite"
    init_db(path)
    start = date(2026, 6, 1)
    days = [(start + timedelta(days=n)).isoformat() for n in range(120) if (start + timedelta(days=n)).weekday() < 5]
    rows = []
    for index, day in enumerate(days):
        rows += [_row("TWSE", "2330", day), _row("TWSE", f"{1100 + index % 7}", day)]
        if index % 3:
            rows.append(_row("TPEx", "6488", day))
    with connect(path) as conn:
        upsert_rows(conn, rows)
        conn.commit()
    init_history_raw_ranges_table(path)

    assert load_twse_dates_to_fetch(path, today=TODAY) == _reference_load_twse_dates_to_fetch(path, today=TODAY)
    assert load_tpex_dates_to_fetch(path, today=TODAY) == _reference_load_tpex_dates_to_fetch(path, today=TODAY)
    assert load_twse_backfill_universe(path) == _reference_load_twse_backfill_universe(path)


def _symbol_meta(path: Path, rows: list[tuple[str, str | None]]) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE symbol_meta (code TEXT PRIMARY KEY, name TEXT, listed_date TEXT, market TEXT NOT NULL)")
        conn.executemany("INSERT INTO symbol_meta (code, listed_date, market) VALUES (?, ?, 'TWSE')", rows)


def test_listing_months_reads_valid_dates_and_ignores_the_rest(tmp_path: Path):
    path = tmp_path / "symbol_meta.sqlite"
    _symbol_meta(path, [("009831", "2026-10-29"), ("2330", "1994-09-05"), ("1111", None), ("2222", ""), ("3333", "115/10/29"), ("4444", "2026-13-40")])

    assert load_listing_months(path) == {"009831": "2026-10", "2330": "1994-09"}


def test_listing_months_is_empty_without_a_usable_database(tmp_path: Path):
    assert load_listing_months(tmp_path / "absent.sqlite") == {}
    broken = tmp_path / "symbol_meta.sqlite"
    sqlite3.connect(broken).close()  # exists but has no symbol_meta table
    assert load_listing_months(broken) == {}


def test_listing_months_does_not_modify_the_database(tmp_path: Path):
    path = tmp_path / "symbol_meta.sqlite"
    _symbol_meta(path, [("009831", "2026-10-29")])
    before = path.read_bytes()

    load_listing_months(path)

    assert path.read_bytes() == before
    assert not list(tmp_path.glob("symbol_meta.sqlite-*"))


@pytest.mark.parametrize("newest_first", [False, True])
def test_units_start_at_the_listing_month(newest_first: bool):
    units = build_crawl_units(
        ["009831", "2330", "9999"], "2026-07", "2026-10", newest_first=newest_first, listing_months={"009831": "2026-09", "2330": "1994-09"}
    )

    by_code = {code: sorted(u.period for u in units if u.code == code) for code in ("009831", "2330", "9999")}
    assert by_code["009831"] == ["2026-09", "2026-10"]
    assert by_code["2330"] == ["2026-07", "2026-08", "2026-09", "2026-10"]
    assert by_code["9999"] == by_code["2330"], "a code missing from symbol_meta keeps the old behaviour"


def test_listing_floor_never_reaches_below_the_global_start():
    units = build_crawl_units(["2330"], "2010-01", "2010-03", listing_months={"2330": "1994-09"})

    assert [u.period for u in units] == ["2010-01", "2010-02", "2010-03"]


def test_a_code_listed_after_the_window_gets_no_units():
    assert build_crawl_units(["009831"], "2010-01", "2026-09", listing_months={"009831": "2026-10"}) == []


def test_run_backfill_logs_the_pre_listing_skip_and_requests_only_listed_months(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    fetched: list[tuple[str, str]] = []

    def fake_fetch(code: str, period: str, **_kwargs: Any) -> dict[str, Any]:
        fetched.append((code, period))
        return {"stat": "OK", "fields": [], "data": []}

    monkeypatch.setattr(backfill, "fetch_twse_symbol_month", fake_fetch)
    monkeypatch.setattr(backfill.time, "sleep", lambda _s: None)

    with caplog.at_level(logging.INFO):
        run_backfill(
            codes=["009831", "2330"],
            start_year_month="2026-07",
            end_year_month="2026-10",
            db_path=tmp_path / "official_daily.sqlite",
            throttle_seconds=0,
            today=date(2026, 11, 1),
            listing_months={"009831": "2026-10"},
        )

    assert sorted(fetched) == [("009831", "2026-10"), ("2330", "2026-07"), ("2330", "2026-08"), ("2330", "2026-09"), ("2330", "2026-10")]
    assert "auto plan: skipped 3 pre-listing month unit(s) across 1 code(s)" in caplog.text


def test_run_backfill_without_listing_months_logs_no_skip_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    monkeypatch.setattr(backfill, "fetch_twse_symbol_month", lambda *_a, **_k: {"stat": "OK", "fields": [], "data": []})
    monkeypatch.setattr(backfill.time, "sleep", lambda _s: None)

    with caplog.at_level(logging.INFO):
        run_backfill(
            codes=["2330"], start_year_month="2026-07", end_year_month="2026-07", db_path=tmp_path / "o.sqlite", throttle_seconds=0, today=date(2026, 11, 1)
        )

    assert "pre-listing" not in caplog.text


def test_main_auto_mode_reads_symbol_meta_beside_the_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "official_daily.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        upsert_rows(conn, [_row("TWSE", "009831", "2026-10-29")])
        conn.commit()
    _symbol_meta(tmp_path / "symbol_meta.sqlite", [("009831", "2026-10-29")])
    captured: dict[str, Any] = {}
    monkeypatch.setattr(backfill, "run_backfill", lambda **kwargs: captured.update(kwargs) or {"fetched": 0, "skipped_cached": 0, "failed": 0})
    monkeypatch.setattr(sys, "argv", ["backfill_official_history.py", "--db-path", str(db_path)])

    backfill.main()

    assert captured["listing_months"] == {"009831": "2026-10"}


def test_main_explicit_mode_does_not_apply_listing_floors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, Any] = {}
    monkeypatch.setattr(backfill, "run_backfill", lambda **kwargs: captured.update(kwargs) or {"fetched": 0, "skipped_cached": 0, "failed": 0})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backfill_official_history.py",
            "--codes",
            "009831",
            "--start-year-month",
            "2010-01",
            "--end-year-month",
            "2010-02",
            "--db-path",
            str(tmp_path / "o.sqlite"),
        ],
    )

    backfill.main()

    assert captured["listing_months"] is None
