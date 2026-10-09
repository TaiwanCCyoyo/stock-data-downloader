"""Pure, source-bound free-share evidence; no entitlement or timing policy."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any, cast

from shp_utils.annual_cash_evidence import AnnualCashEvidenceError, extract_annual_cash
from shp_utils.corporate_detail_capture import FIELDS

SCHEMA_VERSION = "free-share-evidence.v1"
_TPEX_SOURCE = "tpex/exdailyq"
_TWSE_SOURCE = "twse/twt49u_detail"
_TPEX_FIELD = "每仟股無償配股"
_TWSE_FIELD = "A. 按普通股股東持股比例每千股無償配股"
_MISSING = {"", "--", "---", "NA", "N/A"}
_NUMBER = re.compile(r"^(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)(?:\.[0-9]+)?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class FreeShareEvidenceError(ValueError):
    """The held response or its caller-bound provenance is not valid evidence."""


def extract_tpex_free_shares(body: bytes) -> dict[str, Any]:
    """Extract TPEx annual free-share evidence from one held response body."""
    annual = _annual(body)
    packet = _json_object(body)
    table = _tpex_table(packet)
    fields, rows = table.get("fields"), table.get("data")
    fields = _fields(fields, _TPEX_FIELD)
    # The annual extractor has already validated nonempty TPEx rows.
    rows = cast(list[list[str]], rows)
    if annual["status"] == "unobserved_empty":
        return _result(_TPEX_SOURCE, body, [])

    records = []
    for annual_row in annual["records"]:
        row_index = annual_row["row_index"]
        row = rows[row_index]
        literals = dict(zip(fields, row, strict=True))
        status, numerator, denominator = _quantity(literals[_TPEX_FIELD], units=False)
        records.append(
            _record(
                code=annual_row["code"],
                effective_date=annual_row["effective_date"],
                row_index=row_index,
                literals=literals,
                free_field=_TPEX_FIELD,
                literal=literals[_TPEX_FIELD],
                status=status,
                numerator=numerator,
                denominator=denominator,
                date_basis="annual_row",
            )
        )
    return _result(_TPEX_SOURCE, body, records)


def extract_twse_free_shares(
    body: bytes,
    *,
    code: str,
    effective_date: str,
    annual_raw_sha256: str,
    annual_row_index: int,
) -> dict[str, Any]:
    """Extract caller-bound TWSE TWT49U detail evidence from one held body."""
    bound_code = _code(code)
    bound_date = _iso_date(effective_date)
    if not isinstance(annual_raw_sha256, str) or not _SHA256.fullmatch(annual_raw_sha256):
        raise FreeShareEvidenceError("invalid annual_raw_sha256")
    if not isinstance(annual_row_index, int) or isinstance(annual_row_index, bool) or annual_row_index < 0:
        raise FreeShareEvidenceError("invalid annual_row_index")

    packet = _json_object(body)
    if not isinstance(packet.get("stat"), str) or packet["stat"].lower() != "ok":
        raise FreeShareEvidenceError("source status is not ok")
    fields, rows = packet.get("fields"), packet.get("data")
    fields = _fields(fields, _TWSE_FIELD, exact=FIELDS["twt49u"])
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], list) or len(rows[0]) != len(fields):
        raise FreeShareEvidenceError("unexpected detail rows")
    if not all(isinstance(value, str) for value in rows[0]):
        raise FreeShareEvidenceError("detail literal values must be strings")
    literals = dict(zip(fields, rows[0], strict=True))
    if _code(literals["股票代號"]) != bound_code:
        raise FreeShareEvidenceError("detail response code mismatch")
    status, numerator, denominator = _quantity(literals[_TWSE_FIELD], units=True)
    return _result(
        _TWSE_SOURCE,
        body,
        [
            _record(
                code=bound_code,
                effective_date=bound_date,
                row_index=0,
                literals=literals,
                free_field=_TWSE_FIELD,
                literal=literals[_TWSE_FIELD],
                status=status,
                numerator=numerator,
                denominator=denominator,
                date_basis="caller_bound_annual_row_not_in_detail",
                annual_raw_sha256=annual_raw_sha256,
                annual_row_index=annual_row_index,
            )
        ],
    )


def _annual(body: bytes) -> dict[str, Any]:
    try:
        return extract_annual_cash(body, _TPEX_SOURCE)
    except AnnualCashEvidenceError as error:
        raise FreeShareEvidenceError(str(error)) from error


def _json_object(body: bytes) -> dict[str, Any]:
    if not isinstance(body, bytes):
        raise FreeShareEvidenceError("body must be bytes")
    try:
        packet = json.loads(body)
    except (TypeError, ValueError) as error:
        raise FreeShareEvidenceError("body is not JSON") from error
    if not isinstance(packet, dict):
        raise FreeShareEvidenceError("response must be an object")
    return packet


def _tpex_table(packet: dict[str, Any]) -> dict[str, Any]:
    tables = packet.get("tables")
    if not isinstance(tables, list) or len(tables) != 1 or not isinstance(tables[0], dict):
        raise FreeShareEvidenceError("TPEx response requires one table")
    return tables[0]


def _fields(fields: Any, free_field: str, *, exact: tuple[str, ...] | None = None) -> list[str]:
    if not isinstance(fields, list) or not all(isinstance(field, str) for field in fields) or len(fields) != len(set(fields)):
        raise FreeShareEvidenceError("unexpected fields")
    if free_field not in fields or exact is not None and set(fields) != set(exact):
        raise FreeShareEvidenceError("unexpected fields")

    return fields


def _quantity(literal: str, *, units: bool) -> tuple[str, int | None, int | None]:
    text = literal.strip()
    if text in _MISSING:
        return "missing", None, None
    if units:
        if not text.endswith("股"):
            return "invalid", None, None
        text = text.removesuffix("股").strip()
    if not _NUMBER.fullmatch(text):
        return "invalid", None, None
    try:
        amount = Decimal(text.replace(",", ""))
    except InvalidOperation:
        return "invalid", None, None
    if not amount.is_finite() or amount < 0:
        return "invalid", None, None
    ratio = Fraction(amount) / 1000
    return "stated", ratio.numerator, ratio.denominator


def _record(
    *,
    code: str,
    effective_date: str,
    row_index: int,
    literals: dict[str, str],
    free_field: str,
    literal: str,
    status: str,
    numerator: int | None,
    denominator: int | None,
    date_basis: str,
    annual_raw_sha256: str | None = None,
    annual_row_index: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "code": code,
        "effective_date": effective_date,
        "row_index": row_index,
        "literal_fields": literals,
        "free_share_field": free_field,
        "free_share_literal": literal,
        "quantity_status": status,
        "bonus_per_share_numerator": numerator,
        "bonus_per_share_denominator": denominator,
        "historical_available_at": None,
        "delivery_at": None,
        "fractional_settlement_at": None,
        "date_basis": date_basis,
    }
    if annual_raw_sha256 is not None:
        result["annual_raw_sha256"] = annual_raw_sha256
        result["annual_row_index"] = annual_row_index
    return result


def _result(source: str, body: bytes, records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "status": "observed" if records else "unobserved_empty",
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "records": records,
    }


def _code(value: Any) -> str:
    code = value.strip() if isinstance(value, str) else ""
    if not (4 <= len(code) <= 8 and code.isascii() and code.isalnum()):
        raise FreeShareEvidenceError("invalid code")
    return code


def _iso_date(value: Any) -> str:
    if not isinstance(value, str):
        raise FreeShareEvidenceError("invalid effective_date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise FreeShareEvidenceError("invalid effective_date") from error
    if parsed.isoformat() != value:
        raise FreeShareEvidenceError("invalid effective_date")
    return value
