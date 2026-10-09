"""Pure, source-bound evidence for observed par-value change resume notices."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from html.parser import HTMLParser
from typing import Any

SCHEMA_VERSION = "par-value-evidence.v1"
_SOURCES = {"twse/twtb8u", "tpex/pvChgRslt"}
_TWSE_FIELDS = ("恢復買賣日期", "股票代號", "停止買賣前收盤價格", "恢復買賣參考價", "開盤競價基準", "詳細資料")
_TPEX_FIELDS = ("恢復買賣日期", "證券代號", "最後交易日之收盤價格", "恢復買賣開始參考價", "開始交易基準價", "詳細資料")
_DETAIL_FIELDS = ("股票代號", "停止買賣日期", "每股換發新股票", "變更前面額", "變更後面額")
_DETAIL_LABELS = ("證券代號/證券名稱:", "停止買賣日期:", "恢復買賣日期:", "變更股票面額換股率:", "變更前股票面額:", "變更後股票面額:")
_NUMBER = re.compile(r"^(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)(?:\.[0-9]+)?$")
_CODE = re.compile(r"^[0-9A-Za-z]{4,8}$")


class ParValueEvidenceError(ValueError):
    """A held response is not a valid, source-bound par-value record."""


def extract_par_value_resumes(body: bytes, *, source: str, start: str, end: str) -> dict[str, Any]:
    """Extract TWSE or TPEx resume rows, retaining unresolved TWSE exchange terms."""
    bounds = _bounds(start, end)
    packet = _packet(body)
    if source not in _SOURCES:
        raise ParValueEvidenceError("unsupported source")
    fields, rows = _resume_payload(packet, source)
    required = _TWSE_FIELDS if source == "twse/twtb8u" else _TPEX_FIELDS
    _fields(fields, required)
    if source == "tpex/pvChgRslt":
        table = packet["tables"][0]
        if type(table.get("totalCount")) is not int or table["totalCount"] != len(rows):
            raise ParValueEvidenceError("TPEx declared count mismatch")
    records = [_resume_record(source, fields, row, index, bounds) for index, row in enumerate(rows)]
    _duplicates(records)
    return _result(source, body, records)


def extract_twse_par_detail(
    body: bytes,
    *,
    resume_body: bytes,
    resume_row_index: int,
    start: str,
    end: str,
) -> dict[str, Any]:
    """Bind one TWTB7UDetail response to an independently reparsed TWTB8U row."""
    resume = extract_par_value_resumes(resume_body, source="twse/twtb8u", start=start, end=end)
    if not isinstance(resume_row_index, int) or isinstance(resume_row_index, bool) or not 0 <= resume_row_index < len(resume["records"]):
        raise ParValueEvidenceError("invalid resume row index")
    base = dict(resume["records"][resume_row_index])
    base["resume_body_sha256"] = resume["body_sha256"]
    packet = _packet(body)
    if not isinstance(packet.get("stat"), str) or packet["stat"].lower() != "ok":
        raise ParValueEvidenceError("source status is not ok")
    fields, rows = packet.get("fields"), packet.get("data")
    _fields(fields, _DETAIL_FIELDS)
    if not isinstance(fields, list) or not isinstance(rows, list):
        raise ParValueEvidenceError("data must be a list")
    if not rows:
        base.update({"detail_body_sha256": _sha(body), "detail_row_index": None, "detail_literal_fields": None})
        return _result("twse/twtb7u_detail", body, [base])
    if len(rows) != 1:
        raise ParValueEvidenceError("detail requires one row")
    literals = _literals(fields, rows[0])
    code = _code(literals["股票代號"])
    stop = _roc_date(literals["停止買賣日期"])
    if code != base["code"] or stop != base["stop_date"]:
        raise ParValueEvidenceError("detail identity mismatch")
    ratio = _unit_number(literals["每股換發新股票"], "股")
    old_face = _unit_number(literals["變更前面額"], "元/股")
    new_face = _unit_number(literals["變更後面額"], "元/股")
    if ratio is not None and old_face is not None and new_face is not None:
        _face_ratio(ratio, old_face, new_face)
    base.update({
        "exchange_ratio": _fraction_json(ratio),
        "exchange_ratio_status": "stated" if ratio is not None else "unresolved",
        "old_face_value": str(old_face) if old_face is not None else None,
        "new_face_value": str(new_face) if new_face is not None else None,
        "detail_body_sha256": _sha(body),
        "detail_row_index": 0,
        "detail_literal_fields": literals,
    })
    return _result("twse/twtb7u_detail", body, [base])


def _resume_payload(packet: dict[str, Any], source: str) -> tuple[list[str], list[Any]]:
    if source == "twse/twtb8u":
        if not isinstance(packet.get("stat"), str) or packet["stat"].lower() != "ok":
            raise ParValueEvidenceError("source status is not ok")
        fields, rows = packet.get("fields"), packet.get("data")
    else:
        if not isinstance(packet.get("stat"), str) or packet["stat"].lower() != "ok":
            raise ParValueEvidenceError("source status is not ok")
        tables = packet.get("tables")
        if not isinstance(tables, list) or len(tables) != 1 or not isinstance(tables[0], dict):
            raise ParValueEvidenceError("TPEx requires one table")
        fields, rows = tables[0].get("fields"), tables[0].get("data")
    if not isinstance(fields, list) or not isinstance(rows, list):
        raise ParValueEvidenceError("invalid resume schema")
    return fields, rows


def _resume_record(source: str, fields: list[str], row: Any, index: int, bounds: tuple[date, date]) -> dict[str, Any]:
    literals = _literals(fields, row)
    code_field = "股票代號" if source == "twse/twtb8u" else "證券代號"
    resume_field = "恢復買賣參考價" if source == "twse/twtb8u" else "恢復買賣開始參考價"
    previous_field = "停止買賣前收盤價格" if source == "twse/twtb8u" else "最後交易日之收盤價格"
    auction_field = "開盤競價基準" if source == "twse/twtb8u" else "開始交易基準價"
    code = _code(literals[code_field])
    resume = _roc_date(literals["恢復買賣日期"])
    stop: str | None = None
    ratio: Decimal | None = None
    old_face: Decimal | None = None
    new_face: Decimal | None = None
    if source == "twse/twtb8u":
        detail = literals["詳細資料"]
        pieces = detail.split(",")
        if len(pieces) != 3 or _code(pieces[0]) != code:
            raise ParValueEvidenceError("TWSE detail key mismatch")
        stop, detail_resume = _iso_compact(pieces[1]), _iso_compact(pieces[2])
        if detail_resume != resume:
            raise ParValueEvidenceError("TWSE detail resume mismatch")
    else:
        details = _html_details(literals["詳細資料"])
        code = _detail_code(details["證券代號/證券名稱:"], code)
        stop, detail_resume = _roc_date(details["停止買賣日期:"]), _roc_date(details["恢復買賣日期:"])
        if detail_resume != resume:
            raise ParValueEvidenceError("TPEx detail resume mismatch")
        ratio = _optional_decimal(details["變更股票面額換股率:"], positive=True)
        old_face = _optional_decimal(details["變更前股票面額:"], positive=True)
        new_face = _optional_decimal(details["變更後股票面額:"], positive=True)
        if ratio is not None and old_face is not None and new_face is not None:
            _face_ratio(ratio, old_face, new_face)
    if date.fromisoformat(stop) >= date.fromisoformat(resume):
        raise ParValueEvidenceError("stop date must precede resume date")
    if not bounds[0] <= date.fromisoformat(resume) <= bounds[1]:
        raise ParValueEvidenceError("event date outside requested bounds")
    return {
        "source_row_index": index,
        "market": "TWSE" if source == "twse/twtb8u" else "TPEx",
        "code": code,
        "stop_date": stop,
        "resume_date": resume,
        "literal_fields": literals,
        "exchange_ratio": _fraction_json(ratio),
        "exchange_ratio_status": "stated" if ratio is not None else "unresolved",
        "old_face_value": str(old_face) if old_face is not None else None,
        "new_face_value": str(new_face) if new_face is not None else None,
        "previous_close": _price_text(literals[previous_field]),
        "resume_reference_price": _price_text(literals[resume_field]),
        "opening_auction_base": _price_text(literals[auction_field]),
        "historical_available_at": None,
    }


def _packet(body: bytes) -> dict[str, Any]:
    if not isinstance(body, bytes):
        raise ParValueEvidenceError("body must be bytes")
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ParValueEvidenceError("body is not JSON") from error
    if not isinstance(value, dict):
        raise ParValueEvidenceError("response must be an object")
    return value


def _fields(fields: Any, required: tuple[str, ...]) -> None:
    if (
        not isinstance(fields, list)
        or not all(isinstance(field, str) for field in fields)
        or len(fields) != len(set(fields))
        or not set(required).issubset(fields)
    ):
        raise ParValueEvidenceError("unexpected fields")


def _literals(fields: list[str], row: Any) -> dict[str, str]:
    if not isinstance(row, list) or len(row) != len(fields) or not all(isinstance(value, str) for value in row):
        raise ParValueEvidenceError("malformed row")
    return dict(zip(fields, row, strict=True))


def _bounds(start: str, end: str) -> tuple[date, date]:
    try:
        lower, upper = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError) as error:
        raise ParValueEvidenceError("invalid ISO bounds") from error
    if lower.isoformat() != start or upper.isoformat() != end or lower > upper:
        raise ParValueEvidenceError("invalid ISO bounds")
    return lower, upper


def _code(value: Any) -> str:
    code = value.strip() if isinstance(value, str) else ""
    if not _CODE.fullmatch(code):
        raise ParValueEvidenceError("invalid code")
    return code


def _roc_date(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    try:
        if "/" in text:
            year, month, day = text.split("/")
        elif len(text) == 7 and text.isascii() and text.isdigit():
            year, month, day = text[:3], text[3:5], text[5:]
        else:
            raise ValueError
        if not all(part.isascii() and part.isdigit() for part in (year, month, day)):
            raise ValueError
        return date(int(year) + 1911, int(month), int(day)).isoformat()
    except ValueError as error:
        raise ParValueEvidenceError("invalid ROC date") from error


def _iso_compact(value: str) -> str:
    if not value.isascii() or not value.isdigit() or len(value) != 8:
        raise ParValueEvidenceError("invalid detail key date")
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:])).isoformat()
    except ValueError as error:
        raise ParValueEvidenceError("invalid detail key date") from error


def _decimal(value: Any, *, positive: bool) -> Decimal:
    if not isinstance(value, str) or not _NUMBER.fullmatch(value.strip()):
        raise ParValueEvidenceError("invalid numeric literal")
    try:
        result = Decimal(value.strip().replace(",", ""))
    except InvalidOperation as error:
        raise ParValueEvidenceError("invalid numeric literal") from error
    if not result.is_finite() or positive and result <= 0:
        raise ParValueEvidenceError("invalid numeric literal")
    return result


def _price_text(value: str) -> str | None:
    parsed = _optional_decimal(value, positive=True)
    return str(parsed) if parsed is not None else None


def _optional_decimal(value: str, *, positive: bool) -> Decimal | None:
    if value.strip() in {"", "--", "---", "NA", "N/A"}:
        return None
    return _decimal(value, positive=positive)


def _unit_number(value: str, suffix: str) -> Decimal | None:
    text = value.strip()
    if text in {"", "--", "---", "NA", "N/A"}:
        return None
    if not text.endswith(suffix):
        raise ParValueEvidenceError("invalid detail unit")
    return _decimal(text[: -len(suffix)].strip(), positive=True)


def _face_ratio(ratio: Decimal, old_face: Decimal, new_face: Decimal) -> None:
    if Fraction(ratio) != Fraction(old_face) / Fraction(new_face):
        raise ParValueEvidenceError("exchange ratio conflicts with face values")


def _fraction_json(value: Decimal | None) -> dict[str, int] | None:
    if value is None:
        return None
    fraction = Fraction(value)
    return {"numerator": fraction.numerator, "denominator": fraction.denominator}


class _Table(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: list[tuple[str, str]] = []
        self._tag: str | None = None
        self._text: list[str] = []
        self._pending: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"th", "td"}:
            if self._tag is not None:
                raise ParValueEvidenceError("nested HTML cells")
            self._tag, self._text = tag, []

    def handle_data(self, data: str) -> None:
        if self._tag is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != self._tag:
            return
        text = "".join(self._text).replace("\xa0", " ").strip()
        if tag == "th":
            if self._pending is not None:
                raise ParValueEvidenceError("HTML label without value")
            self._pending = text
        elif self._pending is None:
            raise ParValueEvidenceError("HTML value without label")
        else:
            self.cells.append((self._pending, text))
            self._pending = None
        self._tag = None
        self._text = []


def _html_details(value: str) -> dict[str, str]:
    parser = _Table()
    try:
        parser.feed(value)
        parser.close()
    except (ParValueEvidenceError, ValueError) as error:
        raise ParValueEvidenceError("invalid TPEx detail HTML") from error
    if parser._tag is not None or parser._pending is not None:
        raise ParValueEvidenceError("incomplete TPEx detail HTML")
    result = dict(parser.cells)
    if len(result) != len(parser.cells) or set(result) != set(_DETAIL_LABELS):
        raise ParValueEvidenceError("unexpected TPEx detail labels")
    return result


def _detail_code(value: str, expected: str) -> str:
    code = value.split("/", 1)[0].strip()
    if _code(code) != expected:
        raise ParValueEvidenceError("TPEx detail code mismatch")
    return code


def _duplicates(records: list[dict[str, Any]]) -> None:
    keys = {(record["market"], record["code"], record["stop_date"], record["resume_date"]) for record in records}
    if len(keys) != len(records):
        raise ParValueEvidenceError("duplicate event identity")


def _result(source: str, body: bytes, records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "status": "observed" if records else "unobserved_empty",
        "body_sha256": _sha(body),
        "records": records,
    }


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
