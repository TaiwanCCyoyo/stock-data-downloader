"""Offline projection of dated TPEx category rosters, not PIT certification."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

TYPES = frozenset({"EW", "EE", "EN", "TD", "AA", "80", "GG"})
CATEGORIES = {
    "EW": "所有證券(不含權證、牛熊證)",
    "EE": "上櫃指數股票型基金(ETF)",
    "EN": "",
    "TD": "台灣存託憑證(TDR)",
    "AA": "受益證券",
    "80": "管理股票",
    "GG": "認股權憑證",
}


def project_roster(body: bytes, *, requested_date: str, category_type: str, expected_sha256: str) -> dict[str, Any]:
    """Validate both printed dates and identity before projecting named fields."""
    day = date.fromisoformat(requested_date)
    if category_type not in TYPES:
        raise ValueError("unsupported_type")
    digest = hashlib.sha256(body).hexdigest()
    if digest != expected_sha256:
        raise ValueError("raw_hash_mismatch")
    payload = json.loads(body)
    if not isinstance(payload, dict) or str(payload.get("stat", "")).lower() != "ok":
        raise ValueError("unsuccessful_payload")
    if payload.get("date") != day.strftime("%Y%m%d"):
        raise ValueError("response_date_mismatch")
    tables = payload.get("tables")
    if not isinstance(tables, list) or len(tables) != 1 or not isinstance(tables[0], dict):
        raise ValueError("unexpected_tables")
    table = tables[0]
    if table.get("date") != f"{day.year - 1911}/{day.month:02}/{day.day:02}":
        raise ValueError("table_date_mismatch")
    fields, rows = table.get("fields"), table.get("data")
    if not isinstance(fields, list) or not all(isinstance(f, str) for f in fields) or not isinstance(rows, list):
        raise ValueError("malformed_schema")
    names = [f.strip() for f in fields]
    if len(names) != len(set(names)) or "代號" not in names or "名稱" not in names:
        raise ValueError("missing_or_duplicate_fields")
    if type(table.get("totalCount")) is not int or table["totalCount"] != len(rows):
        raise ValueError("count_mismatch")
    category = table.get("category")
    # Observed EN responses print an empty label even with populated rows.
    if not isinstance(category, str) or (not category and category_type != "EN"):
        raise ValueError("missing_category")
    if category != CATEGORIES[category_type]:
        raise ValueError("category_type_mismatch")
    ci, ni = names.index("代號"), names.index("名稱")
    output, seen = [], set()
    for row in rows:
        if not isinstance(row, list) or len(row) != len(fields):
            raise ValueError("row_width_mismatch")
        code, name = row[ci], row[ni]
        if not isinstance(code, str) or not code or not code.isascii() or not code.isalnum():
            raise ValueError("invalid_code")
        if not isinstance(name, str) or not name or code in seen:
            raise ValueError("duplicate_code_or_missing_name")
        seen.add(code)
        output.append({"code": code, "name": name})
    return {
        "schema": "tpex-dated-roster-evidence.v1",
        "date": requested_date,
        "type": category_type,
        "category": category,
        "rows": output,
        "row_count": len(output),
        "raw_sha256": digest,
        "status": "observed_rows" if rows else "observed_empty",
        "historical_public_availability": "unknown",
        "ordinary_share_qualification": "not_evaluated",
        "daily_membership_coverage": "not_established",
    }
