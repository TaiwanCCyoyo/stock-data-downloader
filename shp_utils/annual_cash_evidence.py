"""Pure, source-bound annual cash evidence; no acquisition or execution policy."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

SCHEMA_VERSION = "annual-cash-evidence.v1"
_SOURCES = {"twse/twt49u", "tpex/exdailyq"}
_REQUIRED = {"twse/twt49u": ("資料日期", "股票代號", "權/息", "權值+息值"), "tpex/exdailyq": ("除權息日期", "代號", "權/息", "現金股利")}
_KINDS = {"twse/twt49u": {"權", "息", "權息"}, "tpex/exdailyq": {"除權", "除息", "除權息"}}
_NUMBER = re.compile(r"^(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)(?:\.[0-9]+)?$")


class AnnualCashEvidenceError(ValueError):
    pass


def extract_annual_cash(body: bytes, source: str) -> dict[str, Any]:
    if not isinstance(source, str) or source not in _SOURCES:
        raise AnnualCashEvidenceError("unsupported source")
    if not isinstance(body, bytes):
        raise AnnualCashEvidenceError("body must be bytes")
    digest = hashlib.sha256(body).hexdigest()
    try:
        packet = json.loads(body)
    except (TypeError, ValueError) as error:
        raise AnnualCashEvidenceError("body is not JSON") from error
    fields, rows = _payload(packet, source)
    required = _REQUIRED[source]
    if (
        not isinstance(fields, list)
        or not all(isinstance(field, str) for field in fields)
        or len(fields) != len(set(fields))
        or not set(required).issubset(fields)
    ):
        raise AnnualCashEvidenceError("unexpected fields")
    if not rows:
        return {"schema_version": SCHEMA_VERSION, "status": "unobserved_empty", "body_sha256": digest, "source": source, "records": []}
    records = []
    identities: set[tuple[str, str]] = set()
    for row_index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != len(fields) or not all(isinstance(cell, str) for cell in row):
            raise AnnualCashEvidenceError("malformed row")
        value = dict(zip(fields, row, strict=True))
        code = _code(value[required[1]])
        effective_date = _roc_date(value[required[0]])
        if (code, effective_date) in identities:
            raise AnnualCashEvidenceError("duplicate code/date")
        identities.add((code, effective_date))
        kind = value[required[2]].strip()
        if kind not in _KINDS[source]:
            raise AnnualCashEvidenceError("unknown event kind")
        cash_field = required[3]
        literal = value[cash_field]
        status, amount, basis = _cash(source, kind, literal)
        records.append({
            "code": code,
            "effective_date": effective_date,
            "row_index": row_index,
            "event_kind": kind,
            "cash_field": cash_field,
            "cash_literal": literal,
            "cash_per_share": amount,
            "cash_status": status,
            "cash_basis": basis,
            "historical_available_at": None,
            "payment_at": None,
        })
    return {"schema_version": SCHEMA_VERSION, "status": "observed", "body_sha256": digest, "source": source, "records": records}


def _payload(packet: Any, source: str) -> tuple[Any, Any]:
    if not isinstance(packet, dict):
        raise AnnualCashEvidenceError("response must be an object")
    if not isinstance(packet.get("stat"), str) or packet["stat"].lower() != "ok":
        raise AnnualCashEvidenceError("source status is not ok")
    container = packet if source == "twse/twt49u" else _table(packet)
    if not isinstance(container, dict):
        raise AnnualCashEvidenceError("invalid table")
    if "stat" in container and (not isinstance(container["stat"], str) or container["stat"].lower() != "ok"):
        raise AnnualCashEvidenceError("source status is not ok")
    fields, rows = container.get("fields"), container.get("data")
    if not isinstance(rows, list):
        raise AnnualCashEvidenceError("data must be a list")
    return fields, rows


def _table(packet: dict[str, Any]) -> Any:
    tables = packet.get("tables")
    if not isinstance(tables, list) or len(tables) != 1:
        raise AnnualCashEvidenceError("TPEx response requires one table")
    return tables[0]


def _cash(source: str, kind: str, literal: str) -> tuple[str, str | None, str]:
    if source == "twse/twt49u" and kind == "權":
        return "cash_not_indicated", None, "not_resolved"
    if source == "twse/twt49u" and kind == "權息":
        return "requires_detail", None, "not_resolved"
    basis = "cash_only_total" if source == "twse/twt49u" else "explicit_cash_column"
    if literal.strip() in {"", "--", "---", "NA", "N/A"}:
        return "missing", None, basis
    if not _NUMBER.fullmatch(literal.strip()):
        return "invalid", None, basis
    try:
        amount = Decimal(literal.strip().replace(",", ""))
    except InvalidOperation:
        return "invalid", None, basis
    if not amount.is_finite() or amount < 0:
        return "invalid", None, basis
    return "stated", str(amount), basis


def _code(value: str) -> str:
    code = value.strip()
    if not (4 <= len(code) <= 8 and code.isascii() and code.isalnum()):
        raise AnnualCashEvidenceError("invalid code")
    return code


def _roc_date(value: str) -> str:
    text = value.strip()
    try:
        if "年" in text:
            if not text.endswith("日"):
                raise ValueError
            year, tail = text.split("年", 1)
            month, day = tail.removesuffix("日").split("月", 1)
        elif "/" in text:
            year, month, day = text.split("/")
        elif text.isdigit() and len(text) == 7:
            year, month, day = text[:3], text[3:5], text[5:]
        else:
            raise ValueError
        if not all(part.isascii() and part.isdigit() for part in (year, month, day)):
            raise ValueError
        if not 1 <= len(year) <= 3 or int(year) <= 0:
            raise ValueError
        return date(int(year) + 1911, int(month), int(day)).isoformat()
    except (TypeError, ValueError) as error:
        raise AnnualCashEvidenceError("invalid effective date") from error
