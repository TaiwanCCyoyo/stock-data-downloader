"""Bounded raw MOPS disclosure capture; never a normalized cash-payment feed."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

from config import config
from shp_utils import mops_raw_evidence as raw
from shp_utils.official_api import DEFAULT_USER_AGENT as DEFAULT_USER_AGENT

LIST_URL = "https://mops.twse.com.tw/mops/api/t108sb19"
DETAIL_URL = "https://mops.twse.com.tw/mops/api/t108sb19_detail"
DEFAULT_RAW_DIR = config.DATA_DIR / "raw" / "mops" / "dividend_announcements"
MAX_RESPONSE_BYTES = raw.MAX_RESPONSE_BYTES
LIST_TITLES = ("公告日期", "申報序號", "主旨", "種類", "內容", "修正後現金股利發放日")
PARAM_KEYS = {"declarationDate", "etn_no", "companyId", "serialNumber", "detailReportKind"}


HttpReply = raw.HttpReply
EvidenceError = raw.EvidenceError


def post_json(url: str, parameters: dict[str, Any]) -> HttpReply:
    """One bounded request. Never execute remote links or follow redirects."""
    return raw.post_json(url, parameters, allowed_urls=(LIST_URL, DETAIL_URL))


def _write_new(path: Path, body: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(body)


def _default_target() -> Path:
    """Keep the implicit data destination physical and below config.DATA_DIR."""
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
    section = _result(packet, company_id).get("recordDateAnnouncement")
    if not isinstance(section, dict):
        raise EvidenceError("missing_announcement_section")
    titles = _title_names(section.get("titles"))
    if not set(LIST_TITLES).issubset(titles):
        raise EvidenceError("unsupported_list_titles")
    data = section.get("data")
    if not isinstance(data, list):
        raise EvidenceError("missing_announcement_data")
    rows = []
    for index, row in enumerate(data):
        if not isinstance(row, list) or len(row) != len(titles):
            raise EvidenceError("list_row_alignment")
        cells = dict(zip(titles, row, strict=True))
        for title in LIST_TITLES:
            if title != "內容" and not isinstance(cells[title], str):
                raise EvidenceError("invalid_list_text")
        link = cells["內容"]
        if not isinstance(link, dict):
            raise EvidenceError("missing_detail_link")
        rows.append({
            "list_row_index": index,
            "raw_row": row,
            "displayed_announcement_date_raw": cells["公告日期"],
            "serial_number_raw": cells["申報序號"],
            "subject_raw": cells["主旨"],
            "revised_payment_date_raw": cells["修正後現金股利發放日"],
            "detail_api_name": link.get("apiName"),
            "detail_parameters": link.get("parameters"),
            "status": "unprocessed",
        })
    return rows


def _detail_parameters(row: dict[str, Any], company_id: str) -> dict[str, Any]:
    params = row["detail_parameters"]
    if row["detail_api_name"] != "t108sb19_detail":
        raise EvidenceError("unsupported_detail_route")
    if not isinstance(params, dict) or set(params) != PARAM_KEYS:
        raise EvidenceError("unsupported_detail_parameters")
    if params["companyId"] != company_id or params["etn_no"] != "" or params["detailReportKind"] != "A":
        raise EvidenceError("unsupported_detail_identity")
    serial = params["serialNumber"]
    if type(serial) is not int or serial <= 0:
        raise EvidenceError("invalid_detail_serial")
    key = params["declarationDate"]
    if not isinstance(key, str) or not re.fullmatch(r"[0-9]{7}", key):
        raise EvidenceError("invalid_detail_date_key")
    try:
        date(int(key[:3]) + 1911, int(key[3:5]), int(key[5:]))
    except ValueError as exc:
        raise EvidenceError("invalid_detail_date_key") from exc
    return dict(params)


def _validate_detail(packet: Any, company_id: str, announcement: dict[str, Any]) -> None:
    result = _result(packet, company_id)
    titles = _title_names(result.get("titles"))
    data = result.get("data")
    if not isinstance(data, list) or len(data) != 1:
        raise EvidenceError("missing_detail_data")
    if any(not isinstance(row, list) or len(row) != len(titles) for row in data):
        raise EvidenceError("detail_row_alignment")
    cells = dict(zip(titles, data[0], strict=True))
    serial = cells.get("一、公告序號：")
    if type(serial) is not int or serial != announcement["detail_parameters"]["serialNumber"]:
        raise EvidenceError("detail_serial_mismatch")
    if cells.get("二、主旨：") != announcement["subject_raw"]:
        raise EvidenceError("detail_subject_mismatch")


def capture(
    company_id: str,
    roc_year: int,
    *,
    output_dir: Path | None = None,
    max_details: int = 10,
    transport: Callable[[str, dict[str, Any]], HttpReply] | None = None,
    continue_after_business_refusal: bool = False,
    select_rows: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    stop_after: Callable[[dict[str, Any], Any], bool] | None = None,
) -> dict[str, Any]:
    """Capture one list and bounded details into a fresh directory.

    The injectable transport is for offline verification, not an alternate-source
    endpoint. A replay must be identified externally as replay, not a fresh fetch.
    Input/filesystem failures raise; source failures return an error manifest.

    The three trailing options are opt-in and default to the original behaviour;
    see ``mops_raw_evidence.run_capture`` for their exact semantics.
    """
    if not isinstance(company_id, str) or not re.fullmatch(r"[0-9]{4,6}", company_id):
        raise ValueError("company_id must contain four to six ASCII digits")
    if type(roc_year) is not int or not 1 <= roc_year <= 999:
        raise ValueError("roc_year must be an integer from 1 to 999")
    if type(max_details) is not int or not 1 <= max_details <= 50:
        raise ValueError("max_details must be an integer from 1 to 50")
    target = Path(output_dir) if output_dir is not None else _default_target()
    return raw.run_capture(
        company_id=company_id,
        scope={"roc_year": roc_year},
        max_details=max_details,
        target=target,
        schema_version="mops-dividend-evidence.v1",
        list_url=LIST_URL,
        detail_url=DETAIL_URL,
        list_parameters={"companyId": company_id, "dataType": "2", "year": str(roc_year), "month": "all", "firstDay": "", "lastDay": ""},
        validate_result=_result,
        parse_list=_announcements,
        detail_parameters=_detail_parameters,
        validate_detail=_validate_detail,
        transport=transport or post_json,
        write_new=_write_new,
        injected_transport=transport is not None,
        business_refusal=raw.classify_business_refusal if continue_after_business_refusal else None,
        select_rows=select_rows,
        stop_after=stop_after,
    )
