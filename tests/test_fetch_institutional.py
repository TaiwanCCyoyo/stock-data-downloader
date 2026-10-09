"""Regression coverage for the institutional data pipeline."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

import fetch_institutional as institutional
import pytest

from shp_utils import institutional_schema as schema
from shp_utils.official_api import OfficialFetchError, UnexpectedResponseHostError


class _CollectionArguments(TypedDict):
    start_date: date
    end_date: date
    db_path: Path
    raw_dir: Path
    symbol_meta_path: Path
    throttle_seconds: float
    today: date
    markets: NotRequired[list[str]]
    retry_empty_since: NotRequired[date]
    trading_dates: NotRequired[set[date]]


REPORT_DATE = date(2026, 8, 7)
FETCHED_AT = "2026-08-07T10:00:00+00:00"


def _named_row(fields: tuple[str, ...], *, code: str = "2330", name: str = " 台積電 ", **values: str) -> list[str]:
    row = []
    for field in fields:
        if field in {"證券代號", "代號"}:
            row.append(code)
        elif field in {"證券名稱", "名稱"}:
            row.append(name)
        else:
            row.append(values.get(field, "0"))
    return row


@pytest.mark.parametrize(
    ("fields", "values", "expected"),
    [
        (
            schema.TWSE_FIELDS_A,
            {
                "外資買進股數": "1,000",
                "外資賣出股數": "400",
                "外資買賣超股數": "600",
                "自營商買進股數": "30",
                "自營商賣出股數": "10",
                "自營商買賣超股數": "20",
            },
            {"foreign_total_net": 600, "dealer_total_buy": 30, "dealer_self_net": None},
        ),
        (
            schema.TWSE_FIELDS_B,
            {
                "外資買賣超股數": "600",
                "自營商買賣超股數": "20",
                "自營商買賣超股數(自行買賣)": "15",
                "自營商買賣超股數(避險)": "5",
            },
            {"foreign_total_net": 600, "dealer_total_buy": None, "dealer_self_net": 15},
        ),
        (
            schema.TWSE_FIELDS_C,
            {
                "外陸資買進股數(不含外資自營商)": "1,000",
                "外陸資賣出股數(不含外資自營商)": "400",
                "外陸資買賣超股數(不含外資自營商)": "600",
                "外資自營商買進股數": "10",
                "外資自營商賣出股數": "5",
                "外資自營商買賣超股數": "5",
                "自營商買賣超股數(自行買賣)": "15",
            },
            {"foreign_total_net": 605, "foreign_dealer_net": 5, "dealer_self_net": 15},
        ),
    ],
)
def test_normalize_all_twse_schema_eras(fields: tuple[str, ...], values: dict[str, str], expected: dict[str, int | None]):
    payload = {"stat": "OK", "fields": list(fields), "data": [_named_row(fields, **values)]}

    rows, signature = institutional.normalize_twse_payload(payload, REPORT_DATE, instrument_types={"2330": "stock"}, fetched_at=FETCHED_AT)

    assert signature.startswith("twse-")
    assert rows[0]["name"] == "台積電"
    assert rows[0]["instrument_type"] == "stock"
    for key, value in expected.items():
        assert rows[0][key] == value


def test_normalize_tpex_old_schema_from_nonfixed_table_index():
    values = {
        "外資及陸資淨買股數": "600",
        "自營淨買股數": "20",
        "自營商(自行買賣)淨買股數": "15",
    }
    table = {"fields": list(schema.TPEX_FIELDS_OLD), "data": [_named_row(schema.TPEX_FIELDS_OLD, **values)]}
    payload = {
        "stat": "ok",
        "tables": [
            {"fields": list(schema.TPEX_FIELDS_CURRENT), "data": []},
            {},
            table,
        ],
    }

    rows, signature = institutional.normalize_tpex_payload(payload, REPORT_DATE, fetched_at=FETCHED_AT)

    assert signature == "tpex-16-old"
    assert rows[0]["foreign_total_net"] == 600
    assert rows[0]["dealer_total_buy"] is None
    assert rows[0]["dealer_total_net"] == 20
    assert rows[0]["dealer_self_net"] == 15


def test_normalize_tpex_current_schema_uses_csv_confirmed_group_order():
    raw_row = [
        "6488",
        "環球晶",
        "100",
        "40",
        "60",
        "10",
        "5",
        "5",
        "110",
        "45",
        "65",
        "20",
        "10",
        "10",
        "5",
        "3",
        "2",
        "7",
        "9",
        "-2",
        "12",
        "12",
        "0",
        "75",
    ]
    payload = {"stat": "ok", "tables": [{"fields": list(schema.TPEX_FIELDS_CURRENT), "data": [raw_row]}, {}]}

    rows, signature = institutional.normalize_tpex_payload(payload, REPORT_DATE, fetched_at=FETCHED_AT)

    row = rows[0]
    assert signature == "tpex-24-current"
    assert (row["foreign_excl_dealer_net"], row["foreign_dealer_net"], row["foreign_total_net"]) == (60, 5, 65)
    assert (row["dealer_self_net"], row["dealer_hedge_net"], row["dealer_total_net"]) == (2, -2, 0)
    assert row["three_institutions_net"] == 75


@pytest.mark.parametrize(
    ("normalizer", "payload"),
    [
        (institutional.normalize_twse_payload, {"stat": "OK", "fields": ["new"], "data": [["x"]]}),
        (
            institutional.normalize_tpex_payload,
            {"stat": "ok", "tables": [{"fields": ["new"], "data": [["x"]]}]},
        ),
    ],
)
def test_unknown_schema_signature_is_rejected(normalizer: Callable[..., tuple[list[dict[str, Any]], str]], payload: dict[str, Any]):
    with pytest.raises(ValueError, match="unknown .* institutional schema"):
        normalizer(payload, REPORT_DATE)


def _twse_payload(net: str = "600") -> dict[str, Any]:
    values = {"外資買賣超股數": net, "三大法人買賣超股數": net}
    return {
        "stat": "OK",
        "fields": list(schema.TWSE_FIELDS_A),
        "data": [_named_row(schema.TWSE_FIELDS_A, **values)],
    }


def test_collection_is_idempotent_and_preserves_raw_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "institutional.sqlite"
    raw_dir = tmp_path / "raw"
    calls: list[date] = []

    def fake_fetch(report_date: date, **_kwargs):
        calls.append(report_date)
        return _twse_payload(), "https://example.test/t86", "https://example.test/t86"

    monkeypatch.setattr(institutional, "fetch_twse_report", fake_fetch)
    common: _CollectionArguments = {
        "start_date": REPORT_DATE,
        "end_date": REPORT_DATE,
        "markets": [institutional.MARKET_TWSE],
        "db_path": db_path,
        "raw_dir": raw_dir,
        "symbol_meta_path": tmp_path / "missing.sqlite",
        "throttle_seconds": 0,
        "today": REPORT_DATE.replace(day=8),
    }

    first = institutional.run_collection(**common)
    second = institutional.run_collection(**common)

    assert first == {
        "attempted": 1,
        "fetched": 1,
        "skipped_cached": 0,
        "failed": 0,
        "rows": 1,
        "refused_to_erase": 0,
        "stopped_early": 0,
    }
    assert second["attempted"] == 0
    assert second["skipped_cached"] == 1
    assert calls == [REPORT_DATE]
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT instrument_type, foreign_total_net, published_at, fetched_at FROM institutional_daily").fetchone()
        log = conn.execute("SELECT status, raw_path, sha256 FROM institutional_fetch_log").fetchone()
        assert conn.execute("SELECT COUNT(*) FROM institutional_daily").fetchone()[0] == 1
    assert row[0:3] == ("unknown", 600, None)
    assert row[3]
    stored_raw_path = Path(log[1])
    raw_path = db_path.parent / stored_raw_path
    assert log[0] == "ok"
    assert not stored_raw_path.is_absolute()
    assert raw_path.is_file()
    assert hashlib.sha256(raw_path.read_bytes()).hexdigest() == log[2]


def test_force_refresh_updates_without_duplicate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "institutional.sqlite"
    raw_dir = tmp_path / "raw"
    payloads = iter([_twse_payload("600"), _twse_payload("700")])
    monkeypatch.setattr(
        institutional,
        "fetch_twse_report",
        lambda *_args, **_kwargs: (next(payloads), "https://example.test/t86", "https://example.test/t86"),
    )
    common: _CollectionArguments = {
        "start_date": REPORT_DATE,
        "end_date": REPORT_DATE,
        "markets": [institutional.MARKET_TWSE],
        "db_path": db_path,
        "raw_dir": raw_dir,
        "symbol_meta_path": tmp_path / "missing.sqlite",
        "throttle_seconds": 0,
        "today": REPORT_DATE.replace(day=8),
    }
    institutional.run_collection(**common)
    institutional.run_collection(**common, force=True)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM institutional_daily").fetchone()[0] == 1
        assert conn.execute("SELECT foreign_total_net FROM institutional_daily").fetchone()[0] == 700


def test_current_day_empty_response_remains_retryable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "institutional.sqlite"
    calls = 0
    empty = {"stat": "OK", "fields": list(schema.TWSE_FIELDS_C), "data": []}

    def fake_fetch(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return empty, "https://example.test/t86", "https://example.test/t86"

    monkeypatch.setattr(institutional, "fetch_twse_report", fake_fetch)
    common: _CollectionArguments = {
        "start_date": REPORT_DATE,
        "end_date": REPORT_DATE,
        "markets": [institutional.MARKET_TWSE],
        "db_path": db_path,
        "raw_dir": tmp_path / "raw",
        "symbol_meta_path": tmp_path / "missing.sqlite",
        "throttle_seconds": 0,
        "today": REPORT_DATE,
    }
    institutional.run_collection(**common)
    institutional.run_collection(**common)

    with sqlite3.connect(db_path) as conn:
        status = conn.execute("SELECT status FROM institutional_fetch_log").fetchone()[0]
    assert status == "partial"
    assert calls == 2


def test_recent_prior_day_empty_response_remains_retryable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "institutional.sqlite"
    calls = 0
    empty = {"stat": "OK", "fields": list(schema.TWSE_FIELDS_C), "data": []}

    def fake_fetch(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return empty, "https://example.test/t86", "https://example.test/t86"

    monkeypatch.setattr(institutional, "fetch_twse_report", fake_fetch)
    common: _CollectionArguments = {
        "start_date": REPORT_DATE,
        "end_date": REPORT_DATE,
        "markets": [institutional.MARKET_TWSE],
        "db_path": db_path,
        "raw_dir": tmp_path / "raw",
        "symbol_meta_path": tmp_path / "missing.sqlite",
        "throttle_seconds": 0,
        "today": REPORT_DATE + timedelta(days=1),
        "retry_empty_since": REPORT_DATE,
    }

    institutional.run_collection(**common)
    institutional.run_collection(**common)

    with sqlite3.connect(db_path) as conn:
        status = conn.execute("SELECT status FROM institutional_fetch_log").fetchone()[0]
    assert status == "partial"
    assert calls == 2


def test_daily_fetch_revisits_rolling_window(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, Any] = {}

    def fake_collection(**kwargs: Any) -> dict[str, int]:
        captured.update(kwargs)
        return {
            "attempted": 0,
            "fetched": 0,
            "skipped_cached": 14,
            "failed": 0,
            "rows": 0,
        }

    monkeypatch.setattr(institutional, "run_collection", fake_collection)

    # Explicitly calendar-less: the default reads the real price cache, and a
    # unit test must not depend on which days that file happens to contain.
    institutional.run_daily_fetch(target_date=REPORT_DATE, trading_dates=None)

    assert captured["start_date"] == date(2026, 8, 1)
    assert captured["end_date"] == REPORT_DATE
    assert captured["max_requests"] == 14
    assert captured["retry_empty_since"] == date(2026, 8, 1)
    assert captured["trading_dates"] is None


def test_daily_fetch_asks_only_about_days_the_market_opened(monkeypatch: pytest.MonkeyPatch):
    """The rolling window is seven calendar days, of which two are a weekend."""
    captured: dict[str, Any] = {}

    def fake_collection(**kwargs: Any) -> dict[str, int]:
        captured.update(kwargs)
        return {"attempted": 0, "fetched": 0, "skipped_cached": 0, "failed": 0, "rows": 0}

    monkeypatch.setattr(institutional, "run_collection", fake_collection)
    open_days = {date(2026, 8, 1) + timedelta(days=offset) for offset in range(7)} - {
        date(2026, 8, 1),
        date(2026, 8, 2),
    }

    institutional.run_daily_fetch(target_date=REPORT_DATE, trading_dates=open_days)

    assert captured["max_requests"] == 10, "five open days across two markets"
    assert captured["trading_dates"] == open_days


def test_daily_fetch_reads_the_calendar_when_the_caller_supplies_none(monkeypatch: pytest.MonkeyPatch):
    """run_daily.py calls this with no arguments, so the default has to load."""
    loaded: list[int] = []
    captured: dict[str, Any] = {}

    def fake_calendar() -> frozenset[date]:
        loaded.append(1)
        return frozenset({REPORT_DATE})

    monkeypatch.setattr(institutional, "load_trading_dates", fake_calendar)
    monkeypatch.setattr(
        institutional,
        "run_collection",
        lambda **kwargs: (captured.update(kwargs), {"attempted": 0, "fetched": 0, "skipped_cached": 0, "failed": 0, "rows": 0})[1],
    )

    institutional.run_daily_fetch(target_date=REPORT_DATE)

    assert loaded == [1]
    assert captured["max_requests"] == 2, "one open day across two markets"


def test_daily_fetch_rejects_nonpositive_lookback():
    with pytest.raises(ValueError, match="lookback_days"):
        institutional.run_daily_fetch(target_date=REPORT_DATE, lookback_days=0)


def test_load_instrument_types_classifies_and_falls_back(tmp_path: Path):
    meta_path = tmp_path / "symbol_meta.sqlite"
    with sqlite3.connect(meta_path) as conn:
        conn.execute("CREATE TABLE symbol_meta (code TEXT, is_etf INTEGER, security_category TEXT)")
        conn.executemany(
            "INSERT INTO symbol_meta VALUES (?, ?, ?)",
            [("2330", 0, "股票"), ("0050", 1, "ETF"), ("9101", 0, "存託憑證")],
        )

    types = institutional.load_instrument_types(meta_path)

    assert types == {"2330": "stock", "0050": "etf", "9101": "other"}
    assert institutional.load_instrument_types(tmp_path / "missing.sqlite") == {}


def _tpex_payload() -> dict[str, Any]:
    return {
        "tables": [
            {
                "fields": list(schema.TPEX_FIELDS_CURRENT),
                "data": [["8069", "元太", *(["10"] * 21), "10"]],
            }
        ]
    }


def _both_market_common(tmp_path: Path, db_path: Path) -> dict[str, Any]:
    return {
        "start_date": REPORT_DATE,
        "end_date": REPORT_DATE,
        "db_path": db_path,
        "raw_dir": tmp_path / "raw",
        "symbol_meta_path": tmp_path / "missing.sqlite",
        "throttle_seconds": 0,
        "today": REPORT_DATE.replace(day=8),
    }


def _log_rows(db_path: Path) -> dict[str, tuple[str, int, str | None]]:
    with sqlite3.connect(db_path) as conn:
        return {
            str(market): (str(status), int(row_count), error_detail)
            for market, status, row_count, error_detail in conn.execute("SELECT market, status, row_count, error_detail FROM institutional_fetch_log")
        }


def _log_units(db_path: Path) -> dict[tuple[str, str], tuple[str, int, str | None]]:
    """As :func:`_log_rows`, but keyed by the full ``(market, date)`` unit, for
    the tests that span several dates."""
    with sqlite3.connect(db_path) as conn:
        return {
            (str(market), str(report_date)): (str(status), int(row_count), error_detail)
            for market, report_date, status, row_count, error_detail in conn.execute(
                "SELECT market, date, status, row_count, error_detail FROM institutional_fetch_log"
            )
        }


def test_failing_market_is_checkpointed_as_error_without_holding_back_the_other(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A TPEx outage must not cost the TWSE report for the same day.

    This is the failure that stalled the 2019-2023 backfill: one market failing
    left no record at all, and the calling batch could only start over.
    """
    db_path = tmp_path / "institutional.sqlite"
    tpex_calls = 0

    def failing_tpex(*_args, **_kwargs):
        nonlocal tpex_calls
        tpex_calls += 1
        raise OfficialFetchError("TPEx said no", status_code=520, attempts=5)

    monkeypatch.setattr(
        institutional,
        "fetch_twse_report",
        lambda *_args, **_kwargs: (_twse_payload(), "https://example.test/t86", "https://example.test/t86"),
    )
    monkeypatch.setattr(institutional, "fetch_tpex_report", failing_tpex)
    common = _both_market_common(tmp_path, db_path)

    first = institutional.run_collection(**common)
    assert first["fetched"] == 1
    assert first["failed"] == 1

    logs = _log_rows(db_path)
    assert logs["TWSE"][0] == "ok"
    assert logs["TPEx"][0] == "error"
    assert logs["TPEx"][1] == 0
    assert "520" in (logs["TPEx"][2] or "") or "OfficialFetchError" in (logs["TPEx"][2] or "")

    monkeypatch.setattr(
        institutional,
        "fetch_tpex_report",
        lambda *_args, **_kwargs: (_tpex_payload(), "https://example.test/tpex", "https://example.test/tpex"),
    )
    second = institutional.run_collection(**common)

    assert second["attempted"] == 1, "the settled TWSE unit must not be re-fetched"
    assert second["skipped_cached"] == 1
    assert tpex_calls == 1
    assert _log_rows(db_path)["TPEx"][0] == "ok"


def test_error_checkpoint_never_demotes_a_settled_unit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "institutional.sqlite"
    monkeypatch.setattr(
        institutional,
        "fetch_twse_report",
        lambda *_args, **_kwargs: (_twse_payload(), "https://example.test/t86", "https://example.test/t86"),
    )
    common = _both_market_common(tmp_path, db_path)
    common["markets"] = [institutional.MARKET_TWSE]
    institutional.run_collection(**common)

    def failing(*_args, **_kwargs):
        raise OfficialFetchError("transient", status_code=503, attempts=3)

    monkeypatch.setattr(institutional, "fetch_twse_report", failing)
    institutional.run_collection(**common, force=True)

    logs = _log_rows(db_path)
    assert logs["TWSE"][0] == "ok"
    assert logs["TWSE"][1] == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM institutional_daily").fetchone()[0] == 1


def test_historical_empty_response_on_a_trading_day_stays_retryable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An empty body is the endpoint saying nothing, not the market saying zero."""
    db_path = tmp_path / "institutional.sqlite"
    empty = {"stat": "OK", "fields": list(schema.TWSE_FIELDS_C), "data": []}
    monkeypatch.setattr(
        institutional,
        "fetch_twse_report",
        lambda *_args, **_kwargs: (empty, "https://example.test/t86", "https://example.test/t86"),
    )
    common = _both_market_common(tmp_path, db_path)
    common["markets"] = [institutional.MARKET_TWSE]
    common["today"] = REPORT_DATE + timedelta(days=400)
    common["trading_dates"] = {REPORT_DATE}

    institutional.run_collection(**common)
    second = institutional.run_collection(**common)

    assert _log_rows(db_path)["TWSE"][0] == "partial"
    assert second["attempted"] == 1, "a trading day that answered empty is still owed"


def test_historical_empty_response_off_the_calendar_settles_as_empty():
    """A date the calendar does not contain is settled, not owed.

    This is asserted against the classifier rather than through
    ``run_collection``, because a calendar now also decides which units exist:
    a date outside it is never requested, so the end-to-end path cannot reach
    this branch. It stays live for a caller driving :func:`fetch_unit` directly,
    and for the calendar-less path below.
    """
    assert (
        institutional._empty_response_status(
            REPORT_DATE,
            retry_empty_since=REPORT_DATE + timedelta(days=400),
            trading_dates={REPORT_DATE + timedelta(days=1)},
        )
        == "empty"
    )


def test_old_empty_response_settles_when_no_calendar_was_supplied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Without a calendar the cutoff is the only thing separating "no report
    exists" from "not published yet", so an old date settles and stops costing
    a request on every subsequent run."""
    db_path = tmp_path / "institutional.sqlite"
    empty = {"stat": "OK", "fields": list(schema.TWSE_FIELDS_C), "data": []}
    monkeypatch.setattr(
        institutional,
        "fetch_twse_report",
        lambda *_args, **_kwargs: (empty, "https://example.test/t86", "https://example.test/t86"),
    )
    common = _both_market_common(tmp_path, db_path)
    common["markets"] = [institutional.MARKET_TWSE]
    common["today"] = REPORT_DATE + timedelta(days=400)
    common["retry_empty_since"] = REPORT_DATE + timedelta(days=400)

    institutional.run_collection(**common)
    second = institutional.run_collection(**common)

    assert _log_rows(db_path)["TWSE"][0] == "empty"
    assert second["skipped_cached"] == 1


def test_collection_stops_after_consecutive_failures_instead_of_hammering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "institutional.sqlite"
    attempts = 0

    def failing(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise OfficialFetchError("still down", status_code=520, attempts=5)

    monkeypatch.setattr(institutional, "fetch_twse_report", failing)
    monkeypatch.setattr(institutional, "fetch_tpex_report", failing)
    stats = institutional.run_collection(
        start_date=REPORT_DATE,
        end_date=REPORT_DATE + timedelta(days=10),
        db_path=db_path,
        raw_dir=tmp_path / "raw",
        symbol_meta_path=tmp_path / "missing.sqlite",
        throttle_seconds=0,
        today=REPORT_DATE + timedelta(days=400),
        stop_after_consecutive_failures=3,
    )

    # Units interleave TWSE, TPEx within each date and the streaks are counted
    # per market, so TWSE reaches three failures on the third date, by which
    # point TPEx has failed twice: five attempts, not three.
    assert attempts == 5
    assert stats["stopped_early"] == 1
    assert stats["failed"] == 5


_PRE_MIGRATION_FETCH_LOG = """
    CREATE TABLE institutional_fetch_log (
        market TEXT NOT NULL, date TEXT NOT NULL, source_url TEXT NOT NULL,
        status TEXT NOT NULL, row_count INTEGER NOT NULL, schema_signature TEXT NOT NULL,
        published_at TEXT, fetched_at TEXT NOT NULL, raw_path TEXT NOT NULL,
        sha256 TEXT NOT NULL, PRIMARY KEY (market, date)
    )
"""


def test_init_db_adds_error_detail_to_an_existing_artifact_without_touching_rows(tmp_path: Path):
    """The migration's first real run is against caches that cannot be re-acquired.

    Every other test builds a fresh database, where the column arrives with the
    CREATE TABLE, so this is the only place the ALTER TABLE branch executes.
    """
    db_path = tmp_path / "institutional.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(_PRE_MIGRATION_FETCH_LOG)
        conn.execute(
            "INSERT INTO institutional_fetch_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("TWSE", "2019-01-02", "https://example.test/t86", "ok", 1080, "twse-19-c", None, "2026-08-31T05:00:00+00:00", "raw/twse/x.json", "deadbeef"),
        )
    with sqlite3.connect(db_path) as conn:
        before = conn.execute("SELECT * FROM institutional_fetch_log").fetchall()

    institutional.init_db(db_path)
    institutional.init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(institutional_fetch_log)")]
        after = conn.execute(
            "SELECT market, date, source_url, status, row_count, schema_signature, published_at, fetched_at, raw_path, sha256 FROM institutional_fetch_log"
        ).fetchall()
        error_details = conn.execute("SELECT error_detail FROM institutional_fetch_log").fetchall()

    assert columns.count("error_detail") == 1, "the migration must be idempotent"
    assert after == before
    assert error_details == [(None,)]


def test_unparsable_response_checkpoints_the_snapshot_that_explains_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A fail-closed unknown schema must leave the stored response reachable."""
    db_path = tmp_path / "institutional.sqlite"
    unknown = {"stat": "OK", "fields": ["代號", "誰知道"], "data": [["2330", "1"]]}
    monkeypatch.setattr(
        institutional,
        "fetch_twse_report",
        lambda *_args, **_kwargs: (unknown, "https://example.test/t86", "https://example.test/t86"),
    )
    common = _both_market_common(tmp_path, db_path)
    common["markets"] = [institutional.MARKET_TWSE]

    stats = institutional.run_collection(**common)

    assert stats["failed"] == 1
    with sqlite3.connect(db_path) as conn:
        status, raw_path, sha256, detail = conn.execute("SELECT status, raw_path, sha256, error_detail FROM institutional_fetch_log").fetchone()
    assert status == "error"
    assert sha256
    snapshot = db_path.parent / raw_path
    assert snapshot.is_file()
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == sha256
    assert "could not be parsed" in detail


def _twse_ok_tpex_down(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """TWSE answers, TPEx 520s -- the shape the 2019-2023 backfill actually hit."""
    calls: dict[str, list] = {"TWSE": [], "TPEx": []}

    def twse(report_date: date, **_kwargs: Any):
        calls["TWSE"].append(report_date)
        return _twse_payload(), "https://example.test/t86", "https://example.test/t86"

    def tpex(report_date: date, **_kwargs: Any):
        calls["TPEx"].append(report_date)
        raise OfficialFetchError("edge proxy said no", status_code=520, attempts=5)

    monkeypatch.setattr(institutional, "fetch_twse_report", twse)
    monkeypatch.setattr(institutional, "fetch_tpex_report", tpex)
    return calls


def test_one_market_failing_stops_the_run_even_while_the_other_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A healthy market must not clear the streak of a failing one.

    With a single global counter, TWSE succeeding on every date reset the count
    before the next TPEx failure could accumulate, so TPEx could fail on every
    date of a multi-year range and never trip the stop.
    """
    db_path = tmp_path / "institutional.sqlite"
    calls = _twse_ok_tpex_down(monkeypatch)
    dates = [REPORT_DATE + timedelta(days=offset) for offset in range(3)]

    stats = institutional.run_collection(
        start_date=dates[0],
        end_date=dates[-1],
        db_path=db_path,
        raw_dir=tmp_path / "raw",
        symbol_meta_path=tmp_path / "missing.sqlite",
        throttle_seconds=0,
        today=REPORT_DATE + timedelta(days=400),
        trading_dates=set(dates),
        stop_after_consecutive_failures=2,
    )

    assert stats["stopped_early"] == 1
    assert stats["failed"] == 2
    assert calls["TPEx"] == dates[:2], "must stop on the second TPEx failure, not carry on to day three"
    assert calls["TWSE"] == dates[:2]
    rows = _log_units(db_path)
    assert rows[("TWSE", dates[0].isoformat())][0] == "ok", "the healthy market keeps its data"
    assert rows[("TPEx", dates[0].isoformat())][0] == "error"
    assert ("TWSE", dates[2].isoformat()) not in rows


def test_cooldown_follows_the_failing_market_not_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The cooldown counts the same per-market streak the stop does."""
    db_path = tmp_path / "institutional.sqlite"
    _twse_ok_tpex_down(monkeypatch)
    slept: list[float] = []
    monkeypatch.setattr(institutional.time, "sleep", lambda seconds: slept.append(seconds))
    dates = [REPORT_DATE + timedelta(days=offset) for offset in range(3)]

    institutional.run_collection(
        start_date=dates[0],
        end_date=dates[-1],
        db_path=db_path,
        raw_dir=tmp_path / "raw",
        symbol_meta_path=tmp_path / "missing.sqlite",
        throttle_seconds=0,
        today=REPORT_DATE + timedelta(days=400),
        trading_dates=set(dates),
        cooldown_seconds=600.0,
        cooldown_after_failures=2,
    )

    assert slept == [600.0, 600.0], "cooldown from the second TPEx failure on, despite TWSE succeeding between them"


def test_rerun_retries_only_the_market_that_never_settled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Resume must re-ask the failed market and leave the settled one alone."""
    db_path = tmp_path / "institutional.sqlite"
    dates = [REPORT_DATE + timedelta(days=offset) for offset in range(2)]
    common: _CollectionArguments = {
        "start_date": dates[0],
        "end_date": dates[-1],
        "db_path": db_path,
        "raw_dir": tmp_path / "raw",
        "symbol_meta_path": tmp_path / "missing.sqlite",
        "throttle_seconds": 0,
        "today": REPORT_DATE + timedelta(days=400),
        "trading_dates": set(dates),
    }
    _twse_ok_tpex_down(monkeypatch)
    institutional.run_collection(**common)

    calls: list[tuple[str, Any]] = []

    def twse(report_date: date, **_kwargs: Any):
        calls.append(("TWSE", report_date))
        return _twse_payload(), "https://example.test/t86", "https://example.test/t86"

    def tpex(report_date: date, **_kwargs: Any):
        calls.append(("TPEx", report_date))
        return _tpex_payload(), "https://example.test/tpex", "https://example.test/tpex"

    monkeypatch.setattr(institutional, "fetch_twse_report", twse)
    monkeypatch.setattr(institutional, "fetch_tpex_report", tpex)
    stats = institutional.run_collection(**common)

    assert calls == [("TPEx", dates[0]), ("TPEx", dates[1])], "settled TWSE units must not be re-requested"
    assert stats["attempted"] == 2
    assert stats["skipped_cached"] == 2
    assert stats["failed"] == 0
    assert {row[0] for row in _log_units(db_path).values()} == {"ok"}


def test_units_are_not_built_for_days_outside_the_trading_calendar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Weekends were requested every day and answered with a successful empty
    body, so they sat at ``partial`` forever and were asked again tomorrow."""
    db_path = tmp_path / "institutional.sqlite"
    calls: list[Any] = []

    def twse(report_date: date, **_kwargs: Any):
        calls.append(report_date)
        return _twse_payload(), "https://example.test/t86", "https://example.test/t86"

    monkeypatch.setattr(institutional, "fetch_twse_report", twse)
    dates = [REPORT_DATE + timedelta(days=offset) for offset in range(5)]
    open_days = {dates[0], dates[3]}

    institutional.run_collection(
        start_date=dates[0],
        end_date=dates[-1],
        markets=[institutional.MARKET_TWSE],
        db_path=db_path,
        raw_dir=tmp_path / "raw",
        symbol_meta_path=tmp_path / "missing.sqlite",
        throttle_seconds=0,
        today=REPORT_DATE + timedelta(days=400),
        trading_dates=open_days,
    )

    assert calls == sorted(open_days)
    assert set(_log_units(db_path)) == {("TWSE", day.isoformat()) for day in open_days}


def test_no_calendar_still_asks_for_every_day():
    """A caller whose calendar failed to load must not silently fetch nothing
    and report success, so omitting it keeps the every-day behaviour."""
    units = institutional.build_units(REPORT_DATE, REPORT_DATE + timedelta(days=4), [institutional.MARKET_TWSE])
    assert len(units) == 5


def test_a_response_from_another_host_is_never_recorded_as_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The requested URL proves intent; only the response URL proves origin."""
    db_path = tmp_path / "institutional.sqlite"

    def redirected(*_args, **_kwargs):
        raise UnexpectedResponseHostError(
            "response for www.twse.com.tw came back with a different host",
            requested_url="https://www.twse.com.tw/rwd/zh/fund/T86?date=20190102",
            final_url="https://mirror.example.net/rwd/zh/fund/T86?date=20190102",
        )

    monkeypatch.setattr(institutional, "fetch_twse_report", redirected)
    common = _both_market_common(tmp_path, db_path)
    common["markets"] = [institutional.MARKET_TWSE]

    stats = institutional.run_collection(**common)

    assert stats["failed"] == 1
    assert stats["rows"] == 0
    with sqlite3.connect(db_path) as conn:
        status, source_url, final_url = conn.execute("SELECT status, source_url, final_url FROM institutional_fetch_log").fetchone()
        assert conn.execute("SELECT count(*) FROM institutional_daily").fetchone()[0] == 0
    assert status == "error"
    assert source_url == "https://www.twse.com.tw/rwd/zh/fund/T86?date=20190102"
    assert final_url == "https://mirror.example.net/rwd/zh/fund/T86?date=20190102"


def test_a_successful_fetch_records_the_url_the_response_came_from(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "institutional.sqlite"
    monkeypatch.setattr(
        institutional,
        "fetch_twse_report",
        lambda *_args, **_kwargs: (
            _twse_payload(),
            "https://www.twse.com.tw/rwd/zh/fund/T86?date=20190102",
            "https://www.twse.com.tw/rwd/zh/fund/T86?date=20190102&response=json",
        ),
    )
    common = _both_market_common(tmp_path, db_path)
    common["markets"] = [institutional.MARKET_TWSE]

    institutional.run_collection(**common)

    with sqlite3.connect(db_path) as conn:
        status, final_url = conn.execute("SELECT status, final_url FROM institutional_fetch_log").fetchone()
    assert status == "ok"
    assert final_url.endswith("&response=json")


def test_daily_window_drops_days_the_market_was_closed():
    """Four requests a day were being spent on Saturday and Sunday, which the
    endpoints answer with a successful empty body."""
    monday = date(2026, 8, 24)
    friday = monday + timedelta(days=4)
    calendar = {monday + timedelta(days=offset) for offset in range(5)}
    calendar |= {monday + timedelta(days=7)}

    window = institutional._daily_window(monday, monday + timedelta(days=6), calendar)
    assert window is not None

    assert window == calendar - {monday + timedelta(days=7)}
    assert friday in window
    assert monday + timedelta(days=5) not in window


def test_daily_window_keeps_days_newer_than_the_calendar_knows_about():
    """The price cache is rebuilt earlier in the same pipeline run and can lag.
    A stale calendar must not be able to suppress today."""
    monday = date(2026, 8, 24)
    calendar = {monday, monday + timedelta(days=1)}

    window = institutional._daily_window(monday, monday + timedelta(days=3), calendar)
    assert window is not None

    assert monday + timedelta(days=2) in window, "newer than the calendar, so still asked for"
    assert monday + timedelta(days=3) in window


def test_daily_window_without_a_calendar_asks_for_everything():
    monday = date(2026, 8, 24)
    assert institutional._daily_window(monday, monday + timedelta(days=3), None) is None
    assert institutional._daily_window(monday, monday + timedelta(days=3), set()) is None


def test_missing_price_cache_yields_no_calendar_rather_than_an_error(tmp_path: Path):
    """A calendar that cannot be read degrades into redundant requests, never
    into fetching nothing and reporting success."""
    assert institutional.load_trading_dates(tmp_path / "absent.parquet") is None


def test_a_transport_failure_still_records_which_url_was_asked_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The 520 is the failure the error checkpoint exists to explain, and it
    carries no response, so nothing supplied a URL and the checkpoint stored an
    empty one."""
    db_path = tmp_path / "institutional.sqlite"

    def down(*_args: Any, **_kwargs: Any) -> None:
        raise OfficialFetchError("edge proxy said no", status_code=520, attempts=5)

    monkeypatch.setattr(institutional, "fetch_json_with_retry_url", down)
    common = _both_market_common(tmp_path, db_path)
    common["markets"] = [institutional.MARKET_TWSE]

    stats = institutional.run_collection(**common)

    assert stats["failed"] == 1
    with sqlite3.connect(db_path) as conn:
        status, source_url, final_url = conn.execute("SELECT status, source_url, final_url FROM institutional_fetch_log").fetchone()
    assert status == "error"
    assert source_url.startswith("https://www.twse.com.tw/rwd/zh/fund/T86?")
    assert "selectType=ALLBUT0999" in source_url
    assert final_url == "", "there was no response, so nothing came back from anywhere"


def _legacy_success(db_path: Path, *, final_url: str | None, status: str = "ok", row_count: int = 1080) -> None:
    """A fetch log built before `final_url` existed, then migrated.

    The migration only adds the column, so every pre-existing success comes out
    of it with no value in that column -- which is the whole reason the cached
    check cannot be a status test alone.
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute(_PRE_MIGRATION_FETCH_LOG)
        conn.execute(
            "INSERT INTO institutional_fetch_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "TWSE",
                REPORT_DATE.isoformat(),
                "https://www.twse.com.tw/rwd/zh/fund/T86?date=20190102",
                status,
                row_count,
                "twse-19-c",
                None,
                "2026-08-31T05:00:00+00:00",
                "raw/twse/x.json",
                "deadbeef",
            ),
        )
    institutional.init_db(db_path)
    if final_url is not None:
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE institutional_fetch_log SET final_url = ?", (final_url,))


@pytest.mark.parametrize("final_url", [None, "", "   ", "\t"])
def test_a_success_without_response_provenance_is_still_owed(tmp_path: Path, final_url: str | None):
    """A migrated `ok` row proves only what was requested, not what answered.

    Re-fetching is the only honest way to fill that in: copying `source_url`
    across would manufacture exactly the provenance the host check exists to
    establish.
    """
    db_path = tmp_path / "institutional.sqlite"
    _legacy_success(db_path, final_url=final_url)

    with institutional.connect(db_path) as conn:
        cached = institutional.is_unit_cached(conn, institutional.FetchUnit("TWSE", REPORT_DATE))

    assert cached is False


def test_a_success_with_response_provenance_is_settled(tmp_path: Path):
    db_path = tmp_path / "institutional.sqlite"
    _legacy_success(db_path, final_url="https://www.twse.com.tw/rwd/zh/fund/T86?date=20190102")

    with institutional.connect(db_path) as conn:
        cached = institutional.is_unit_cached(conn, institutional.FetchUnit("TWSE", REPORT_DATE))

    assert cached is True


def test_an_empty_day_settles_without_provenance(tmp_path: Path):
    """A non-trading day has no response body to have provenance about."""
    db_path = tmp_path / "institutional.sqlite"
    _legacy_success(db_path, final_url=None, status="empty", row_count=0)

    with institutional.connect(db_path) as conn:
        cached = institutional.is_unit_cached(conn, institutional.FetchUnit("TWSE", REPORT_DATE))

    assert cached is True


def test_refetching_a_legacy_success_records_both_urls_and_a_recomputable_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The re-fetch writes the real response URL, not a presumed one."""
    db_path = tmp_path / "institutional.sqlite"
    _legacy_success(db_path, final_url=None)
    requested = "https://www.twse.com.tw/rwd/zh/fund/T86?date=20190102"
    answered = "https://www.twse.com.tw/rwd/zh/fund/T86?date=20190102&response=json"
    monkeypatch.setattr(
        institutional,
        "fetch_twse_report",
        lambda *_args, **_kwargs: (_twse_payload(), requested, answered),
    )
    common = _both_market_common(tmp_path, db_path)
    common["markets"] = [institutional.MARKET_TWSE]

    stats = institutional.run_collection(**common)

    assert stats["attempted"] == 1, "the legacy row must not be skipped as cached"
    assert stats["skipped_cached"] == 0
    with sqlite3.connect(db_path) as conn:
        status, source_url, final_url, raw_path, sha256 = conn.execute(
            "SELECT status, source_url, final_url, raw_path, sha256 FROM institutional_fetch_log"
        ).fetchone()
    assert status == "ok"
    assert source_url == requested
    assert final_url == answered
    assert final_url != source_url, "the fixture must not let a copy of the requested URL pass"
    snapshot = db_path.parent / raw_path
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == sha256


def test_a_failed_refetch_does_not_demote_a_row_that_still_holds_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Retryable is not the same as unprotected.

    A legacy success is owed a re-fetch, but if that re-fetch fails the stored
    row count must not be overwritten with an error checkpoint claiming zero --
    the rows are still in `institutional_daily`, and a checkpoint disagreeing
    with them is worse than a missing `final_url`.
    """
    db_path = tmp_path / "institutional.sqlite"
    _legacy_success(db_path, final_url=None)
    monkeypatch.setattr(
        institutional,
        "fetch_twse_report",
        _raise(OfficialFetchError("edge proxy said no", status_code=520, attempts=5)),
    )
    common = _both_market_common(tmp_path, db_path)
    common["markets"] = [institutional.MARKET_TWSE]

    stats = institutional.run_collection(**common)

    assert stats["failed"] == 1
    with sqlite3.connect(db_path) as conn:
        status, row_count = conn.execute("SELECT status, row_count FROM institutional_fetch_log").fetchone()
    assert status == "ok"
    assert row_count == 1080


def _raise(error: BaseException):
    def stub(*_args: Any, **_kwargs: Any):
        raise error

    return stub


# --- an explicitly pinned calendar ----------------------------------------


def _write_calendar_parquet(path: Path, days: list[date]) -> None:
    import pandas as pd

    pd.DataFrame({"Date": [pd.Timestamp(day) for day in days]}).to_parquet(path, index=False)


def test_an_explicitly_supplied_calendar_builds_only_its_own_dates(tmp_path: Path):
    """A study pins a frozen artifact so acquisition matches what it preregistered."""
    calendar_path = tmp_path / "frozen.parquet"
    open_days = [REPORT_DATE, REPORT_DATE + timedelta(days=3)]
    _write_calendar_parquet(calendar_path, open_days)

    loaded = institutional.load_trading_dates(calendar_path, required=True)
    units = institutional.build_units(
        REPORT_DATE,
        REPORT_DATE + timedelta(days=4),
        [institutional.MARKET_TWSE],
        trading_dates=loaded,
    )

    assert loaded == frozenset(open_days)
    assert [unit.report_date for unit in units] == open_days


def test_a_pinned_calendar_that_cannot_be_read_fails_instead_of_widening(tmp_path: Path):
    """Falling back to every calendar day would quietly acquire against a
    different calendar than the one that was pinned."""
    with pytest.raises(ValueError, match="could not read the trading calendar"):
        institutional.load_trading_dates(tmp_path / "absent.parquet", required=True)


def test_a_pinned_calendar_with_no_dates_fails_instead_of_widening(tmp_path: Path):
    calendar_path = tmp_path / "empty.parquet"
    _write_calendar_parquet(calendar_path, [])

    with pytest.raises(ValueError, match="empty"):
        institutional.load_trading_dates(calendar_path, required=True)


def test_the_daily_default_still_degrades_to_asking_about_every_day(tmp_path: Path):
    """Only the explicit parameter fails closed; the daily run must not."""
    assert institutional.load_trading_dates(tmp_path / "absent.parquet") is None


def test_the_backfill_calendar_selector_honours_each_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The three reachable cases. The fourth -- both flags at once -- cannot
    reach this function, because the parser rejects the pair."""
    calendar_path = tmp_path / "frozen.parquet"
    _write_calendar_parquet(calendar_path, [REPORT_DATE])
    monkeypatch.setattr(institutional, "load_trading_dates", _tracking_default(frozenset({REPORT_DATE + timedelta(days=1)})))

    assert institutional._backfill_calendar(all_calendar_days=True, trading_calendar_path=None) is None
    assert institutional._backfill_calendar(all_calendar_days=False, trading_calendar_path=None) == frozenset({REPORT_DATE + timedelta(days=1)})


def _tracking_default(default_days: frozenset[date]):
    """Stands in for the canonical-cache read, while letting a named path load
    for real so the required=True contract is exercised end to end."""
    real = institutional.load_trading_dates

    def stub(path: Path = institutional.DEFAULT_PRICE_PARQUET_PATH, *, required: bool = False):
        if required:
            return real(path, required=True)
        return default_days

    return stub


def test_a_named_calendar_reaches_the_selector_and_still_fails_closed(tmp_path: Path):
    with pytest.raises(ValueError, match="could not read the trading calendar"):
        institutional._backfill_calendar(all_calendar_days=False, trading_calendar_path=tmp_path / "absent.parquet")


def test_a_named_calendar_is_what_the_selector_returns(tmp_path: Path):
    calendar_path = tmp_path / "frozen.parquet"
    open_days = [REPORT_DATE, REPORT_DATE + timedelta(days=3)]
    _write_calendar_parquet(calendar_path, open_days)

    selected = institutional._backfill_calendar(all_calendar_days=False, trading_calendar_path=calendar_path)

    assert selected == frozenset(open_days)


def test_the_two_calendar_flags_cannot_be_given_together(monkeypatch: pytest.MonkeyPatch):
    """Two opposite intents about the same thing; the parser rejects the pair
    rather than letting one silently win."""
    argv = [
        "fetch_institutional.py",
        "backfill",
        "--start-date",
        "2019-01-02",
        "--end-date",
        "2019-01-02",
        "--all-calendar-days",
        "--trading-calendar-path",
        "frozen.parquet",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as excinfo:
        institutional.main()

    assert excinfo.value.code == 2


def test_a_fresh_artifact_records_its_schema_version(tmp_path: Path):
    db_path = tmp_path / "institutional.sqlite"
    institutional.init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        assert institutional.read_schema_version(conn) == institutional.SCHEMA_VERSION


def test_an_artifact_from_before_the_meta_table_reports_no_version(tmp_path: Path):
    """Not an error. Every artifact built before 2026-09-03 is in this state
    until init_db next runs against it."""
    db_path = tmp_path / "institutional.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(_PRE_MIGRATION_FETCH_LOG)

    with sqlite3.connect(db_path) as conn:
        assert institutional.read_schema_version(conn) is None


def test_migrating_an_old_artifact_stamps_it_without_touching_rows(tmp_path: Path):
    db_path = tmp_path / "institutional.sqlite"
    _legacy_success(db_path, final_url=None)

    with sqlite3.connect(db_path) as conn:
        version = institutional.read_schema_version(conn)
        status, row_count, source_url = conn.execute("SELECT status, row_count, source_url FROM institutional_fetch_log").fetchone()

    assert version == institutional.SCHEMA_VERSION
    assert (status, row_count) == ("ok", 1080)
    assert source_url == "https://www.twse.com.tw/rwd/zh/fund/T86?date=20190102"


def test_stamping_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "institutional.sqlite"
    institutional.init_db(db_path)
    institutional.init_db(db_path)
    institutional.init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT key, value FROM schema_meta").fetchall()

    assert rows == [(institutional.SCHEMA_VERSION_KEY, str(institutional.SCHEMA_VERSION))]


def test_a_repeat_init_db_does_not_touch_the_stamp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`init_db` runs on every daily fetch and every backfill invocation.

    Re-stamping `updated_at` when nothing migrated would dirty a page and change
    the file's bytes on every run with no schema change -- and a consumer
    freezing a snapshot hashes this file.
    """
    db_path = tmp_path / "institutional.sqlite"
    institutional.init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        first = conn.execute("SELECT updated_at FROM schema_meta").fetchone()[0]

    # A later clock must not be able to move the stamp on its own.
    monkeypatch.setattr(institutional, "_utc_now", lambda **_kwargs: "2099-01-01T00:00:00+00:00")
    institutional.init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT updated_at FROM schema_meta").fetchone()[0] == first


class _FailingAfterProbe:
    """A connection whose table probe succeeds and whose read then fails.

    Stands in for "database is locked" or a disk error, which are the other
    things `OperationalError` covers besides a missing table.
    """

    def __init__(self, error: Exception):
        self.error = error

    def execute(self, sql: str, *_args: Any):
        if "sqlite_master" in sql:
            return _OneRow((1,))
        raise self.error


class _OneRow:
    def __init__(self, row: tuple):
        self.row = row

    def fetchone(self) -> tuple:
        return self.row


def test_a_read_failure_is_not_reported_as_having_no_version():
    """Swallowing every `OperationalError` as "predates the meta table" would
    let a manifest record a version 3 artifact as having none."""
    conn = cast(sqlite3.Connection, _FailingAfterProbe(sqlite3.OperationalError("database is locked")))

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        institutional.read_schema_version(conn)


def test_the_version_stamp_is_not_an_assurance_about_row_content(tmp_path: Path):
    """The stamp describes columns. A migrated artifact carries the current
    version and still holds successes written before `final_url` existed, so a
    consumer that read the version as "every row has provenance" would be
    wrong -- which is why `is_unit_cached` asks per row.
    """
    db_path = tmp_path / "institutional.sqlite"
    _legacy_success(db_path, final_url=None)

    with institutional.connect(db_path) as conn:
        version = institutional.read_schema_version(conn)
        cached = institutional.is_unit_cached(conn, institutional.FetchUnit("TWSE", REPORT_DATE))
        stored_final_url = conn.execute("SELECT final_url FROM institutional_fetch_log").fetchone()[0]

    assert version == institutional.SCHEMA_VERSION
    assert stored_final_url is None
    assert cached is False, "current schema version, and this row is still owed"


def _seed_populated_unit(
    tmp_path: Path,
    db_path: Path,
    *,
    final_url: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """One TWSE unit fetched for real, then optionally stripped of its final_url
    to stand in for an artifact built before that column existed."""
    monkeypatch.setattr(
        institutional,
        "fetch_twse_report",
        lambda *_args, **_kwargs: (
            _twse_payload(),
            "https://www.twse.com.tw/rwd/zh/fund/T86?date=20190102",
            "https://www.twse.com.tw/rwd/zh/fund/T86?date=20190102&response=json",
        ),
    )
    common = _both_market_common(tmp_path, db_path)
    common["markets"] = [institutional.MARKET_TWSE]
    institutional.run_collection(**common)
    if final_url is None:
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE institutional_fetch_log SET final_url = NULL")
    return common


def _unit_state(db_path: Path) -> tuple[list, tuple]:
    with sqlite3.connect(db_path) as conn:
        daily = conn.execute("SELECT * FROM institutional_daily ORDER BY code").fetchall()
        log = conn.execute(
            "SELECT market, date, source_url, final_url, status, row_count, schema_signature, fetched_at, raw_path, sha256 FROM institutional_fetch_log"
        ).fetchone()
    return daily, log


def test_an_empty_response_never_erases_rows_that_are_already_stored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The re-fetch of a legacy success is the reachable path for this.

    `record_failure`'s guard does not cover it: the response succeeded, so no
    exception occurred and that path never ran. The write is a delete-then-
    insert, so zero rows meant deleting every stored row for the unit.
    """
    db_path = tmp_path / "institutional.sqlite"
    common = _seed_populated_unit(tmp_path, db_path, final_url=None, monkeypatch=monkeypatch)
    before_daily, before_log = _unit_state(db_path)
    assert len(before_daily) > 0 and before_log[3] is None

    empty = {"stat": "OK", "fields": list(schema.TWSE_FIELDS_C), "data": []}
    monkeypatch.setattr(
        institutional,
        "fetch_twse_report",
        lambda *_args, **_kwargs: (
            empty,
            "https://www.twse.com.tw/rwd/zh/fund/T86?date=20190102",
            "https://www.twse.com.tw/rwd/zh/fund/T86?date=20190102&response=json",
        ),
    )
    stats = institutional.run_collection(**common)

    after_daily, after_log = _unit_state(db_path)
    assert stats["attempted"] == 1, "the legacy success is owed, so it is re-fetched"
    assert stats["failed"] == 1, "and the empty answer is a failure, not a completion"
    assert stats["rows"] == 0
    assert after_daily == before_daily, "every stored row survives unchanged"
    assert after_log == before_log, "status, row_count and source metadata all unchanged"
    with institutional.connect(db_path) as conn:
        assert institutional.is_unit_cached(conn, institutional.FetchUnit("TWSE", REPORT_DATE)) is False


def test_the_empty_response_is_kept_on_disk_for_diagnosis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Refusing to store it must not mean throwing it away."""
    db_path = tmp_path / "institutional.sqlite"
    common = _seed_populated_unit(tmp_path, db_path, final_url=None, monkeypatch=monkeypatch)
    _, before_log = _unit_state(db_path)
    kept_before = {path for path in (tmp_path / "raw").rglob("*.json")}

    empty = {"stat": "OK", "fields": list(schema.TWSE_FIELDS_C), "data": []}
    monkeypatch.setattr(
        institutional,
        "fetch_twse_report",
        lambda *_args, **_kwargs: (empty, "https://example.test/t86", "https://example.test/t86"),
    )
    institutional.run_collection(**common)

    kept_after = {path for path in (tmp_path / "raw").rglob("*.json")}
    added = kept_after - kept_before
    assert len(added) == 1, "the empty response is written before parsing and stays there"
    payload = json.loads(added.pop().read_text(encoding="utf-8"))
    assert payload["data"] == []


def test_force_cannot_let_an_empty_response_demote_a_current_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """force skips the cache check, not the protection."""
    db_path = tmp_path / "institutional.sqlite"
    common = _seed_populated_unit(
        tmp_path,
        db_path,
        final_url="https://www.twse.com.tw/rwd/zh/fund/T86?date=20190102&response=json",
        monkeypatch=monkeypatch,
    )
    before_daily, before_log = _unit_state(db_path)
    assert before_log[4] == "ok" and before_log[5] > 0

    empty = {"stat": "OK", "fields": list(schema.TWSE_FIELDS_C), "data": []}
    monkeypatch.setattr(
        institutional,
        "fetch_twse_report",
        lambda *_args, **_kwargs: (empty, "https://example.test/t86", "https://example.test/t86"),
    )
    stats = institutional.run_collection(**common, force=True)

    after_daily, after_log = _unit_state(db_path)
    assert stats["failed"] == 1
    assert after_daily == before_daily
    assert after_log == before_log


def test_an_empty_response_still_settles_a_unit_that_holds_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The guard protects stored rows; it must not block the ordinary case of a
    day that legitimately has no report and never had one."""
    db_path = tmp_path / "institutional.sqlite"
    empty = {"stat": "OK", "fields": list(schema.TWSE_FIELDS_C), "data": []}
    monkeypatch.setattr(
        institutional,
        "fetch_twse_report",
        lambda *_args, **_kwargs: (empty, "https://example.test/t86", "https://example.test/t86"),
    )
    common = _both_market_common(tmp_path, db_path)
    common["markets"] = [institutional.MARKET_TWSE]
    common["today"] = REPORT_DATE + timedelta(days=400)
    common["retry_empty_since"] = REPORT_DATE + timedelta(days=400)

    stats = institutional.run_collection(**common)

    assert stats["failed"] == 0
    assert _log_rows(db_path)["TWSE"][0] == "empty"


def test_a_refusal_is_counted_apart_from_a_transport_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Tomorrow's log has to say which kind of failure happened.

    A run that protected stored data and a run that lost a request to a 520
    both land in `failed`, and `institutional_daily_fetch_counts` is what an
    operator reads. One bucket cannot tell them apart.
    """
    db_path = tmp_path / "institutional.sqlite"
    common = _seed_populated_unit(tmp_path, db_path, final_url=None, monkeypatch=monkeypatch)

    empty = {"stat": "OK", "fields": list(schema.TWSE_FIELDS_C), "data": []}
    monkeypatch.setattr(
        institutional,
        "fetch_twse_report",
        lambda *_args, **_kwargs: (empty, "https://example.test/t86", "https://example.test/t86"),
    )
    refused = institutional.run_collection(**common)

    assert refused["failed"] == 1
    assert refused["refused_to_erase"] == 1

    monkeypatch.setattr(
        institutional,
        "fetch_twse_report",
        _raise(OfficialFetchError("edge proxy said no", status_code=520, attempts=5)),
    )
    transport = institutional.run_collection(**common)

    assert transport["failed"] == 1
    assert transport["refused_to_erase"] == 0, "a 520 is not a refusal"
