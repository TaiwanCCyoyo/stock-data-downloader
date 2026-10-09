"""Offline parser coverage with self-authored synthetic financial values.
Schema and dates exercise validation; rows are not acquired market observations.
"""

import copy
import json
from typing import Any

import pytest

from shp_utils import twse_market_day as twse

FIXTURE: dict[str, Any] = {
    "date": "20110615",
    "stat": "OK",
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
                    "9008",
                    "測試證券甲",
                    "11,000",
                    "4",
                    "12,000",
                    "104.00",
                    "104.00",
                    "106.00",
                    "106.00",
                    "<p style= color:green>-</p>",
                    "107.00",
                    "108.00",
                    "6",
                    "109.00",
                    "2",
                    "0.00",
                ]
            ],
        }
    ],
}


def test_synthetic_market_day_preserves_values_and_provenance():
    rows = twse.normalize_twse_market_day_payload("2011-06-15", FIXTURE)
    assert len(rows) == 1
    row = rows[0]
    assert (row["code"], row["name"], row["close"], row["volume"], row["transactions"], row["value"]) == ("9008", "測試證券甲", 106.0, 11000, 4, 12000)
    assert row["date"] == "2011-06-15"
    assert json.loads(row["raw_fields_json"])["證券名稱"] == "測試證券甲"
    assert row["fetched_at"]


@pytest.mark.parametrize("mutation", ["date", "signature", "row", "status", "table"])
def test_malformed_or_misdated_nonempty_response_fails(mutation: str):
    payload = copy.deepcopy(FIXTURE)
    if mutation == "date":
        payload["date"] = "20110616"
    elif mutation == "signature":
        payload["tables"][0]["fields"][-1] = "unknown"
    elif mutation == "row":
        payload["tables"][0]["data"][0].pop()
    elif mutation == "status":
        payload["stat"] = "error"
    else:
        payload["tables"] = []
    with pytest.raises(ValueError):
        twse.normalize_twse_market_day_payload("2011-06-15", payload)


def test_holiday_is_successful_empty():
    assert twse.normalize_twse_market_day_payload("2026-10-04", {"stat": "很抱歉，沒有符合條件的資料!"}) == []


@pytest.mark.parametrize("volume,close", [("0", "106.00"), ("10", "--")])
def test_no_trade_and_odd_lot_only_close_remains_null(volume: str, close: str):
    payload = copy.deepcopy(FIXTURE)
    payload["tables"][0]["data"][0][2] = volume
    payload["tables"][0]["data"][0][8] = close
    row = twse.normalize_twse_market_day_payload("2011-06-15", payload)[0]
    assert row["close_missing"] is True
    assert row["close"] is None


def test_fetch_uses_observed_endpoint_parameters(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, dict[str, Any]]] = []

    def capture_fetch(url: str, **kwargs: Any) -> Any:
        calls.append((url, kwargs))
        return FIXTURE

    monkeypatch.setattr(twse, "fetch_json_with_retry", capture_fetch)
    assert twse.fetch_twse_market_day("2011-06-15", retries=1, retry_sleep=2) == FIXTURE
    assert calls[0][1] == {"params": {"date": "20110615", "type": "ALLBUT0999", "response": "json"}, "retries": 1, "retry_sleep": 2}
