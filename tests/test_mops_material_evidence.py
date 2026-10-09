"""Offline contracts for bounded raw MOPS material-announcement evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from config import config
from shp_utils import mops_material_evidence as evidence
from shp_utils.mops_raw_evidence import HttpReply as _Response


def _reply(payload: dict[str, Any], *, detail: bool = False) -> _Response:
    return _Response(200, evidence.DETAIL_URL if detail else evidence.LIST_URL, json.dumps(payload, ensure_ascii=False).encode())


def _row(serial: str = "2", *, market: str = "otc") -> list[Any]:
    return [
        "6465",
        "Example",
        "115/07/09",
        "16:43:13",
        "Cash payment notice\nconditional regional delay",
        {"apiName": "t05st01_detail", "parameters": {"serialNumber": serial, "enterDate": "1150709", "marketKind": market, "companyId": "6465"}},
    ]


def _list(rows: list[list[Any]]) -> dict[str, Any]:
    return {"code": 200, "result": {"companyId": "6465", "titles": [{"main": title, "sub": []} for title in evidence.LIST_TITLES], "data": rows}}


def _detail(
    *,
    subject: str | None = None,
    serial: str = "2",
    explanation: str = "Original date: 115/07/13\nChanged date: 115/07/14\nUnaffected regions retain the original date.\n",
) -> dict[str, Any]:
    return {
        "code": 200,
        "result": {
            "companyId": "6465",
            "titles": [{"main": title, "sub": []} for title in evidence.DETAIL_TITLES],
            "data": [[serial, "115/07/09", "16:43:13", subject or "Cash payment notice\nconditional regional delay   ", explanation]],
        },
    }


class _Transport:
    def __init__(self, replies: list[_Response]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, parameters: dict[str, Any]) -> _Response:
        self.calls.append((url, parameters))
        return self.replies.pop(0)


def test_capture_is_bounded_and_preserves_conditional_explanation(tmp_path: Path):
    rows = [_row(), _row("4")]
    detail = _detail()
    list_reply = _reply(_list(rows))
    transport = _Transport([list_reply, _reply(detail, detail=True)])

    manifest = evidence.capture("6465", 115, 7, output_dir=tmp_path / "raw", max_details=1, transport=transport)

    assert manifest["schema_version"] == "mops-material-evidence.v1"
    assert manifest["status"] == "partial"
    assert (manifest["total_listed"], manifest["captured_details"], manifest["unprocessed_rows"]) == (2, 1, 1)
    announcement = manifest["announcements"][0]
    assert announcement["raw_row"] == rows[0]
    assert announcement["displayed_announcement_date_raw"] == "115/07/09"
    assert announcement["displayed_announcement_time_raw"] == "16:43:13"
    assert announcement["detail_parameters"]["enterDate"] == "1150709"
    assert announcement["explanation_raw"] == detail["result"]["data"][0][-1]
    assert "cash_event" not in announcement
    assert (tmp_path / "raw" / "response-001.json").read_bytes() == list_reply.body


def test_capture_records_raw_hash_and_exact_fixed_parameters(tmp_path: Path):
    list_reply = _reply(_list([_row()]))
    transport = _Transport([list_reply, _reply(_detail(), detail=True)])
    manifest = evidence.capture("6465", 115, 7, output_dir=tmp_path / "raw", transport=transport)

    request = manifest["requests"][0]
    assert request["sha256"] == hashlib.sha256(list_reply.body).hexdigest()
    assert (tmp_path / "raw" / request["raw_file"]).read_bytes() == list_reply.body
    assert transport.calls == [
        (evidence.LIST_URL, {"companyId": "6465", "year": "115", "month": "7", "firstDay": "", "lastDay": ""}),
        (evidence.DETAIL_URL, {"serialNumber": "2", "enterDate": "1150709", "marketKind": "otc", "companyId": "6465"}),
    ]


@pytest.mark.parametrize("year,month", [(True, 7), (115, True), (115, 0), (115, 13)])
def test_invalid_scope_does_not_create_or_request(tmp_path: Path, year: Any, month: Any):
    transport = _Transport([])
    with pytest.raises(ValueError):
        evidence.capture("6465", year, month, output_dir=tmp_path / "new", transport=transport)
    assert not (tmp_path / "new").exists()
    assert not transport.calls


@pytest.mark.parametrize(
    "mutate",
    [
        lambda row: row[-1].update(apiName="unknown"),
        lambda row: row[-1]["parameters"].update(marketKind="twse"),
        lambda row: row[-1]["parameters"].update(serialNumber=2),
    ],
)
def test_unsupported_detail_link_is_not_requested(tmp_path: Path, mutate: Any):
    row = _row()
    mutate(row)
    transport = _Transport([_reply(_list([row]))])
    manifest = evidence.capture("6465", 115, 7, output_dir=tmp_path / "raw", transport=transport)
    assert manifest["status"] == "error"
    assert len(transport.calls) == 1


def test_detail_identity_mismatch_is_raw_error_without_retry(tmp_path: Path):
    bad = _reply(_detail(subject="different"), detail=True)
    transport = _Transport([_reply(_list([_row()])), bad])
    manifest = evidence.capture("6465", 115, 7, output_dir=tmp_path / "raw", transport=transport)
    assert manifest["status"] == "error"
    assert manifest["announcements"][0]["status"] == "failed"
    assert (tmp_path / "raw" / "response-002.json").read_bytes() == bad.body
    assert len(transport.calls) == 2


def test_reordered_unique_titles_map_fields_by_name(tmp_path: Path):
    titles = ["主旨", "詳細資料", "發言時間", "公司名稱", "公司代號", "發言日期"]
    original = _row()
    by_title: dict[str, Any] = dict(zip(evidence.LIST_TITLES, original, strict=True))
    list_packet = _list([[by_title[title] for title in titles]])
    list_packet["result"]["titles"] = [{"main": title, "sub": []} for title in titles]
    detail_packet = _detail()
    detail_titles = ["說明", "主旨", "發言時間", "序號", "發言日期"]
    detail_by_title: dict[str, Any] = dict(zip(evidence.DETAIL_TITLES, detail_packet["result"]["data"][0], strict=True))
    detail_packet["result"]["titles"] = [{"main": title, "sub": []} for title in detail_titles]
    detail_packet["result"]["data"] = [[detail_by_title[title] for title in detail_titles]]

    manifest = evidence.capture(
        "6465",
        115,
        7,
        output_dir=tmp_path / "raw",
        transport=_Transport([_reply(list_packet), _reply(detail_packet, detail=True)]),
    )

    assert manifest["status"] == "complete"
    assert manifest["announcements"][0]["displayed_announcement_time_raw"] == "16:43:13"


@pytest.mark.parametrize("titles", [[], [{"main": "公司代號", "sub": []}] * 6])
def test_missing_or_duplicate_list_titles_are_rejected(tmp_path: Path, titles: list[dict[str, Any]]):
    packet = _list([_row()])
    packet["result"]["titles"] = titles
    manifest = evidence.capture("6465", 115, 7, output_dir=tmp_path / "raw", transport=_Transport([_reply(packet)]))
    assert manifest["status"] == "error"
    assert manifest["requests"][0]["outcome"] == "schema_error"


@pytest.mark.parametrize("field,value", [(0, "9999"), (1, 2), (2, None), (3, None), (4, None)])
def test_invalid_list_row_company_or_text_is_rejected(tmp_path: Path, field: int, value: Any):
    row = _row()
    row[field] = value
    manifest = evidence.capture("6465", 115, 7, output_dir=tmp_path / "raw", transport=_Transport([_reply(_list([row]))]))
    assert manifest["status"] == "error"


@pytest.mark.parametrize(
    "cell_index,value",
    [(0, "3"), (1, "115/07/10"), (2, "16:43:14"), (4, None)],
)
def test_detail_identity_and_explanation_type_mismatches_fail(tmp_path: Path, cell_index: int, value: Any):
    packet = _detail()
    packet["result"]["data"][0][cell_index] = value
    manifest = evidence.capture(
        "6465",
        115,
        7,
        output_dir=tmp_path / "raw",
        transport=_Transport([_reply(_list([_row()])), _reply(packet, detail=True)]),
    )
    assert manifest["status"] == "error"
    assert manifest["announcements"][0]["status"] == "failed"


def test_enter_date_remains_distinct_from_matching_displayed_date(tmp_path: Path):
    row = _row()
    row[-1]["parameters"]["enterDate"] = "1150708"
    manifest = evidence.capture(
        "6465",
        115,
        7,
        output_dir=tmp_path / "raw",
        transport=_Transport([_reply(_list([row])), _reply(_detail(), detail=True)]),
    )
    announcement = manifest["announcements"][0]
    assert manifest["status"] == "complete"
    assert announcement["detail_parameters"]["enterDate"] == "1150708"
    assert announcement["displayed_announcement_date_raw"] == "115/07/09"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda row: row[-1]["parameters"].update(enterDate="115070"),
        lambda row: row[-1]["parameters"].update(enterDate="0000101"),
        lambda row: row[-1]["parameters"].update(unexpected="value"),
    ],
)
def test_invalid_detail_key_or_parameter_shape_is_not_followed(tmp_path: Path, mutate: Any):
    row = _row()
    mutate(row)
    transport = _Transport([_reply(_list([row]))])
    manifest = evidence.capture("6465", 115, 7, output_dir=tmp_path / "raw", transport=transport)
    assert manifest["status"] == "error"
    assert len(transport.calls) == 1


def test_later_detail_failure_is_partial_and_counts_remaining_rows(tmp_path: Path):
    rows = [_row(), _row("4"), _row("5")]
    bad = _detail(serial="9")
    transport = _Transport([_reply(_list(rows)), _reply(_detail(), detail=True), _reply(bad, detail=True)])
    manifest = evidence.capture("6465", 115, 7, output_dir=tmp_path / "raw", transport=transport)
    assert manifest["status"] == "partial"
    assert (manifest["captured_details"], manifest["unprocessed_rows"]) == (1, 2)
    assert [item["status"] for item in manifest["announcements"]] == ["captured", "failed", "unprocessed"]
    assert len(transport.calls) == 3


def test_default_output_is_config_anchored_and_link_components_fail_before_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    data_root = tmp_path / "data"
    raw_root = data_root / "raw" / "mops" / "material_announcements"
    monkeypatch.setattr(config, "DATA_DIR", data_root)
    monkeypatch.setattr(evidence, "DEFAULT_RAW_DIR", raw_root)
    assert evidence._default_target().parent == raw_root
    monkeypatch.setattr(Path, "is_junction", lambda self: self == data_root / "raw")
    with pytest.raises(ValueError, match="link or junction"):
        evidence.capture("6465", 115, 7, transport=lambda *_args: pytest.fail("must not fetch"))
    assert not data_root.exists()


def test_raw_write_failure_does_not_publish_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    real_write = evidence._write_new

    def fail_raw(path: Path, body: bytes) -> None:
        if path.name.startswith("response-"):
            raise PermissionError("offline storage failure")
        real_write(path, body)

    monkeypatch.setattr(evidence, "_write_new", fail_raw)
    with pytest.raises(PermissionError):
        evidence.capture("6465", 115, 7, output_dir=tmp_path / "raw", transport=_Transport([_reply(_list([]))]))
    assert not (tmp_path / "raw" / "manifest.json").exists()


def test_source_empty_retains_unverified_history_markers(tmp_path: Path):
    manifest = evidence.capture("6465", 115, 7, output_dir=tmp_path / "raw", transport=_Transport([_reply(_list([]))]))
    assert manifest["status"] == "source_empty"
    for key in ("historical_coverage", "historical_public_availability", "revision_completeness"):
        assert manifest[key] == "unverified"
