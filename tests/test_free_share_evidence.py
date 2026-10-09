from __future__ import annotations

import hashlib
import json
from decimal import localcontext
from fractions import Fraction
from typing import Any

import pytest

from shp_utils.corporate_detail_capture import FIELDS
from shp_utils.free_share_evidence import FreeShareEvidenceError, extract_tpex_free_shares, extract_twse_free_shares


def tpex_packet(value: str = "30.00000000", *, code: str = "1234") -> dict[str, Any]:
    fields = ["現金股利", "每仟股無償配股", "權/息", "代號", "除權息日期", "現金增資認購價"]
    return {"stat": "OK", "tables": [{"stat": "ok", "fields": fields, "data": [["1.25", value, "除權息", code, "112/05/26", "18"]]}]}


def test_exact_precision_and_unknown_timing() -> None:
    with localcontext() as context:
        context.prec = 2
        row = extract_tpex_free_shares(json.dumps(tpex_packet("123.45678901"), ensure_ascii=False).encode())["records"][0]
    assert Fraction(row["bonus_per_share_numerator"], row["bonus_per_share_denominator"]) == Fraction(12345678901, 100000000000)
    assert all(row[key] is None for key in ("delivery_at", "fractional_settlement_at", "historical_available_at"))


def twse_packet(value: str = "30.00000000 股", *, code: str = "1234") -> dict[str, Any]:
    fields = list(FIELDS["twt49u"])
    row = ["" for _ in fields]
    row[fields.index("股票代號")] = code
    row[fields.index(_twse_field())] = value
    row[fields.index("C. (有償) 現金增資")] = "100 股"
    return {"stat": "OK", "fields": fields, "data": [row]}


def _twse_field() -> str:
    return "A. 按普通股股東持股比例每千股無償配股"


def twse_extract(packet: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return extract_twse_free_shares(
        json.dumps(packet, ensure_ascii=False).encode(),
        code=kwargs.pop("code", "1234"),
        effective_date=kwargs.pop("effective_date", "2023-05-26"),
        annual_raw_sha256=kwargs.pop("annual_raw_sha256", "a" * 64),
        annual_row_index=kwargs.pop("annual_row_index", 4),
        **kwargs,
    )


def test_tpex_exact_ratio_reordered_fields_and_paid_literals() -> None:
    body = json.dumps(tpex_packet(), ensure_ascii=False).encode()
    result = extract_tpex_free_shares(body)
    row = result["records"][0]
    assert result["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert (row["bonus_per_share_numerator"], row["bonus_per_share_denominator"]) == (3, 100)
    assert row["literal_fields"]["現金增資認購價"] == "18"
    assert row["date_basis"] == "annual_row"


@pytest.mark.parametrize("value,status", [("0", "stated"), ("", "missing"), ("--", "missing"), ("1e3", "invalid"), ("-1", "invalid"), ("1,23", "invalid")])
def test_tpex_zero_missing_and_invalid_are_distinct(value: str, status: str) -> None:
    row = extract_tpex_free_shares(json.dumps(tpex_packet(value), ensure_ascii=False).encode())["records"][0]
    assert row["quantity_status"] == status
    assert (row["bonus_per_share_numerator"], row["bonus_per_share_denominator"]) == ((0, 1) if status == "stated" else (None, None))


def test_tpex_requires_free_field_and_valid_annual_identity() -> None:
    packet = tpex_packet()
    packet["tables"][0]["fields"][1] = "other"
    with pytest.raises(FreeShareEvidenceError, match="fields"):
        extract_tpex_free_shares(json.dumps(packet, ensure_ascii=False).encode())
    packet = tpex_packet()
    packet["tables"][0]["data"].append(packet["tables"][0]["data"][0].copy())
    with pytest.raises(FreeShareEvidenceError, match="duplicate"):
        extract_tpex_free_shares(json.dumps(packet, ensure_ascii=False).encode())


def test_tpex_empty_is_unobserved_not_zero_coverage() -> None:
    packet = tpex_packet()
    packet["tables"][0]["data"] = []
    assert extract_tpex_free_shares(json.dumps(packet, ensure_ascii=False).encode())["status"] == "unobserved_empty"


def test_twse_exact_ratio_and_caller_bound_identity() -> None:
    result = twse_extract(twse_packet("30.00000000 股"))
    row = result["records"][0]
    assert result["source"] == "twse/twt49u_detail"
    assert (row["bonus_per_share_numerator"], row["bonus_per_share_denominator"]) == (3, 100)
    assert row["annual_raw_sha256"] == "a" * 64 and row["annual_row_index"] == 4
    assert row["date_basis"] == "caller_bound_annual_row_not_in_detail"
    assert row["literal_fields"]["C. (有償) 現金增資"] == "100 股"


@pytest.mark.parametrize(
    "value,status", [("0 股", "stated"), ("", "missing"), ("N/A", "missing"), ("30", "invalid"), ("30 shares", "invalid"), ("NaN 股", "invalid")]
)
def test_twse_units_zero_missing_and_invalid_are_distinct(value: str, status: str) -> None:
    row = twse_extract(twse_packet(value))["records"][0]
    assert row["quantity_status"] == status
    assert (row["bonus_per_share_numerator"], row["bonus_per_share_denominator"]) == ((0, 1) if status == "stated" else (None, None))


def test_twse_reordered_fields_and_malformed_identity_reject() -> None:
    packet = twse_packet()
    fields, row = packet["fields"], packet["data"][0]
    packet["fields"] = list(reversed(fields))
    packet["data"] = [list(reversed(row))]
    assert twse_extract(packet)["records"][0]["code"] == "1234"
    with pytest.raises(FreeShareEvidenceError, match="mismatch"):
        twse_extract(twse_packet(code="5678"))
    with pytest.raises(FreeShareEvidenceError, match="effective_date"):
        twse_extract(twse_packet(), effective_date="2023-5-26")
    with pytest.raises(FreeShareEvidenceError, match="sha256"):
        twse_extract(twse_packet(), annual_raw_sha256="not-a-hash")


@pytest.mark.parametrize(
    "packet",
    [
        {"stat": "OK", "fields": list(FIELDS["twt49u"]), "data": []},
        {"stat": "bad", "fields": list(FIELDS["twt49u"]), "data": [[]]},
        {"stat": "OK", "fields": list(FIELDS["twt49u"]) + ["股票代號"], "data": [[]]},
    ],
)
def test_twse_empty_bad_status_and_duplicate_fields_reject(packet: dict[str, Any]) -> None:
    with pytest.raises(FreeShareEvidenceError):
        twse_extract(packet)
