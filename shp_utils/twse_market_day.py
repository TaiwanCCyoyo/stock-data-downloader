"""Dated TWSE MI_INDEX observations, independent of script entry points."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from shp_utils.official_api import fetch_json_with_retry, parse_int, parse_number

TWSE_MARKET_DAY_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TWSE_EARLIEST_DATE = "2010-01-04"
# Observed unchanged in six era probes from 2011 through 2024 and 2010/current probes.
TWSE_FIELD_SIGNATURE = (
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
)


def fetch_twse_market_day(day: str, *, retries: int = 3, retry_sleep: float = 5.0) -> Any:
    requested = date.fromisoformat(day)
    if requested.isoformat() != day or day < TWSE_EARLIEST_DATE:
        raise ValueError(f"unsupported TWSE market day: {day}")
    return fetch_json_with_retry(
        TWSE_MARKET_DAY_URL,
        params={"date": requested.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"},
        retries=retries,
        retry_sleep=retry_sleep,
    )


def normalize_twse_market_day_payload(day: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the dated price table before mapping shares and observed prices."""
    requested = date.fromisoformat(day)
    if requested.isoformat() != day or day < TWSE_EARLIEST_DATE:
        raise ValueError(f"unsupported TWSE market day: {day}")
    if not isinstance(payload, dict):
        raise ValueError("malformed TWSE market-day payload")
    returned_date = payload.get("date")
    if returned_date is not None and str(returned_date) != requested.strftime("%Y%m%d"):
        raise ValueError(f"TWSE response date mismatch for {day}: {returned_date}")
    # Holidays have only this status, without a date or tables.
    if payload.get("stat") == "很抱歉，沒有符合條件的資料!":
        return []
    if payload.get("stat") != "OK" or returned_date is None:
        raise ValueError(f"malformed TWSE response for {day}: missing successful status/date")
    tables = payload.get("tables")
    if not isinstance(tables, list):
        raise ValueError(f"malformed TWSE tables for {day}")
    price_tables = [table for table in tables if isinstance(table, dict) and "證券代號" in (table.get("fields") or [])]
    if len(price_tables) != 1:
        raise ValueError(f"missing or ambiguous TWSE price table for {day}")
    table = price_tables[0]
    fields = tuple(table.get("fields") or [])
    data = table.get("data")
    if not isinstance(data, list):
        raise ValueError(f"malformed TWSE price rows for {day}")
    if not data:
        return []
    if fields != TWSE_FIELD_SIGNATURE:
        raise ValueError(f"unrecognized TWSE field signature for {day}: {fields}")
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    rows = []
    for values in data:
        if not isinstance(values, list) or len(values) != len(fields):
            raise ValueError(f"malformed TWSE price row for {day}")
        raw = dict(zip(fields, values, strict=True))
        code = str(raw["證券代號"]).strip()
        if not code:
            raise ValueError(f"missing TWSE security code for {day}")
        volume = parse_int(raw["成交股數"])
        close = parse_number(raw["收盤價"])
        close_missing = close is None or not volume
        rows.append({
            "market": "TWSE",
            "code": code,
            "date": day,
            "name": str(raw["證券名稱"]).strip() or None,
            "open": parse_number(raw["開盤價"]),
            "high": parse_number(raw["最高價"]),
            "low": parse_number(raw["最低價"]),
            "close": None if close_missing else close,
            "volume": volume,
            "value": parse_number(raw["成交金額"]),
            "transactions": parse_int(raw["成交筆數"]),
            "close_missing": close_missing,
            "raw_fields_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
            "fetched_at": fetched_at,
        })
    return rows
