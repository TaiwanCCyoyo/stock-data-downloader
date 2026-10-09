from __future__ import annotations

import hashlib
import json
from typing import Any, cast

import pytest

from shp_utils.annual_cash_evidence import AnnualCashEvidenceError, extract_annual_cash


def twse_packet(value: str = "0.000000") -> dict:
    return {"stat": "OK", "fields": ["股票代號", "資料日期", "權/息", "權值+息值"], "data": [["1234", "112年05月26日", "息", value]]}


@pytest.mark.parametrize(
    "value,status",
    [
        ("0.000000", "stated"),
        ("--", "missing"),
        ("NA", "missing"),
        ("N/A", "missing"),
        ("-0.5", "invalid"),
        ("NaN", "invalid"),
        ("1,23", "invalid"),
        ("1２", "invalid"),
    ],
)
def test_numeric_zero_missing_and_invalid_remain_distinct(value: str, status: str) -> None:
    result = extract_annual_cash(json.dumps(twse_packet(value)).encode(), "twse/twt49u")
    row = result["records"][0]
    assert row["cash_literal"] == value and row["cash_status"] == status
    assert (row["cash_per_share"] is None) == (status != "stated")


@pytest.mark.parametrize("bad_date", ["112/02/30", "2023/05/26", "112年05月26", "１１２/05/26", "0/01/01"])
def test_invalid_roc_date_is_not_coerced(bad_date: str) -> None:
    packet = twse_packet()
    packet["data"][0][1] = bad_date
    with pytest.raises(AnnualCashEvidenceError, match="date"):
        extract_annual_cash(json.dumps(packet).encode(), "twse/twt49u")


def test_duplicate_identity_and_named_fields_reject() -> None:
    packet = twse_packet()
    packet["data"].append(packet["data"][0].copy())
    with pytest.raises(AnnualCashEvidenceError, match="duplicate"):
        extract_annual_cash(json.dumps(packet).encode(), "twse/twt49u")
    packet = twse_packet()
    packet["fields"][0] = "權值+息值"
    with pytest.raises(AnnualCashEvidenceError, match="fields"):
        extract_annual_cash(json.dumps(packet).encode(), "twse/twt49u")


@pytest.mark.parametrize("data", [None, {}, [["1234"]], [["1234", "112/05/26", "息", 1.5]]])
def test_invalid_rows_reject(data: Any) -> None:
    packet = twse_packet()
    packet["data"] = data
    with pytest.raises(AnnualCashEvidenceError):
        extract_annual_cash(json.dumps(packet).encode(), "twse/twt49u")


def test_unknown_status_empty_shape_and_source_reject() -> None:
    packet = twse_packet()
    packet["stat"] = "error"
    with pytest.raises(AnnualCashEvidenceError, match="status"):
        extract_annual_cash(json.dumps(packet).encode(), "twse/twt49u")
    with pytest.raises(AnnualCashEvidenceError, match="source"):
        extract_annual_cash(b"{}", cast("Any", []))
    with pytest.raises(AnnualCashEvidenceError, match="fields"):
        extract_annual_cash(b'{"stat":"OK","data":[],"fields":[]}', "twse/twt49u")


def test_outer_failure_cannot_be_hidden_by_tpex_table_success() -> None:
    table = {"stat": "ok", "fields": ["代號", "除權息日期", "權/息", "現金股利"], "data": [["1234", "1120526", "除息", "1"]]}
    with pytest.raises(AnnualCashEvidenceError, match="status"):
        extract_annual_cash(json.dumps({"stat": "failed", "tables": [table]}).encode(), "tpex/exdailyq")


def test_metadata_is_not_promoted_to_historical_timing_or_cash() -> None:
    packet = twse_packet()
    packet["fields"].append("最近一次申報資料 季別/日期")
    packet["data"][0].append("2026Q2")
    row = extract_annual_cash(json.dumps(packet).encode(), "twse/twt49u")["records"][0]
    assert row["historical_available_at"] is None and row["payment_at"] is None
    assert "2026Q2" not in json.dumps(row)


def test_twse_cash_only_precision_and_non_cash_kinds() -> None:
    fields = ["股票代號", "資料日期", "權/息", "權值+息值"]
    body = json.dumps({
        "stat": "OK",
        "fields": fields,
        "data": [["1234", "112年05月26日", "息", "6.000000"], ["1235", "112年05月26日", "權", "9"], ["1236", "112年05月26日", "權息", "10"]],
    }).encode()
    result = extract_annual_cash(body, "twse/twt49u")
    assert result["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert [(row["cash_status"], row["cash_per_share"]) for row in result["records"]] == [
        ("stated", "6.000000"),
        ("cash_not_indicated", None),
        ("requires_detail", None),
    ]


def test_tpex_explicit_cash_reordered_and_missing_invalid() -> None:
    fields = ["現金股利", "權/息", "代號", "除權息日期"]
    table = {
        "stat": "ok",
        "fields": fields,
        "data": [["1,234.50", "除權", "1234", "112/05/26"], ["", "除息", "1235", "112/05/27"], ["1e3", "除權息", "1236", "1020116"]],
    }
    result = extract_annual_cash(json.dumps({"stat": "ok", "tables": [table]}).encode(), "tpex/exdailyq")
    assert [(row["cash_status"], row["cash_per_share"]) for row in result["records"]] == [("stated", "1234.50"), ("missing", None), ("invalid", None)]


def test_empty_is_unobserved_and_malformed_structures_reject() -> None:
    empty = extract_annual_cash(json.dumps({"stat": "OK", "fields": ["股票代號", "資料日期", "權/息", "權值+息值"], "data": []}).encode(), "twse/twt49u")
    assert empty["status"] == "unobserved_empty"
    with pytest.raises(AnnualCashEvidenceError, match="unknown"):
        extract_annual_cash(
            json.dumps({"stat": "OK", "fields": ["股票代號", "資料日期", "權/息", "權值+息值"], "data": [["1234", "112年05月26日", "其他", "1"]]}).encode(),
            "twse/twt49u",
        )
