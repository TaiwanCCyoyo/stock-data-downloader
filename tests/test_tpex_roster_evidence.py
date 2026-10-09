"""Observed schema and fail-closed tests without network access."""

import hashlib
import json
from typing import Any

import pytest

from shp_utils.tpex_roster_evidence import project_roster


def payload():
    return {
        "stat": "ok",
        "date": "20210105",
        "tables": [
            {
                "date": "110/01/05",
                "category": "上櫃指數股票型基金(ETF)",
                "totalCount": 1,
                "fields": ["名稱", "代號", "開盤 "],
                "data": [["基金", "00679B", "45.0"]],
            }
        ],
    }


def parse(value: dict[str, Any], digest: str | None = None):
    body = json.dumps(value).encode()
    return project_roster(body, requested_date="2021-01-05", category_type="EE", expected_sha256=digest or hashlib.sha256(body).hexdigest())


def test_named_fields_and_unknown_status():
    result = parse(payload())
    assert result["rows"] == [{"code": "00679B", "name": "基金"}]
    assert result["historical_public_availability"] == "unknown"


def test_empty_is_not_absence():
    value = payload()
    value["tables"][0].update(data=[], totalCount=0)
    assert parse(value)["status"] == "observed_empty"


def test_observed_etn_empty_category_label():
    value = payload()
    value["tables"][0]["category"] = ""
    body = json.dumps(value).encode()
    result = project_roster(body, requested_date="2021-01-05", category_type="EN", expected_sha256=hashlib.sha256(body).hexdigest())
    assert result["category"] == ""
    with pytest.raises(ValueError, match="missing_category"):
        parse(value)


@pytest.mark.parametrize("fault", ["date", "table_date", "hash", "width", "duplicate", "count", "field", "stat", "category"])
def test_refusals(fault: str):
    value = payload()
    table = value["tables"][0]
    if fault == "date":
        value["date"] = "20210106"
    elif fault == "table_date":
        table["date"] = "110/01/06"
    elif fault == "width":
        table["data"][0].pop()
    elif fault == "duplicate":
        table["data"] *= 2
        table["totalCount"] = 2
    elif fault == "count":
        table["totalCount"] = 2
    elif fault == "field":
        table["fields"][1] = "unknown"
    elif fault == "stat":
        value["stat"] = "error"
    elif fault == "category":
        table["category"] = "所有證券(不含權證、牛熊證)"
    with pytest.raises(ValueError):
        parse(value, "bad" if fault == "hash" else None)
