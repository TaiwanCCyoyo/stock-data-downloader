"""Pure projection of one hash-qualified ordinary MOPS dividend detail body."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Any, NoReturn

from shp_utils import mops_dividend_evidence as dividend

MAX_BODY_BYTES = 2 * 1024 * 1024
_RIGHTS_TITLE = "（五）權利分派內容："
_FOREIGN_CURRENCY_TITLE = "（六）現金股利之一部或全部是否以外幣發放："
_RECORD_TITLE = "（八）權利分派基準日："
_CASH_PREFIX = "※除息--普通股：每壹股配發現金(股利)"
_PERIOD_PREFIX = "＊股利所屬年度："
_PAYMENT_PREFIX = "＊現金股利發放日："
_EX_DATE_PREFIX = "除權/除息交易日："
_ROC_DATE = re.compile(r"([1-9][0-9]{0,2})年([0-9]{1,2})月([0-9]{1,2})日")


def _error(message: str) -> NoReturn:
    raise dividend.EvidenceError(message)


def _field(value: Any, status: str, raw: Any) -> dict[str, Any]:
    return {"value": value, "status": status, "raw": raw}


def _lines(block: str, prefix: str) -> list[str]:
    return [line for line in block.splitlines() if line.lstrip().startswith(prefix)]


def _roc_date(value: str) -> str | None:
    cleaned = value.strip()
    if re.fullmatch(r"[　 ]*年[　 ]*月[　 ]*日", cleaned):
        return None
    match = _ROC_DATE.fullmatch(cleaned)
    if match is None:
        _error("unsupported_roc_date")
    try:
        return date(
            int(match.group(1)) + 1911,
            int(match.group(2)),
            int(match.group(3)),
        ).isoformat()
    except ValueError as exc:
        raise dividend.EvidenceError("unsupported_roc_date") from exc


def _single(lines: list[str], *, missing_status: str = "missing") -> tuple[str | None, str | None]:
    if not lines:
        return None, missing_status
    if len(lines) != 1:
        return None, "ambiguous"
    return lines[0], None


def _cash_per_share(rights: str | None) -> dict[str, Any]:
    if rights is None:
        return _field(None, "missing", None)
    line, status = _single(_lines(rights, _CASH_PREFIX))
    if status is not None:
        return _field(None, status, None if status == "missing" else _lines(rights, _CASH_PREFIX))
    assert line is not None
    suffix = line.lstrip()[len(_CASH_PREFIX) :]
    match = re.match(r"([0-9]+(?:\.[0-9]+)?)元(?:，|,|$)", suffix)
    if match is not None:
        return _field(match.group(1), "stated", line)
    if not suffix.strip() or re.match(r"\s*元(?:，|,|$)", suffix):
        return _field(None, "blank", line)
    return _field(None, "unsupported", line)


def _period(rights: str | None) -> dict[str, Any]:
    if rights is None:
        return _field(None, "missing", None)
    lines = _lines(rights, _PERIOD_PREFIX)
    line, status = _single(lines)
    if status is not None:
        return _field(None, status, None if status == "missing" else lines)
    assert line is not None
    value = line.lstrip()[len(_PERIOD_PREFIX) :].strip()
    if not value:
        return _field(None, "blank", line)
    if re.fullmatch(r"其他[　 ]+不適用", value):
        return _field(None, "not_applicable", line)
    match = re.fullmatch(r"([1-9][0-9]{0,2})年，股利所屬期間(年度|第([1-4])季)", value)
    if match is None:
        return _field(None, "unsupported", line)
    period = "annual" if match.group(2) == "年度" else f"Q{match.group(3)}"
    return _field({"roc_year": int(match.group(1)), "period": period}, "stated", line)


def _payment_date(rights: str | None) -> dict[str, Any]:
    if rights is None:
        return _field(None, "missing", None)
    lines = _lines(rights, _PAYMENT_PREFIX)
    line, status = _single(lines)
    if status is not None:
        return _field(None, status, None if status == "missing" else lines)
    assert line is not None
    value = line.lstrip()[len(_PAYMENT_PREFIX) :]
    expected_suffix = "（發放現金股利時適用。）"
    if not value.endswith(expected_suffix):
        return _field(None, "unsupported", line)
    date_text = value[: -len(expected_suffix)]
    if not date_text.strip():
        return _field(None, "blank", line)
    try:
        parsed = _roc_date(date_text)
    except dividend.EvidenceError:
        return _field(None, "unsupported", line)
    return _field(parsed, "blank" if parsed is None else "stated", line)


def _record_date(record: str | None) -> dict[str, Any]:
    if record is None:
        return _field(None, "missing", None)
    lines = [line for line in record.splitlines() if line.strip()]
    if not lines:
        return _field(None, "blank", record)
    candidates = [line for line in lines if not line.lstrip().startswith(_EX_DATE_PREFIX)]
    if not candidates:
        return _field(None, "missing", None)
    if len(candidates) != 1:
        return _field(None, "ambiguous", candidates)
    line = candidates[0]
    try:
        parsed = _roc_date(line)
    except dividend.EvidenceError:
        return _field(None, "unsupported", line)
    return _field(parsed, "blank" if parsed is None else "stated", line)


def _ex_date(record: str | None) -> dict[str, Any]:
    if record is None:
        return _field(None, "missing", None)
    lines = _lines(record, _EX_DATE_PREFIX)
    line, status = _single(lines)
    if status is not None:
        return _field(None, status, None if status == "missing" else lines)
    assert line is not None
    date_text = line.lstrip()[len(_EX_DATE_PREFIX) :]
    if not date_text.strip():
        return _field(None, "blank", line)
    try:
        parsed = _roc_date(date_text)
    except dividend.EvidenceError:
        return _field(None, "unsupported", line)
    return _field(parsed, "blank" if parsed is None else "stated", line)


def _announcement_context(announcement: Any, company_id: str) -> dict[str, Any]:
    if not isinstance(announcement, dict):
        _error("invalid_announcement")
    required_text = ("subject_raw", "displayed_announcement_date_raw", "revised_payment_date_raw")
    if any(not isinstance(announcement.get(name), str) for name in required_text):
        _error("invalid_announcement")
    try:
        params = dividend._detail_parameters(announcement, company_id)
    except (KeyError, TypeError) as exc:
        raise dividend.EvidenceError("invalid_announcement") from exc
    return params


def project_statement(
    detail_body: bytes,
    *,
    company_id: str,
    announcement: dict[str, Any],
    expected_sha256: str,
) -> dict[str, Any]:
    """Project one ordinary detail body without assigning a cash event or payment outcome."""
    if not isinstance(detail_body, bytes) or len(detail_body) > MAX_BODY_BYTES:
        _error("invalid_detail_body")
    if not isinstance(company_id, str) or not re.fullmatch(r"[0-9]{4,6}", company_id):
        _error("invalid_company_id")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        _error("invalid_expected_sha256")
    body_sha256 = hashlib.sha256(detail_body).hexdigest()
    if body_sha256 != expected_sha256:
        _error("detail_body_hash_mismatch")
    parameters = _announcement_context(announcement, company_id)
    try:
        packet = json.loads(detail_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise dividend.EvidenceError("invalid_detail_json") from exc
    try:
        dividend._validate_detail(packet, company_id, announcement)
        result = dividend._result(packet, company_id)
        titles = dividend._title_names(result.get("titles"))
    except (KeyError, TypeError) as exc:
        raise dividend.EvidenceError("invalid_detail_structure") from exc
    data = result.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], list) or len(data[0]) != len(titles):
        _error("invalid_detail_structure")
    cells = dict(zip(titles, data[0], strict=True))
    blocks: dict[str, str | None] = {}
    for key, title in (
        ("rights_distribution", _RIGHTS_TITLE),
        ("foreign_currency_answer", _FOREIGN_CURRENCY_TITLE),
        ("record_date", _RECORD_TITLE),
    ):
        if title not in cells:
            blocks[key] = None
            continue
        value = cells[title]
        if not isinstance(value, str):
            _error("invalid_detail_block")
        blocks[key] = value
    identity_material = json.dumps(
        {"route": dividend.DETAIL_URL, "parameters": parameters},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schema_version": "mops-dividend-statement.v1",
        "company_id": company_id,
        "source_route": dividend.DETAIL_URL,
        "detail_parameters": dict(parameters),
        "body_sha256": body_sha256,
        "announcement_id": hashlib.sha256(identity_material).hexdigest(),
        "response_revision_id": body_sha256,
        "source_subject_raw": announcement["subject_raw"],
        "source_displayed_announcement_date_raw": announcement["displayed_announcement_date_raw"],
        "source_revised_payment_date_raw": announcement["revised_payment_date_raw"],
        "raw_blocks": blocks,
        "fields": {
            "cash_per_share": _cash_per_share(blocks["rights_distribution"]),
            "dividend_period": _period(blocks["rights_distribution"]),
            "record_date": _record_date(blocks["record_date"]),
            "stated_ex_date": _ex_date(blocks["record_date"]),
            "announced_payment_date": _payment_date(blocks["rights_distribution"]),
        },
        "payment_resolution": "unresolved",
        "event_link_status": "unlinked",
        "historical_public_availability": "unverified",
        "revision_completeness": "unverified",
    }
