"""Transport-free schema and normalizers for the institutional daily reports.

Split out of ``scripts/fetch_institutional.py`` so a stored raw response can be
re-parsed without importing the HTTP transport. ``shp_utils.official_api``
installs an OS trust store at import time and pulls in ``requests`` and
``truststore``; a consumer that only wants to reproduce rows from a snapshot it
already holds should not have to carry either. Nothing here performs I/O.

The field tuples are signatures, matched exactly. An unrecognised one raises
rather than being guessed at: these reports have changed column shape three
times on the TWSE side and twice on the TPEx side, and a positional misread
produces plausible numbers rather than an error.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any

from shp_utils.official_parsing import parse_int

MARKET_TWSE = "TWSE"
MARKET_TPEX = "TPEx"
MARKETS = (MARKET_TWSE, MARKET_TPEX)

TWSE_FIELDS_A = (
    "證券代號",
    "證券名稱",
    "外資買進股數",
    "外資賣出股數",
    "外資買賣超股數",
    "投信買進股數",
    "投信賣出股數",
    "投信買賣超股數",
    "自營商買賣超股數",
    "自營商買進股數",
    "自營商賣出股數",
    "三大法人買賣超股數",
)
TWSE_FIELDS_B = (
    "證券代號",
    "證券名稱",
    "外資買進股數",
    "外資賣出股數",
    "外資買賣超股數",
    "投信買進股數",
    "投信賣出股數",
    "投信買賣超股數",
    "自營商買賣超股數",
    "自營商買進股數(自行買賣)",
    "自營商賣出股數(自行買賣)",
    "自營商買賣超股數(自行買賣)",
    "自營商買進股數(避險)",
    "自營商賣出股數(避險)",
    "自營商買賣超股數(避險)",
    "三大法人買賣超股數",
)
TWSE_FIELDS_C = (
    "證券代號",
    "證券名稱",
    "外陸資買進股數(不含外資自營商)",
    "外陸資賣出股數(不含外資自營商)",
    "外陸資買賣超股數(不含外資自營商)",
    "外資自營商買進股數",
    "外資自營商賣出股數",
    "外資自營商買賣超股數",
    "投信買進股數",
    "投信賣出股數",
    "投信買賣超股數",
    "自營商買賣超股數",
    "自營商買進股數(自行買賣)",
    "自營商賣出股數(自行買賣)",
    "自營商買賣超股數(自行買賣)",
    "自營商買進股數(避險)",
    "自營商賣出股數(避險)",
    "自營商買賣超股數(避險)",
    "三大法人買賣超股數",
)
TPEX_FIELDS_OLD = (
    "代號",
    "名稱",
    "外資及陸資買股數",
    "外資及陸資賣股數",
    "外資及陸資淨買股數",
    "投信買進股數",
    "投信賣股數",
    "投信淨買股數",
    "自營淨買股數",
    "自營商(自行買賣)買股數",
    "自營商(自行買賣)賣股數",
    "自營商(自行買賣)淨買股數",
    "自營商(避險)買股數",
    "自營商(避險)賣股數",
    "自營商(避險)淨買股數",
    "三大法人買賣超股數",
)
TPEX_FIELDS_CURRENT = (
    "代號",
    "名稱",
    *("買進股數", "賣出股數", "買賣超股數") * 7,
    "三大法人買賣超股數合計",
)

ROW_COLUMNS = (
    "market",
    "date",
    "code",
    "name",
    "instrument_type",
    "foreign_total_buy",
    "foreign_total_sell",
    "foreign_total_net",
    "foreign_excl_dealer_buy",
    "foreign_excl_dealer_sell",
    "foreign_excl_dealer_net",
    "foreign_dealer_buy",
    "foreign_dealer_sell",
    "foreign_dealer_net",
    "trust_buy",
    "trust_sell",
    "trust_net",
    "dealer_total_buy",
    "dealer_total_sell",
    "dealer_total_net",
    "dealer_self_buy",
    "dealer_self_sell",
    "dealer_self_net",
    "dealer_hedge_buy",
    "dealer_hedge_sell",
    "dealer_hedge_net",
    "three_institutions_net",
    "source",
    "schema_signature",
    "published_at",
    "fetched_at",
    "raw_fields_json",
)


def normalize_twse_payload(
    payload: dict[str, Any],
    report_date: date,
    *,
    instrument_types: dict[str, str] | None = None,
    fetched_at: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    fields = tuple(str(field).strip() for field in payload.get("fields") or [])
    data = payload.get("data") or []
    if not fields and not data:
        if str(payload.get("stat", "")) == "OK":
            raise ValueError(f"TWSE {report_date.isoformat()} returned OK without a schema")
        return [], "twse-empty"
    signature_map = {
        TWSE_FIELDS_A: ("twse-12-a", _normalize_twse_a),
        TWSE_FIELDS_B: ("twse-16-b", _normalize_twse_b),
        TWSE_FIELDS_C: ("twse-19-c", _normalize_twse_c),
    }
    selected = signature_map.get(fields)
    if selected is None:
        raise _unknown_signature(MARKET_TWSE, report_date, fields)
    signature, row_parser = selected
    observed_at = fetched_at or _utc_now()
    types = instrument_types or {}
    rows = [row_parser(dict(zip(fields, raw_row, strict=True)), report_date, types, observed_at, signature) for raw_row in data]
    return rows, signature


def _normalize_twse_a(raw: dict[str, Any], report_date: date, types: dict[str, str], fetched_at: str, signature: str) -> dict[str, Any]:
    return _base_row(
        raw,
        market=MARKET_TWSE,
        report_date=report_date,
        code_field="證券代號",
        name_field="證券名稱",
        types=types,
        fetched_at=fetched_at,
        signature=signature,
        foreign_total=_triple(raw, "外資買進股數", "外資賣出股數", "外資買賣超股數"),
        trust=_triple(raw, "投信買進股數", "投信賣出股數", "投信買賣超股數"),
        dealer_total=_triple(raw, "自營商買進股數", "自營商賣出股數", "自營商買賣超股數"),
        three_net=parse_int(raw.get("三大法人買賣超股數")),
    )


def _normalize_twse_b(raw: dict[str, Any], report_date: date, types: dict[str, str], fetched_at: str, signature: str) -> dict[str, Any]:
    return _base_row(
        raw,
        market=MARKET_TWSE,
        report_date=report_date,
        code_field="證券代號",
        name_field="證券名稱",
        types=types,
        fetched_at=fetched_at,
        signature=signature,
        foreign_total=_triple(raw, "外資買進股數", "外資賣出股數", "外資買賣超股數"),
        trust=_triple(raw, "投信買進股數", "投信賣出股數", "投信買賣超股數"),
        dealer_total=(None, None, parse_int(raw.get("自營商買賣超股數"))),
        dealer_self=_triple(
            raw,
            "自營商買進股數(自行買賣)",
            "自營商賣出股數(自行買賣)",
            "自營商買賣超股數(自行買賣)",
        ),
        dealer_hedge=_triple(
            raw,
            "自營商買進股數(避險)",
            "自營商賣出股數(避險)",
            "自營商買賣超股數(避險)",
        ),
        three_net=parse_int(raw.get("三大法人買賣超股數")),
    )


def _normalize_twse_c(raw: dict[str, Any], report_date: date, types: dict[str, str], fetched_at: str, signature: str) -> dict[str, Any]:
    foreign_excl = _triple(
        raw,
        "外陸資買進股數(不含外資自營商)",
        "外陸資賣出股數(不含外資自營商)",
        "外陸資買賣超股數(不含外資自營商)",
    )
    foreign_dealer = _triple(
        raw,
        "外資自營商買進股數",
        "外資自營商賣出股數",
        "外資自營商買賣超股數",
    )
    return _base_row(
        raw,
        market=MARKET_TWSE,
        report_date=report_date,
        code_field="證券代號",
        name_field="證券名稱",
        types=types,
        fetched_at=fetched_at,
        signature=signature,
        foreign_total=(
            _sum_present(foreign_excl[0], foreign_dealer[0]),
            _sum_present(foreign_excl[1], foreign_dealer[1]),
            _sum_present(foreign_excl[2], foreign_dealer[2]),
        ),
        foreign_excl=foreign_excl,
        foreign_dealer=foreign_dealer,
        trust=_triple(raw, "投信買進股數", "投信賣出股數", "投信買賣超股數"),
        dealer_total=(None, None, parse_int(raw.get("自營商買賣超股數"))),
        dealer_self=_triple(
            raw,
            "自營商買進股數(自行買賣)",
            "自營商賣出股數(自行買賣)",
            "自營商買賣超股數(自行買賣)",
        ),
        dealer_hedge=_triple(
            raw,
            "自營商買進股數(避險)",
            "自營商賣出股數(避險)",
            "自營商買賣超股數(避險)",
        ),
        three_net=parse_int(raw.get("三大法人買賣超股數")),
    )


def normalize_tpex_payload(
    payload: dict[str, Any],
    report_date: date,
    *,
    instrument_types: dict[str, str] | None = None,
    fetched_at: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    table = next(
        (table for table in payload.get("tables") or [] if table.get("fields") and table.get("data")),
        None,
    )
    if table is None:
        return [], "tpex-empty"
    fields = tuple(str(field).strip() for field in table.get("fields") or [])
    data = table.get("data") or []
    signature_map = {
        TPEX_FIELDS_OLD: ("tpex-16-old", _normalize_tpex_old),
        TPEX_FIELDS_CURRENT: ("tpex-24-current", _normalize_tpex_current),
    }
    selected = signature_map.get(fields)
    if selected is None:
        raise _unknown_signature(MARKET_TPEX, report_date, fields)
    signature, row_parser = selected
    observed_at = fetched_at or _utc_now()
    types = instrument_types or {}
    rows = [row_parser(raw_row, fields, report_date, types, observed_at, signature) for raw_row in data]
    return rows, signature


def _normalize_tpex_old(
    raw_row: list[Any],
    fields: tuple[str, ...],
    report_date: date,
    types: dict[str, str],
    fetched_at: str,
    signature: str,
) -> dict[str, Any]:
    raw = dict(zip(fields, raw_row, strict=True))
    return _base_row(
        raw,
        market=MARKET_TPEX,
        report_date=report_date,
        code_field="代號",
        name_field="名稱",
        types=types,
        fetched_at=fetched_at,
        signature=signature,
        foreign_total=_triple(raw, "外資及陸資買股數", "外資及陸資賣股數", "外資及陸資淨買股數"),
        trust=_triple(raw, "投信買進股數", "投信賣股數", "投信淨買股數"),
        dealer_total=(None, None, parse_int(raw.get("自營淨買股數"))),
        dealer_self=_triple(
            raw,
            "自營商(自行買賣)買股數",
            "自營商(自行買賣)賣股數",
            "自營商(自行買賣)淨買股數",
        ),
        dealer_hedge=_triple(
            raw,
            "自營商(避險)買股數",
            "自營商(避險)賣股數",
            "自營商(避險)淨買股數",
        ),
        three_net=parse_int(raw.get("三大法人買賣超股數")),
    )


def _normalize_tpex_current(
    raw_row: list[Any],
    fields: tuple[str, ...],
    report_date: date,
    types: dict[str, str],
    fetched_at: str,
    signature: str,
) -> dict[str, Any]:
    if len(raw_row) != len(fields):
        raise ValueError(f"TPEx {report_date.isoformat()} row has {len(raw_row)} values for {len(fields)} fields")
    triples = [(parse_int(raw_row[index]), parse_int(raw_row[index + 1]), parse_int(raw_row[index + 2])) for index in range(2, 23, 3)]
    raw = {f"{index:02d}:{field}": value for index, (field, value) in enumerate(zip(fields, raw_row, strict=True))}
    raw["代號"] = raw_row[0]
    raw["名稱"] = raw_row[1]
    return _base_row(
        raw,
        market=MARKET_TPEX,
        report_date=report_date,
        code_field="代號",
        name_field="名稱",
        types=types,
        fetched_at=fetched_at,
        signature=signature,
        foreign_excl=triples[0],
        foreign_dealer=triples[1],
        foreign_total=triples[2],
        trust=triples[3],
        dealer_self=triples[4],
        dealer_hedge=triples[5],
        dealer_total=triples[6],
        three_net=parse_int(raw_row[23]),
    )


def _base_row(
    raw: dict[str, Any],
    *,
    market: str,
    report_date: date,
    code_field: str,
    name_field: str,
    types: dict[str, str],
    fetched_at: str,
    signature: str,
    foreign_total: tuple[int | None, int | None, int | None] = (None, None, None),
    foreign_excl: tuple[int | None, int | None, int | None] = (None, None, None),
    foreign_dealer: tuple[int | None, int | None, int | None] = (None, None, None),
    trust: tuple[int | None, int | None, int | None] = (None, None, None),
    dealer_total: tuple[int | None, int | None, int | None] = (None, None, None),
    dealer_self: tuple[int | None, int | None, int | None] = (None, None, None),
    dealer_hedge: tuple[int | None, int | None, int | None] = (None, None, None),
    three_net: int | None = None,
) -> dict[str, Any]:
    code = str(raw.get(code_field, "")).strip()
    if not code:
        raise ValueError(f"{market} {report_date.isoformat()} row has no security code")
    return {
        "market": market,
        "date": report_date.isoformat(),
        "code": code,
        "name": _clean_text(raw.get(name_field)),
        "instrument_type": types.get(code, "unknown"),
        "foreign_total_buy": foreign_total[0],
        "foreign_total_sell": foreign_total[1],
        "foreign_total_net": foreign_total[2],
        "foreign_excl_dealer_buy": foreign_excl[0],
        "foreign_excl_dealer_sell": foreign_excl[1],
        "foreign_excl_dealer_net": foreign_excl[2],
        "foreign_dealer_buy": foreign_dealer[0],
        "foreign_dealer_sell": foreign_dealer[1],
        "foreign_dealer_net": foreign_dealer[2],
        "trust_buy": trust[0],
        "trust_sell": trust[1],
        "trust_net": trust[2],
        "dealer_total_buy": dealer_total[0],
        "dealer_total_sell": dealer_total[1],
        "dealer_total_net": dealer_total[2],
        "dealer_self_buy": dealer_self[0],
        "dealer_self_sell": dealer_self[1],
        "dealer_self_net": dealer_self[2],
        "dealer_hedge_buy": dealer_hedge[0],
        "dealer_hedge_sell": dealer_hedge[1],
        "dealer_hedge_net": dealer_hedge[2],
        "three_institutions_net": three_net,
        "source": "twse_t86" if market == MARKET_TWSE else "tpex_daily_trade",
        "schema_signature": signature,
        "published_at": None,
        "fetched_at": fetched_at,
        "raw_fields_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
    }


def _triple(raw: dict[str, Any], buy: str, sell: str, net: str) -> tuple[int | None, int | None, int | None]:
    return parse_int(raw.get(buy)), parse_int(raw.get(sell)), parse_int(raw.get(net))


def _sum_present(left: int | None, right: int | None) -> int | None:
    return None if left is None or right is None else left + right


def _clean_text(value: Any) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def _unknown_signature(market: str, report_date: date, fields: tuple[str, ...]) -> ValueError:
    digest = hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    return ValueError(f"unknown {market} institutional schema on {report_date.isoformat()}: {digest} {fields!r}")


def _utc_now(*, microseconds: bool = False) -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds" if microseconds else "seconds")
