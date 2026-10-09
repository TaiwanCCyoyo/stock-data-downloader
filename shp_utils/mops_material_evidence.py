"""Bounded MOPS material-announcement capture; never a normalized cash-event feed."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

from config import config
from shp_utils import mops_raw_evidence as raw

LIST_URL = "https://mops.twse.com.tw/mops/api/t05st01"
DETAIL_URL = "https://mops.twse.com.tw/mops/api/t05st01_detail"
DEFAULT_RAW_DIR = config.DATA_DIR / "raw" / "mops" / "material_announcements"
MAX_RESPONSE_BYTES = raw.MAX_RESPONSE_BYTES
LIST_TITLES = ("公司代號", "公司名稱", "發言日期", "發言時間", "主旨", "詳細資料")
DETAIL_TITLES = ("序號", "發言日期", "發言時間", "主旨", "說明")
DETAIL_PARAMETER_KEYS = {"serialNumber", "enterDate", "marketKind", "companyId"}
HttpReply = raw.HttpReply
EvidenceError = raw.EvidenceError


def post_json(url: str, parameters: dict[str, Any]) -> HttpReply:
    return raw.post_json(url, parameters, allowed_urls=(LIST_URL, DETAIL_URL))


def _write_new(path: Path, body: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(body)


def _default_target() -> Path:
    data_root = config.DATA_DIR.absolute()
    raw_root = DEFAULT_RAW_DIR.absolute()
    if not raw_root.is_relative_to(data_root):
        raise ValueError("default raw root is outside config.DATA_DIR")
    for component in (raw_root, *raw_root.parents):
        if component.is_symlink() or component.is_junction():
            raise ValueError("default data path contains a link or junction")
        if component == data_root:
            break
    if not raw_root.resolve().is_relative_to(data_root.resolve()):
        raise ValueError("resolved raw root is outside config.DATA_DIR")
    return raw_root / uuid4().hex


def _result(packet: Any, company_id: str) -> dict[str, Any]:
    if not isinstance(packet, dict) or type(packet.get("code")) is not int or packet["code"] != 200:
        raise EvidenceError("application_error")
    result = packet.get("result")
    if not isinstance(result, dict) or result.get("companyId") != company_id:
        raise EvidenceError("response_company_mismatch")
    return result


def _title_names(titles: Any) -> list[str]:
    if not isinstance(titles, list) or not titles:
        raise EvidenceError("missing_titles")
    names = []
    for title in titles:
        if not isinstance(title, dict) or not isinstance(title.get("main"), str):
            raise EvidenceError("invalid_title")
        names.append(title["main"])
    if len(set(names)) != len(names):
        raise EvidenceError("ambiguous_titles")
    return names


def _announcements(packet: Any, company_id: str) -> list[dict[str, Any]]:
    result = _result(packet, company_id)
    titles = _title_names(result.get("titles"))
    if not set(LIST_TITLES).issubset(titles):
        raise EvidenceError("unsupported_list_titles")
    data = result.get("data")
    if not isinstance(data, list):
        raise EvidenceError("missing_announcement_data")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(data):
        if not isinstance(row, list) or len(row) != len(titles):
            raise EvidenceError("list_row_alignment")
        cells = dict(zip(titles, row, strict=True))
        if any(not isinstance(cells[title], str) for title in LIST_TITLES if title != "詳細資料"):
            raise EvidenceError("invalid_list_text")
        if cells["公司代號"] != company_id:
            raise EvidenceError("list_row_company_mismatch")
        link = cells["詳細資料"]
        if not isinstance(link, dict):
            raise EvidenceError("missing_detail_link")
        rows.append({
            "list_row_index": index,
            "raw_row": row,
            "company_name_raw": cells["公司名稱"],
            "displayed_announcement_date_raw": cells["發言日期"],
            "displayed_announcement_time_raw": cells["發言時間"],
            "subject_raw": cells["主旨"],
            "detail_api_name": link.get("apiName"),
            "detail_parameters": link.get("parameters"),
            "status": "unprocessed",
        })
    return rows


def _detail_parameters(row: dict[str, Any], company_id: str) -> dict[str, Any]:
    params = row["detail_parameters"]
    if row["detail_api_name"] != "t05st01_detail":
        raise EvidenceError("unsupported_detail_route")
    if not isinstance(params, dict) or set(params) != DETAIL_PARAMETER_KEYS:
        raise EvidenceError("unsupported_detail_parameters")
    if params["companyId"] != company_id or params["marketKind"] != "otc":
        raise EvidenceError("unsupported_detail_identity")
    serial = params["serialNumber"]
    if not isinstance(serial, str) or not re.fullmatch(r"[1-9][0-9]*", serial):
        raise EvidenceError("invalid_detail_serial")
    key = params["enterDate"]
    if not isinstance(key, str) or not re.fullmatch(r"[0-9]{7}", key) or int(key[:3]) == 0:
        raise EvidenceError("invalid_detail_date_key")
    try:
        date(int(key[:3]) + 1911, int(key[3:5]), int(key[5:]))
    except ValueError as exc:
        raise EvidenceError("invalid_detail_date_key") from exc
    return dict(params)


def _validate_detail(packet: Any, company_id: str, announcement: dict[str, Any]) -> None:
    result = _result(packet, company_id)
    titles = _title_names(result.get("titles"))
    if not set(DETAIL_TITLES).issubset(titles):
        raise EvidenceError("unsupported_detail_titles")
    data = result.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], list) or len(data[0]) != len(titles):
        raise EvidenceError("missing_detail_data")
    cells = dict(zip(titles, data[0], strict=True))
    if any(not isinstance(cells[title], str) for title in DETAIL_TITLES):
        raise EvidenceError("invalid_detail_text")
    if cells["序號"] != announcement["detail_parameters"]["serialNumber"]:
        raise EvidenceError("detail_serial_mismatch")
    if cells["發言日期"] != announcement["displayed_announcement_date_raw"]:
        raise EvidenceError("detail_date_mismatch")
    if cells["發言時間"] != announcement["displayed_announcement_time_raw"]:
        raise EvidenceError("detail_time_mismatch")
    if cells["主旨"].rstrip() != announcement["subject_raw"].rstrip():
        raise EvidenceError("detail_subject_mismatch")
    announcement["explanation_raw"] = cells["說明"]


def capture(
    company_id: str,
    roc_year: int,
    roc_month: int,
    *,
    output_dir: Path | None = None,
    max_details: int = 10,
    transport: Callable[[str, dict[str, Any]], HttpReply] | None = None,
) -> dict[str, Any]:
    """Capture a company/month list and its first bounded detail rows in source order."""
    if not isinstance(company_id, str) or not re.fullmatch(r"[0-9]{4,6}", company_id):
        raise ValueError("company_id must contain four to six ASCII digits")
    if type(roc_year) is not int or not 1 <= roc_year <= 999:
        raise ValueError("roc_year must be an integer from 1 to 999")
    if type(roc_month) is not int or not 1 <= roc_month <= 12:
        raise ValueError("roc_month must be an integer from 1 to 12")
    if type(max_details) is not int or not 1 <= max_details <= 50:
        raise ValueError("max_details must be an integer from 1 to 50")
    target = Path(output_dir) if output_dir is not None else _default_target()
    return raw.run_capture(
        company_id=company_id,
        scope={"roc_year": roc_year, "roc_month": roc_month},
        max_details=max_details,
        target=target,
        schema_version="mops-material-evidence.v1",
        list_url=LIST_URL,
        detail_url=DETAIL_URL,
        list_parameters={"companyId": company_id, "year": str(roc_year), "month": str(roc_month), "firstDay": "", "lastDay": ""},
        validate_result=_result,
        parse_list=_announcements,
        detail_parameters=_detail_parameters,
        validate_detail=_validate_detail,
        transport=transport or post_json,
        write_new=_write_new,
        injected_transport=transport is not None,
    )
