"""Pytest coverage for fetch_corporate_actions.py's range fetching, both markets.

Run with: uv run pytest test_fetch_corporate_actions.py -v
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest
import requests
from fetch_corporate_actions import (
    SOURCE_CONFIGS,
    FetchRange,
    fetch_source_range,
    init_db,
    normalize_payload,
    run_fetch,
)


class _FakeResponse:
    def __init__(self, *, json_body: Any = None, url: str = "http://example.test") -> None:
        self._json_body = json_body
        self.status_code = 200
        self.text = ""
        self.url = url

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._json_body


def test_fetch_source_range_returns_the_payload_and_resolved_url(monkeypatch: pytest.MonkeyPatch):
    def fake_get(url: str, params: Any = None, timeout: Any = None, headers: Any = None) -> _FakeResponse:
        assert params["startDate"] == "20260101"
        assert params["endDate"] == "20261231"
        return _FakeResponse(json_body={"stat": "OK"}, url="http://example.test/twt49u?startDate=20260101")

    monkeypatch.setattr(requests, "get", fake_get)
    payload, url = fetch_source_range(
        SOURCE_CONFIGS["twt49u"],
        FetchRange("20260101", "20261231"),
        retries=3,
        retry_sleep=0.01,
    )

    assert payload == {"stat": "OK"}
    assert url == "http://example.test/twt49u?startDate=20260101"


def test_fetch_source_range_retries_a_read_timeout_rather_than_aborting(monkeypatch: pytest.MonkeyPatch):
    # On 2026-08-19 a single 30-second TWSE read timeout took the whole daily
    # corporate-action step down, because the retries never covered the request.
    attempts = {"count": 0}

    def fake_get(url: str, params: Any = None, timeout: Any = None, headers: Any = None) -> _FakeResponse:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise requests.ReadTimeout("read timed out")
        return _FakeResponse(json_body={"stat": "OK"})

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    payload, _url = fetch_source_range(
        SOURCE_CONFIGS["twt49u"],
        FetchRange("20260101", "20261231"),
        retries=3,
        retry_sleep=0.01,
    )

    assert payload == {"stat": "OK"}
    assert attempts["count"] == 3


def test_fetch_source_range_names_the_source_and_range_when_it_gives_up(monkeypatch: pytest.MonkeyPatch):
    def fake_get(url: str, params: Any = None, timeout: Any = None, headers: Any = None) -> _FakeResponse:
        raise requests.ReadTimeout("read timed out")

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    with pytest.raises(RuntimeError, match="twt49u 20260101-20261231"):
        fetch_source_range(
            SOURCE_CONFIGS["twt49u"],
            FetchRange("20260101", "20261231"),
            retries=1,
            retry_sleep=0.01,
        )


def _fetch_args(tmp_path: Path, *, year: int, force: bool) -> argparse.Namespace:
    return argparse.Namespace(
        source="twt49u",
        start_year=year,
        end_year=year,
        start_date=None,
        end_date=None,
        sleep=0,
        retries=1,
        retry_sleep=0.01,
        force=force,
        db_path=tmp_path / "corporate_actions.sqlite",
        raw_dir=tmp_path / "raw",
    )


def test_run_fetch_skips_a_cached_range_unless_forced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # The raw-range cache is what makes the daily previous-year close-out free:
    # a full YYYY0101-YYYY1231 range is a stable key, so it costs three requests
    # once and nothing after that.
    requested: list[str] = []

    def fake_get(url: str, params: Any = None, timeout: Any = None, headers: Any = None) -> _FakeResponse:
        requested.append(f"{params['startDate']}-{params['endDate']}")
        return _FakeResponse(json_body={"stat": "OK", "fields": [], "data": []})

    monkeypatch.setattr(requests, "get", fake_get)

    run_fetch(_fetch_args(tmp_path, year=2025, force=False))
    assert requested == ["20250101-20251231"]

    run_fetch(_fetch_args(tmp_path, year=2025, force=False))
    assert requested == ["20250101-20251231"], "a cached range must not be requested again"

    run_fetch(_fetch_args(tmp_path, year=2025, force=True))
    assert requested == ["20250101-20251231", "20250101-20251231"], "force must re-fetch"


# --- TPEx sources -------------------------------------------------------------
#
# Interface field labels are retained; all company identities and financial values
# below are self-authored synthetic fixtures, not captured market observations.

EXDAILYQ_FIELDS = [
    "除權息日期",
    "代號",
    "名稱",
    "除權息前收盤價",
    "除權息參考價",
    "權值",
    "息值",
    "權值+息值",
    "權/息",
    "漲停價",
    "跌停價",
    "開始交易基準價",
    "減除股利參考價",
    "現金股利",
    "每仟股無償配股",
    "現金增資股數",
    "現金增資認購價",
    "公開承銷股數",
    "員工認購股數",
    "原股東認購股數",
    "按持股比例仟股認購",
]

# The older 22-field era, which is also what an EMPTY response returns.
EXDAILYQ_FIELDS_OLD_ERA = EXDAILYQ_FIELDS[:15] + ["員工紅利轉增資"] + EXDAILYQ_FIELDS[15:]

REVIVT_FIELDS = [
    "恢復買賣日期",
    "股票代號",
    "名稱",
    "最後交易日之收盤價格",
    "減資恢復買賣開始日參考價格",
    "漲停價格",
    "跌停價格",
    "開始交易基準價",
    "除權參考價",
    "減資原因",
    "詳細資料",
]


def _tpex_payload(fields: list[str], rows: list[list[str]]) -> dict[str, Any]:
    """TPEx nests fields/data under tables[0]; TWSE puts them at the top level."""
    return {"stat": "ok", "tables": [{"fields": fields, "data": rows}]}


def test_exdailyq_row_normalizes_with_the_tpex_vocabulary():
    # 除息 is TPEx's word for TWSE's 息. Against the TWSE-only map every one of the
    # rows would classify as UNKNOWN.
    payload = _tpex_payload(
        EXDAILYQ_FIELDS,
        [
            [
                "114/07/15",
                "9001",
                "Synthetic Company A",
                "100.00",
                "90.00",
                "0",
                "10.00",
                "10.00",
                "除息",
                "99.00",
                "81.00",
                "90.00",
                "90.00",
                "10.00",
                "0",
                "0",
                "0",
                "0",
                "0",
                "0",
                "0",
            ]
        ],
    )
    (row,) = normalize_payload(SOURCE_CONFIGS["exdailyq"], payload)

    assert row["market"] == "TPEx"
    assert row["code"] == "9001"
    assert row["ex_date"] == "2025-07-15"
    assert row["event_type"] == "CASH_DIVIDEND"
    assert row["previous_close"] == 100.0
    assert row["reference_price"] == 90.0
    # price_factor is the reference-price ratio, exactly as for TWSE.
    assert row["price_factor"] == pytest.approx(90.0 / 100.0)
    assert row["factor_method"] == "TWSE_REFERENCE_PRICE_RATIO"
    # TPEx publishes the cash dividend outright rather than forcing the inference.
    assert row["cash_dividend_estimate"] == 10.0


def test_exdailyq_parses_the_older_schema_era_identically():
    # 2008 and 2015 responses carry 22 fields including 員工紅利轉增資; 2020 and 2025
    # carry 21 without it. Both schema shapes must parse. Reading by field name means neither needs a
    # mapping table -- and nothing may key off the field count.
    row_values = [
        "097/07/15",
        "9001",
        "Synthetic Company A",
        "100.00",
        "90.00",
        "0",
        "10.00",
        "10.00",
        "除息",
        "99.00",
        "81.00",
        "90.00",
        "90.00",
        "10.00",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
    ]
    (row,) = normalize_payload(SOURCE_CONFIGS["exdailyq"], _tpex_payload(EXDAILYQ_FIELDS_OLD_ERA, [row_values]))

    assert row["ex_date"] == "2008-07-15"
    assert row["event_type"] == "CASH_DIVIDEND"
    assert row["previous_close"] == 100.0


def test_an_empty_exdailyq_response_yields_no_rows_and_no_mapping_change():
    # An empty range returns the OLDER field list. It must not be read as evidence
    # about the schema, and it must not produce rows.
    assert normalize_payload(SOURCE_CONFIGS["exdailyq"], _tpex_payload(EXDAILYQ_FIELDS_OLD_ERA, [])) == []
    # The populated modern era still parses afterwards, unchanged.
    payload = _tpex_payload(
        EXDAILYQ_FIELDS,
        [
            [
                "114/07/15",
                "9001",
                "Synthetic Company A",
                "100.00",
                "90.00",
                "0",
                "10.00",
                "10.00",
                "除息",
                "99.00",
                "81.00",
                "90.00",
                "90.00",
                "10.00",
                "0",
                "0",
                "0",
                "0",
                "0",
                "0",
                "0",
            ]
        ],
    )
    assert normalize_payload(SOURCE_CONFIGS["exdailyq"], payload)[0]["event_type"] == "CASH_DIVIDEND"


def test_revivt_classifies_both_reduction_reasons_and_survives_a_compact_roc_date():
    # 現金減資 is TPEx's word for TWSE's 退還股款; without it cash-reduction rows
    # degrade to the generic CAPITAL_REDUCTION. And 1021007 is a compact 7-digit
    # ROC date -- the shape that used to reach normalize_yyyymmdd and raise SystemExit,
    # which `except Exception` does not catch, taking the whole daily run with it.
    payload = _tpex_payload(
        REVIVT_FIELDS,
        [
            ["1021007", "9002", "協禾", "100.00", "80.00", "88.00", "72.00", "80.00", "0", "現金減資", "<table>...</table>"],
            ["1021210", "9003", "Synthetic Company C", "2.00", "10.00", "11.00", "9.00", "10.00", "0", "彌補虧損", "<table>...</table>"],
        ],
    )
    cash, loss_offset = normalize_payload(SOURCE_CONFIGS["revivt"], payload)

    assert cash["ex_date"] == "2013-10-07", "compact ROC dates must parse, not abort the run"
    assert cash["event_type"] == "CASH_CAPITAL_REDUCTION"
    # A cash reduction returns capital AND shrinks the share count, so its factor can
    # land either side of 1. This synthetic factor is 0.8 -- nothing may assert > 1.
    assert cash["price_factor"] == pytest.approx(80.00 / 100.00)
    assert cash["price_factor"] < 1

    assert loss_offset["event_type"] == "LOSS_OFFSET_CAPITAL_REDUCTION"
    assert loss_offset["price_factor"] == pytest.approx(10.00 / 2.00)
    assert loss_offset["price_factor"] > 1
    # 詳細資料 is an embedded HTML table, not a value: kept verbatim, never parsed.
    assert loss_offset["detail_key"].startswith("<table>")


def test_an_unparseable_date_skips_its_row_instead_of_killing_the_process():
    payload = _tpex_payload(
        REVIVT_FIELDS,
        [["not-a-date", "9999", "壞資料", "10.0", "11.0", "", "", "", "0", "現金減資", ""]],
    )
    assert normalize_payload(SOURCE_CONFIGS["revivt"], payload) == []


def test_the_tpex_etf_bulletins_take_their_event_type_from_the_source():
    # Neither bulletin has a column saying which direction the event was -- unlike
    # TWSE's twtcau, which has 分割(反分割) -- so the endpoint is the event type.
    fields = ["恢復買賣日期", "證券代號", "證券名稱", "最後交易日之收盤價格", "恢復買賣開始參考價", "漲停價格", "跌停價格", "開始交易基準價", "詳細資料"]
    rows = [["1140113", "00xxx", "測試ETF", "40.00", "10.00", "11.00", "9.00", "10.00", ""]]

    (split,) = normalize_payload(SOURCE_CONFIGS["etfsplit"], _tpex_payload(fields, rows))
    (reverse,) = normalize_payload(SOURCE_CONFIGS["etfrvs"], _tpex_payload(fields, rows))

    assert split["event_type"] == "ETF_SPLIT"
    assert reverse["event_type"] == "ETF_REVERSE_SPLIT"
    assert split["ex_date"] == "2025-01-13"
    assert split["action_type"] is None, "there is no field to put here; do not invent one"


def test_twse_and_tpex_rows_for_the_same_code_and_date_coexist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # The primary key is (source, market, code, ex_date). A code listed on one
    # exchange and later the other must not have one row silently overwrite the other.
    def fake_get(url: str, params: Any = None, timeout: Any = None, headers: Any = None) -> _FakeResponse:
        if "tpex" in url:
            return _FakeResponse(
                json_body=_tpex_payload(
                    EXDAILYQ_FIELDS,
                    [
                        [
                            "114/07/15",
                            "1234",
                            "同碼",
                            "50.00",
                            "48.00",
                            "0",
                            "2.00",
                            "2.00",
                            "除息",
                            "52.80",
                            "43.20",
                            "48.00",
                            "48.00",
                            "2.00",
                            "0",
                            "0",
                            "0",
                            "0",
                            "0",
                            "0",
                            "0",
                        ]
                    ],
                )
            )
        return _FakeResponse(
            json_body={
                "stat": "OK",
                "fields": ["資料日期", "股票代號", "股票名稱", "除權息前收盤價", "除權息參考價", "權/息", "權值+息值"],
                "data": [["114年07月15日", "1234", "同碼", "50.00", "48.00", "息", "2.00"]],
            }
        )

    monkeypatch.setattr(requests, "get", fake_get)
    db_path = tmp_path / "corporate_actions.sqlite"
    for source in ("twt49u", "exdailyq"):
        args = _fetch_args(tmp_path, year=2025, force=False)
        args.source = source
        args.db_path = db_path
        run_fetch(args)

    import sqlite3

    with sqlite3.connect(db_path) as conn:
        markets = sorted(r[0] for r in conn.execute("SELECT market FROM corporate_actions WHERE code = '1234' AND ex_date = '2025-07-15'"))
    assert markets == ["TPEx", "TWSE"]


def test_the_legacy_raw_range_table_is_renamed_without_losing_rows(tmp_path: Path):
    # ALTER TABLE RENAME, not copy-and-drop: the standing data rule forbids a
    # migration containing a DROP, and these rows are the cache that makes the daily
    # previous-year close-out free.
    import sqlite3

    db_path = tmp_path / "corporate_actions.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE twse_twt49u_raw_ranges (
                source TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                url TEXT NOT NULL, fetched_at TEXT NOT NULL, status TEXT NOT NULL,
                row_count INTEGER NOT NULL, raw_path TEXT NOT NULL, sha256 TEXT NOT NULL,
                PRIMARY KEY (source, start_date, end_date)
            )
            """
        )
        conn.execute("INSERT INTO twse_twt49u_raw_ranges VALUES ('twse_twt49u','20250101','20251231','u','t','OK',7,'p','s')")
        conn.commit()

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "corporate_action_raw_ranges" in names
        assert "twse_twt49u_raw_ranges" not in names
        (row_count,) = conn.execute("SELECT row_count FROM corporate_action_raw_ranges WHERE source = 'twse_twt49u'").fetchone()
    assert row_count == 7, "the cached range must survive the rename"


def test_a_tpex_source_writes_under_its_own_market_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # data/raw/<market>/<source>/ -- which is byte-identical to where TWSE already
    # writes, so no existing file moves.
    def fake_get(url: str, params: Any = None, timeout: Any = None, headers: Any = None) -> _FakeResponse:
        return _FakeResponse(json_body=_tpex_payload(REVIVT_FIELDS, []))

    monkeypatch.setattr(requests, "get", fake_get)
    args = _fetch_args(tmp_path, year=2025, force=False)
    args.source = "revivt"
    run_fetch(args)

    assert (tmp_path / "raw" / "tpex" / "revivt").is_dir()


def test_tpex_dates_are_sent_slashed_and_twse_dates_compact(monkeypatch: pytest.MonkeyPatch):
    sent: dict[str, Any] = {}

    def fake_get(url: str, params: Any = None, timeout: Any = None, headers: Any = None) -> _FakeResponse:
        sent[url] = params
        return _FakeResponse(json_body={"stat": "OK", "fields": [], "data": []})

    monkeypatch.setattr(requests, "get", fake_get)
    fetch_source_range(SOURCE_CONFIGS["twt49u"], FetchRange("20250101", "20251231"), retries=1, retry_sleep=0.01)
    fetch_source_range(SOURCE_CONFIGS["revivt"], FetchRange("20250101", "20251231"), retries=1, retry_sleep=0.01)

    twse = next(p for u, p in sent.items() if "twse" in u)
    tpex = next(p for u, p in sent.items() if "tpex" in u)
    assert twse["startDate"] == "20250101"
    assert tpex["startDate"] == "2025/01/01"


def test_a_source_is_not_asked_for_years_before_its_own_floor():
    from fetch_corporate_actions import build_fetch_ranges

    # The TPEx ETF bulletins start 2021-06-01. A no-argument run must not ask them
    # for 2003 just because that is when TWSE's history starts.
    ranges = build_fetch_ranges(
        start_year=None,
        end_year=2022,
        start_date=None,
        end_date=None,
        first_date=SOURCE_CONFIGS["etfsplit"].first_date,
    )
    assert [r.start_date for r in ranges] == ["20210601", "20220101"]
