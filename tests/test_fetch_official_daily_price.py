"""Offline parser coverage with self-authored synthetic financial values.
Schema and dates exercise validation; rows are not acquired market observations.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import fetch_official_daily_price as official
import pytest
from fetch_official_daily_price import (
    connect,
    init_db,
    insert_rows,
    normalize_tpex_bulk_payload,
    normalize_twse_bulk_payload,
    run_fetch,
    upsert_rows,
)

# Self-authored synthetic MI_INDEX values with the expected schema.
MI_INDEX_FIXTURE: dict[str, Any] = {
    "stat": "OK",
    "date": "20261002",
    "tables": [
        {
            "fields": [
                "證券代號",
                "證券名稱",
                "成交股數",
                "成交筆數",
                "成交金額",
                "開盤價",
                "最高價",
                "最低價",
                "收盤價",
                "漲跌(+/-)",
                "漲跌價差",
                "最後揭示買價",
                "最後揭示買量",
                "最後揭示賣價",
                "最後揭示賣量",
                "本益比",
            ],
            "data": [
                [
                    "00400A",
                    "測試基金甲",
                    "11,000",
                    "12,000",
                    "13,000",
                    "106.00",
                    "105.00",
                    "106.00",
                    "109.00",
                    "<p style= color:red>+</p>",
                    "108.00",
                    "109.00",
                    "45",
                    "110.00",
                    "80",
                    "0.00",
                ],
                [
                    "00401A",
                    "測試基金乙",
                    "21,000",
                    "22,000",
                    "23,000",
                    "114.00",
                    "115.00",
                    "116.00",
                    "120.00",
                    "<p style= color:red>+</p>",
                    "118.00",
                    "119.00",
                    "488",
                    "120.00",
                    "4",
                    "0.00",
                ],
            ],
        }
    ],
}

# TWSE STOCK_DAY_ALL: a normal row and a synthetic zero-volume/zero-close day (no "---" here).
TWSE_BULK_FIXTURE: list[dict[str, Any]] = [
    {
        "Date": "1150722",
        "Code": "9007",
        "Name": "測試證券乙",
        "TradeVolume": "31000",
        "TradeValue": "32000",
        "OpeningPrice": "123.00",
        "HighestPrice": "124.00",
        "LowestPrice": "125.00",
        "ClosingPrice": "126.00",
        "Change": "-127.0000",
        "Transaction": "38000",
    },
    {
        "Date": "1150722",
        "Code": "020011",
        "Name": "元大民國20年期",
        "TradeVolume": "0",
        "TradeValue": "0",
        "OpeningPrice": "0.00",
        "HighestPrice": "0.00",
        "LowestPrice": "0.00",
        "ClosingPrice": "0.00",
        "Change": "0.0000",
        "Transaction": "0",
    },
]

# TPEx bulk: a normal row, a "---" close with a tiny nonzero trade, and a fully
# no-trade "---" row with zero shares.
TPEX_BULK_FIXTURE: list[dict[str, Any]] = [
    {
        "Date": "1150723",
        "SecuritiesCompanyCode": "006201",
        "CompanyName": "測試基金丙",
        "Close": "129.00",
        "Change": "-130.00",
        "Open": "131.00",
        "High": "132.00",
        "Low": "133.00",
        "TradingShares": "44000",
        "TransactionAmount": "45000",
        "TransactionNumber": "159",
    },
    {
        "Date": "1150723",
        "SecuritiesCompanyCode": "00734B",
        "CompanyName": "測試債券乙",
        "Close": " ---",
        "Change": "--- ",
        "Open": "---",
        "High": "---",
        "Low": "---",
        "TradingShares": "146",
        "TransactionAmount": "46000",
        "TransactionNumber": "1",
    },
    {
        "Date": "1150723",
        "SecuritiesCompanyCode": "020001",
        "CompanyName": "元大新興公司債",
        "Close": " ---",
        "Change": "--- ",
        "Open": "---",
        "High": "---",
        "Low": "---",
        "TradingShares": "0",
        "TransactionAmount": "0",
        "TransactionNumber": "0",
    },
]


def test_normalize_twse_bulk_payload_parses_normal_row():
    rows = normalize_twse_bulk_payload(TWSE_BULK_FIXTURE)
    row = next(r for r in rows if r["code"] == "9007")
    assert row["market"] == "TWSE"
    assert row["date"] == "2026-07-22"
    assert row["close"] == 126.0
    assert row["volume"] == 31000
    assert row["close_missing"] is False


def test_normalize_twse_bulk_payload_treats_zero_volume_day_as_close_missing():
    # TWSE reports "0.00" (not "---") on a no-trade day, but a no-trade day has no real
    # closing price in either market -- must not be stored as a literal 0.0 close, or the
    # official-vs-Shioaji cross-check would flag every TWSE no-trade day as a huge gap.
    rows = normalize_twse_bulk_payload(TWSE_BULK_FIXTURE)
    row = next(r for r in rows if r["code"] == "020011")
    assert row["close"] is None
    assert row["volume"] == 0
    assert row["close_missing"] is True


def test_normalize_tpex_bulk_payload_parses_normal_row():
    rows = normalize_tpex_bulk_payload(TPEX_BULK_FIXTURE)
    row = next(r for r in rows if r["code"] == "006201")
    assert row["market"] == "TPEx"
    assert row["date"] == "2026-07-23"
    assert row["close"] == 129.0
    assert row["volume"] == 44000


def test_normalize_tpex_bulk_payload_flags_dash_close_as_missing_even_with_trades():
    rows = normalize_tpex_bulk_payload(TPEX_BULK_FIXTURE)
    row = next(r for r in rows if r["code"] == "00734B")
    assert row["close"] is None
    assert row["close_missing"] is True
    assert row["volume"] == 146


def test_normalize_tpex_bulk_payload_flags_dash_close_on_zero_volume_day():
    rows = normalize_tpex_bulk_payload(TPEX_BULK_FIXTURE)
    row = next(r for r in rows if r["code"] == "020001")
    assert row["close"] is None
    assert row["close_missing"] is True
    assert row["volume"] == 0


def test_normalize_skips_row_with_blank_code():
    payload = [{**TWSE_BULK_FIXTURE[0], "Code": ""}]
    assert normalize_twse_bulk_payload(payload) == []


def test_upsert_rows_is_idempotent_by_market_code_date(tmp_path: Path):
    db_path = tmp_path / "official_daily.sqlite"
    init_db(db_path)
    rows = normalize_twse_bulk_payload(TWSE_BULK_FIXTURE)
    with sqlite3.connect(db_path) as conn:
        upsert_rows(conn, rows)
        upsert_rows(conn, rows)
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM official_daily_price").fetchone()[0]
    assert count == len(rows)


def test_upsert_rows_updates_existing_row_on_conflict(tmp_path: Path):
    db_path = tmp_path / "official_daily.sqlite"
    init_db(db_path)
    rows = normalize_twse_bulk_payload(TWSE_BULK_FIXTURE)
    updated_rows = [dict(row) for row in rows]
    for row in updated_rows:
        if row["code"] == "9007":
            row["close"] = 2500.0
    with sqlite3.connect(db_path) as conn:
        upsert_rows(conn, rows)
        upsert_rows(conn, updated_rows)
        conn.commit()
        close = conn.execute(
            "SELECT close FROM official_daily_price WHERE code = ? AND date = ?",
            ("9007", "2026-07-22"),
        ).fetchone()[0]
    assert close == 2500.0


def test_init_db_enables_wal_journal_mode(tmp_path: Path):
    # A long-running backfill and run_daily.py's own fetch both write this database;
    # under the default rollback-journal mode a writer blocks readers too, which can
    # fail run_daily.py's steps mid-backfill. WAL lets readers proceed concurrently.
    db_path = tmp_path / "official_daily.sqlite"
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_connect_sets_busy_timeout(tmp_path: Path):
    # busy_timeout is a per-connection setting (unlike journal_mode, it is not
    # persisted in the database file), so every connection -- not just init_db's --
    # must set it, or a brief lock collision fails immediately instead of queueing.
    db_path = tmp_path / "official_daily.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        timeout_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert timeout_ms == official.DEFAULT_BUSY_TIMEOUT_MS


def test_run_fetch_persists_rows_from_both_markets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(official, "fetch_twse_bulk", lambda **_kwargs: TWSE_BULK_FIXTURE)
    monkeypatch.setattr(official, "fetch_tpex_bulk", lambda **_kwargs: TPEX_BULK_FIXTURE)
    monkeypatch.setattr(official, "fetch_twse_market_day", lambda *_args, **_kwargs: {"stat": "很抱歉，沒有符合條件的資料!"})
    db_path = tmp_path / "official_daily.sqlite"

    counts = run_fetch(db_path=db_path)

    assert counts == {"TWSE": 2, "TPEx": 3, "TWSE_supplement": 0}
    with sqlite3.connect(db_path) as conn:
        markets = {row[0] for row in conn.execute("SELECT DISTINCT market FROM official_daily_price")}
    assert markets == {"TWSE", "TPEx"}


def test_run_fetch_isolates_one_market_failure_from_the_other(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def raise_error(**_kwargs):
        raise RuntimeError("TWSE endpoint unavailable")

    monkeypatch.setattr(official, "fetch_twse_bulk", raise_error)
    monkeypatch.setattr(official, "fetch_tpex_bulk", lambda **_kwargs: TPEX_BULK_FIXTURE)
    monkeypatch.setattr(official, "fetch_twse_market_day", lambda *_args, **_kwargs: {"stat": "很抱歉，沒有符合條件的資料!"})
    db_path = tmp_path / "official_daily.sqlite"

    counts = run_fetch(db_path=db_path)

    assert counts["TWSE"] == 0
    assert counts["TPEx"] == 3
    with sqlite3.connect(db_path) as conn:
        tpex_count = conn.execute("SELECT COUNT(*) FROM official_daily_price WHERE market = 'TPEx'").fetchone()[0]
    assert tpex_count == 3


def test_run_fetch_warns_when_the_two_markets_report_different_days(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    # Neither bulk endpoint takes a date, so a lagging feed looks like a healthy
    # fetch. The other market is the only cheap witness: on 2026-08-19 TWSE was
    # still serving 2026-08-18 while TPEx had moved on, and that day went
    # missing silently.
    stale_twse = [dict(row, Date="1150818") for row in TWSE_BULK_FIXTURE]
    monkeypatch.setattr(official, "fetch_twse_bulk", lambda **_kwargs: stale_twse)
    monkeypatch.setattr(official, "fetch_tpex_bulk", lambda **_kwargs: TPEX_BULK_FIXTURE)

    with caplog.at_level("WARNING"):
        run_fetch(db_path=tmp_path / "official.sqlite")

    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert any("disagree on the trading day" in message for message in warnings)
    assert any("TWSE=2026-08-18" in message for message in warnings)


def test_run_fetch_stays_quiet_when_both_markets_report_the_same_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    # A holiday leaves both feeds on the same stale day. That is normal and must
    # not warn, or the signal would be lost in weekly noise.
    aligned_tpex = [dict(row, Date="1150722") for row in TPEX_BULK_FIXTURE]
    monkeypatch.setattr(official, "fetch_twse_bulk", lambda **_kwargs: TWSE_BULK_FIXTURE)
    monkeypatch.setattr(official, "fetch_tpex_bulk", lambda **_kwargs: aligned_tpex)

    with caplog.at_level("WARNING"):
        run_fetch(db_path=tmp_path / "official.sqlite")

    assert not [record for record in caplog.records if record.levelname == "WARNING"]


def test_run_fetch_does_not_warn_when_only_one_market_was_fetched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    # One market failing is already reported by its own exception log; with no
    # second date to compare against there is nothing to say about staleness.
    def raise_error(**_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(official, "fetch_twse_bulk", raise_error)
    monkeypatch.setattr(official, "fetch_tpex_bulk", lambda **_kwargs: TPEX_BULK_FIXTURE)
    monkeypatch.setattr(official, "fetch_twse_market_day", lambda *_args, **_kwargs: {"stat": "很抱歉，沒有符合條件的資料!"})

    with caplog.at_level("WARNING"):
        run_fetch(db_path=tmp_path / "official.sqlite")

    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert not any("disagree on the trading day" in message for message in warnings)


def test_insert_rows_counts_absent_rows_and_preserves_existing_provenance(tmp_path: Path):
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    rows = normalize_twse_bulk_payload(TWSE_BULK_FIXTURE)
    changed = [{**row, "close": 99.0, "fetched_at": "replacement", "raw_fields_json": "{}"} for row in rows]
    with connect(db_path) as conn:
        upsert_rows(conn, rows[:1])
        conn.commit()
        assert insert_rows(conn, changed) == 1
        assert insert_rows(conn, changed) == 0
        assert insert_rows(conn, []) == 0
        stored = conn.execute("SELECT close, fetched_at, raw_fields_json FROM official_daily_price WHERE code='9007'").fetchone()
    assert stored == (rows[0]["close"], rows[0]["fetched_at"], rows[0]["raw_fields_json"])


def patch_lagging_bulk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(official, "fetch_twse_bulk", lambda **_kwargs: [dict(row, Date="1151001") for row in TWSE_BULK_FIXTURE])
    monkeypatch.setattr(official, "fetch_tpex_bulk", lambda **_kwargs: [dict(row, Date="1151002") for row in TPEX_BULK_FIXTURE])


def test_run_fetch_supplements_lagging_twse_and_preserves_existing_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    patch_lagging_bulk(monkeypatch)
    requests: list[tuple[str, dict[str, Any]]] = []

    def fetch_day(day: str, **kwargs: Any) -> dict[str, Any]:
        requests.append((day, kwargs))
        return MI_INDEX_FIXTURE

    monkeypatch.setattr(official, "fetch_twse_market_day", fetch_day)
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    existing = {
        **normalize_twse_bulk_payload(TWSE_BULK_FIXTURE)[0],
        "code": "00400A",
        "date": "2026-10-02",
        "fetched_at": "original",
        "raw_fields_json": "original raw",
    }
    with connect(db_path) as conn:
        upsert_rows(conn, [existing])
        conn.commit()
    with caplog.at_level("INFO"):
        counts = run_fetch(db_path=db_path, retries=2, retry_sleep=0.1)
    assert counts == {"TWSE": 2, "TPEx": 3, "TWSE_supplement": 1}
    assert requests == [("2026-10-02", {"retries": 2, "retry_sleep": 0.1})]
    assert "requested=2026-10-02 returned=2 inserted=1" in caplog.text
    with connect(db_path) as conn:
        stored = conn.execute("SELECT close, fetched_at, raw_fields_json FROM official_daily_price WHERE code='00400A'").fetchone()
        new_row = conn.execute("SELECT close, volume FROM official_daily_price WHERE code='00401A'").fetchone()
    assert stored == (existing["close"], "original", "original raw")
    assert new_row == (120.0, 21000)


def test_run_fetch_does_not_warn_once_the_dated_supplement_covers_the_lagging_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    patch_lagging_bulk(monkeypatch)
    monkeypatch.setattr(official, "fetch_twse_market_day", lambda *_args, **_kwargs: MI_INDEX_FIXTURE)

    with caplog.at_level("WARNING"):
        run_fetch(db_path=tmp_path / "official.sqlite")

    assert not any("disagree on the trading day" in record.getMessage() for record in caplog.records)


def test_run_fetch_still_warns_when_the_dated_supplement_returns_no_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    patch_lagging_bulk(monkeypatch)
    monkeypatch.setattr(official, "fetch_twse_market_day", lambda *_args, **_kwargs: {"stat": "很抱歉，沒有符合條件的資料!"})

    with caplog.at_level("WARNING"):
        run_fetch(db_path=tmp_path / "official.sqlite")

    assert any("disagree on the trading day" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("twse_date", ["1151002", "1151003"])
def test_run_fetch_does_not_request_supplement_when_twse_is_current(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, twse_date: str):
    patch_lagging_bulk(monkeypatch)
    monkeypatch.setattr(official, "fetch_twse_bulk", lambda **_kwargs: [dict(row, Date=twse_date) for row in TWSE_BULK_FIXTURE])

    def unexpected_request(*_args, **_kwargs):
        pytest.fail("current TWSE must not request a dated supplement")

    monkeypatch.setattr(official, "fetch_twse_market_day", unexpected_request)
    assert run_fetch(db_path=tmp_path / "official.sqlite") == {"TWSE": 2, "TPEx": 3}


@pytest.mark.parametrize("failure", ["empty", "transport", "mismatched_date", "insert"])
@pytest.mark.parametrize("missing_twse", [False, True])
def test_run_fetch_supplement_failure_retains_committed_bulk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failure: str, missing_twse: bool
):
    patch_lagging_bulk(monkeypatch)
    if missing_twse:
        monkeypatch.setattr(official, "fetch_twse_bulk", lambda **_kwargs: [])

    def fetch_day(*_args, **_kwargs):
        if failure == "transport":
            raise RuntimeError("dated endpoint unavailable")
        if failure == "empty":
            return {"stat": "很抱歉，沒有符合條件的資料!"}
        return {**MI_INDEX_FIXTURE, "date": "20261001"} if failure == "mismatched_date" else MI_INDEX_FIXTURE

    monkeypatch.setattr(official, "fetch_twse_market_day", fetch_day)
    if failure == "insert":

        def failing_insert(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
            insert_rows(conn, rows[:1])
            raise sqlite3.OperationalError("supplement insert failed")

        monkeypatch.setattr(official, "insert_rows", failing_insert)
    db_path = tmp_path / "official.sqlite"
    counts = run_fetch(db_path=db_path)
    assert counts == {"TWSE": 0 if missing_twse else 2, "TPEx": 3, "TWSE_supplement": 0}
    assert any("retaining bulk results" in record.getMessage() and record.levelname == "WARNING" for record in caplog.records)
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM official_daily_price").fetchone()[0] == (3 if missing_twse else 5)
        assert conn.execute("SELECT COUNT(*) FROM official_daily_price WHERE market='TWSE' AND date='2026-10-02'").fetchone()[0] == 0


@pytest.mark.parametrize("failure", ["empty", "transport", "normalization", "write"])
def test_run_fetch_recovers_missing_twse_using_tpex_date(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failure: str):
    patch_lagging_bulk(monkeypatch)
    db_path = tmp_path / "official.sqlite"
    init_db(db_path)
    existing = {
        **normalize_twse_bulk_payload(TWSE_BULK_FIXTURE)[0],
        "code": "00400A",
        "date": "2026-10-02",
        "fetched_at": "original",
        "raw_fields_json": "original raw",
    }
    with connect(db_path) as conn:
        upsert_rows(conn, [existing])
        conn.commit()
        original = conn.execute("SELECT * FROM official_daily_price").fetchall()

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"TWSE {failure} failed")

    if failure == "empty":
        # A nonempty payload with no usable observations is missing bulk data too.
        monkeypatch.setattr(official, "fetch_twse_bulk", lambda **_kwargs: [{"Code": "9007", "Date": ""}])
    elif failure == "transport":
        monkeypatch.setattr(official, "fetch_twse_bulk", fail)
    elif failure == "normalization":
        monkeypatch.setattr(official, "normalize_twse_bulk_payload", fail)
    else:

        def failing_upsert(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
            if rows[0]["market"] == "TWSE":
                upsert_rows(conn, [{**existing, "close": 99.0, "fetched_at": "failed"}, rows[0]])
                raise sqlite3.OperationalError("bulk write failed")
            upsert_rows(conn, rows)

        monkeypatch.setattr(official, "upsert_rows", failing_upsert)

    requests: list[str] = []

    def fetch_day(day: str, **_kwargs) -> dict[str, Any]:
        requests.append(day)
        # The TPEx commit must not include the failed TWSE write.
        with connect(db_path) as conn:
            assert conn.execute("SELECT * FROM official_daily_price WHERE market='TWSE'").fetchall() == original
            assert conn.execute("SELECT COUNT(*) FROM official_daily_price WHERE market='TPEx'").fetchone()[0] == 3
        return MI_INDEX_FIXTURE

    monkeypatch.setattr(official, "fetch_twse_market_day", fetch_day)
    with caplog.at_level("INFO"):
        counts = run_fetch(db_path=db_path)

    assert counts == {"TWSE": 0, "TPEx": 3, "TWSE_supplement": 1}
    assert requests == ["2026-10-02"]
    assert "bulk date missing" in caplog.text
    with connect(db_path) as conn:
        assert conn.execute("SELECT * FROM official_daily_price WHERE code='00400A'").fetchall() == original
        assert conn.execute("SELECT close, volume FROM official_daily_price WHERE code='00401A'").fetchone() == (120.0, 21000)
        assert conn.execute("SELECT COUNT(*) FROM official_daily_price").fetchone()[0] == 5


@pytest.mark.parametrize("missing_twse", [False, True])
@pytest.mark.parametrize("tpex_failure", ["empty", "invalid_date", "transport", "normalization"])
def test_run_fetch_without_tpex_date_does_not_request_supplement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing_twse: bool, tpex_failure: str):
    patch_lagging_bulk(monkeypatch)
    if missing_twse:
        monkeypatch.setattr(official, "fetch_twse_bulk", lambda **_kwargs: [])

    def fail(*_args, **_kwargs):
        raise RuntimeError("TPEx failed")

    if tpex_failure == "transport":
        monkeypatch.setattr(official, "fetch_tpex_bulk", fail)
    elif tpex_failure == "normalization":
        monkeypatch.setattr(official, "normalize_tpex_bulk_payload", fail)
    else:
        payload = [] if tpex_failure == "empty" else [dict(row, Date="") for row in TPEX_BULK_FIXTURE]
        monkeypatch.setattr(official, "fetch_tpex_bulk", lambda **_kwargs: payload)

    def unexpected_request(*_args, **_kwargs):
        pytest.fail("missing TPEx date must not trigger a speculative request")

    monkeypatch.setattr(official, "fetch_twse_market_day", unexpected_request)
    assert run_fetch(db_path=tmp_path / "official.sqlite") == {"TWSE": 0 if missing_twse else 2, "TPEx": 0}
