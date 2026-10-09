"""Offline contract tests for durable MOPS dividend announcement evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import requests

from shp_utils import mops_dividend_evidence as evidence
from shp_utils.mops_raw_evidence import HttpReply as _Response


def _json_reply(payload: dict[str, Any], *, status: int = 200, url: str | None = None) -> _Response:
    default_url = evidence.DETAIL_URL if "data" in payload.get("result", {}) else evidence.LIST_URL
    return _Response(status=status, url=url or default_url, body=json.dumps(payload, ensure_ascii=False).encode())


def _list_payload(
    rows: list[list[Any]],
    *,
    company_id: str = "5511",
    code: int | str = 200,
    titles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "result": {
            "companyId": company_id,
            "recordDateAnnouncement": {
                "titles": titles
                if titles is not None
                else [{"main": name, "sub": []} for name in ("公告日期", "申報序號", "主旨", "種類", "內容", "修正後現金股利發放日")],
                "data": rows,
            },
        },
    }


def _row(*, date: str = "105/06/28", serial: int = 1, revision: str = "無", detail_date: str = "1050624") -> list[Any]:
    return [
        date,
        str(serial),
        "本公司105年分派現金股利基準日公告",
        "",
        {
            "parameters": {
                "declarationDate": detail_date,
                "etn_no": "",
                "companyId": "5511",
                "serialNumber": serial,
                "detailReportKind": "A",
            },
            "apiName": "t108sb19_detail",
        },
        revision,
    ]


def _detail_payload(
    *,
    company_id: str = "5511",
    serial: int = 1,
    subject: str = "本公司105年分派現金股利基準日公告",
    rows: list[list[Any]] | None = None,
    titles: list[dict[str, Any]] | None = None,
    code: int | str = 200,
) -> dict[str, Any]:
    return {
        "code": code,
        "result": {
            "companyId": company_id,
            "titles": titles if titles is not None else [{"main": "一、公告序號：", "sub": []}, {"main": "二、主旨：", "sub": []}],
            "data": rows if rows is not None else [[serial, subject]],
        },
    }


class _Transport:
    def __init__(self, replies: list[_Response]) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, parameters: dict[str, Any]) -> _Response:
        self.calls.append((url, parameters))
        return self.replies.pop(0)


def _capture(tmp_path: Path, transport: Callable[[str, dict[str, Any]], _Response], **kwargs: Any) -> dict[str, Any]:
    return evidence.capture("5511", 105, output_dir=tmp_path / "evidence", transport=transport, **kwargs)


def _raw_path(output_dir: Path, record: dict[str, Any]) -> Path:
    raw_file = Path(record["raw_file"])
    return raw_file if raw_file.is_absolute() else output_dir / raw_file


def test_capture_preserves_raw_rows_hashes_and_exact_detail_request(tmp_path: Path):
    list_reply = _json_reply(_list_payload([_row(revision="無")]))
    detail_reply = _json_reply(_detail_payload())
    transport = _Transport([list_reply, detail_reply])

    manifest = _capture(tmp_path, transport)

    assert manifest["schema_version"] == "mops-dividend-evidence.v1"
    assert manifest["status"] == "complete"
    assert manifest["total_listed"] == 1
    assert manifest["captured_details"] == 1
    assert manifest["unprocessed_rows"] == 0
    request = manifest["requests"][0]
    assert request["http_status"] == 200
    assert request["outcome"] == "validated_list"
    assert request["request_parameters"]
    assert _raw_path(tmp_path / "evidence", request).read_bytes() == list_reply.body
    assert request["sha256"] == hashlib.sha256(list_reply.body).hexdigest()
    announcement = manifest["announcements"][0]
    assert announcement["raw_row"] == _row(revision="無")
    assert announcement["displayed_announcement_date_raw"] == "105/06/28"
    assert announcement["subject_raw"] == "本公司105年分派現金股利基準日公告"
    assert announcement["serial_number_raw"] == "1"
    assert announcement["revised_payment_date_raw"] == "無"
    assert announcement["detail_parameters"] == {
        "declarationDate": "1050624",
        "etn_no": "",
        "companyId": "5511",
        "serialNumber": 1,
        "detailReportKind": "A",
    }
    assert "payment_date" not in announcement
    assert "publication_timestamp" not in announcement
    assert manifest["requests"][1]["outcome"] == "validated_detail"
    assert transport.calls[1] == (
        evidence.DETAIL_URL,
        {"declarationDate": "1050624", "etn_no": "", "companyId": "5511", "serialNumber": 1, "detailReportKind": "A"},
    )


def test_capture_retains_duplicate_rows_as_separate_announcements(tmp_path: Path):
    row = _row()
    transport = _Transport([_json_reply(_list_payload([row, row])), _json_reply(_detail_payload()), _json_reply(_detail_payload())])

    manifest = _capture(tmp_path, transport)

    assert manifest["status"] == "complete"
    assert manifest["total_listed"] == 2
    assert manifest["captured_details"] == 2
    assert [item["raw_row"] for item in manifest["announcements"]] == [row, row]
    assert len(transport.calls) == 3


def test_capture_marks_bad_detail_date_as_unprocessed_without_requesting_detail(tmp_path: Path):
    transport = _Transport([_json_reply(_list_payload([_row(detail_date="105624")]))])

    manifest = _capture(tmp_path, transport)

    assert manifest["status"] == "error"
    assert manifest["captured_details"] == 0
    assert manifest["unprocessed_rows"] == 1
    assert len(transport.calls) == 1


def test_capture_marks_over_budget_rows_partial(tmp_path: Path):
    transport = _Transport([_json_reply(_list_payload([_row(serial=1), _row(serial=2)])), _json_reply(_detail_payload())])

    manifest = _capture(tmp_path, transport, max_details=1)

    assert manifest["status"] == "partial"
    assert manifest["total_listed"] == 2
    assert manifest["captured_details"] == 1
    assert manifest["unprocessed_rows"] == 1
    assert len(transport.calls) == 2


@pytest.mark.parametrize(
    ("reply", "expected_status"),
    [
        (_json_reply(_list_payload([])), "source_empty"),
        (_json_reply({"code": 200, "result": {"companyId": "5511", "recordDateAnnouncement": {}}}), "error"),
        (_json_reply(_list_payload([_row()], code="200")), "error"),
        (_json_reply(_list_payload([_row()], company_id="9999")), "error"),
        (_json_reply(_list_payload([_row()], titles=[{"main": "公告日期", "sub": []}])), "error"),
        (
            _json_reply(
                _list_payload([_row()], titles=[{"main": name, "sub": []} for name in ("公告日期", "公告日期", "主旨", "種類", "內容", "修正後現金股利發放日")])
            ),
            "error",
        ),
        (_json_reply(_list_payload([_row()]), status=503), "error"),
    ],
)
def test_capture_classifies_invalid_or_unsuccessful_list_and_keeps_raw(tmp_path: Path, reply: _Response, expected_status: str):
    transport = _Transport([reply])

    manifest = _capture(tmp_path, transport)

    assert manifest["status"] == expected_status
    assert manifest["captured_details"] == 0
    assert _raw_path(tmp_path / "evidence", manifest["requests"][0]).read_bytes() == reply.body
    assert len(transport.calls) == 1


def test_capture_malformed_detail_after_success_is_partial_and_is_not_retried(tmp_path: Path):
    bad_detail = _json_reply(_detail_payload(rows=[[1, "extra"]], titles=[{"main": "only one", "sub": []}]))
    transport = _Transport([_json_reply(_list_payload([_row(serial=1), _row(serial=2)])), _json_reply(_detail_payload()), bad_detail])

    manifest = _capture(tmp_path, transport)

    assert manifest["status"] == "partial"
    assert manifest["captured_details"] == 1
    assert manifest["unprocessed_rows"] == 1
    assert len(transport.calls) == 3
    assert any(path.read_bytes() == bad_detail.body for path in (tmp_path / "evidence").rglob("*.json"))


@pytest.mark.parametrize(
    "company_id,roc_year,max_details",
    [
        ("../5511", 105, 10),
        ("5511/evil", 105, 10),
        ("", 105, 10),
        ("5511", 0, 10),
        ("5511", True, 10),
        ("5511", 105, 0),
        ("5511", 105, True),
    ],
)
def test_invalid_arguments_fail_before_any_request(tmp_path: Path, company_id: Any, roc_year: Any, max_details: Any):
    transport = _Transport([])

    with pytest.raises(ValueError):
        evidence.capture(company_id, roc_year, output_dir=tmp_path / "new", max_details=max_details, transport=transport)

    assert transport.calls == []
    assert not (tmp_path / "new").exists()


def test_existing_output_directory_is_preserved_without_request(tmp_path: Path):
    output_dir = tmp_path / "evidence"
    output_dir.mkdir()
    sentinel = output_dir / "do-not-touch.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    transport = _Transport([])

    with pytest.raises(FileExistsError):
        evidence.capture("5511", 105, output_dir=output_dir, transport=transport)

    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert transport.calls == []


def test_reordered_list_titles_map_raw_fields_by_name(tmp_path: Path):
    titles = ["主旨", "內容", "公告日期", "修正後現金股利發放日", "申報序號", "種類"]
    original = _row()
    row_by_title: dict[str, Any] = dict(zip(("公告日期", "申報序號", "主旨", "種類", "內容", "修正後現金股利發放日"), original, strict=True))
    reordered_row = [row_by_title[title] for title in titles]
    transport = _Transport([
        _json_reply(_list_payload([reordered_row], titles=[{"main": title, "sub": []} for title in titles])),
        _json_reply(_detail_payload()),
    ])

    manifest = _capture(tmp_path, transport)

    assert manifest["status"] == "complete"
    assert manifest["announcements"][0]["subject_raw"] == original[2]
    assert manifest["announcements"][0]["detail_parameters"]["serialNumber"] == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda row: row[4].update(apiName="t108sb19_detail_evil"),
        lambda row: row[4]["parameters"].update(unexpected="value"),
        lambda row: row[4]["parameters"].update(serialNumber=True),
        lambda row: row[4]["parameters"].update(companyId="9999"),
    ],
)
def test_hostile_detail_route_or_parameters_are_never_requested(tmp_path: Path, mutate: Callable[[list[Any]], None]):
    row = _row()
    mutate(row)
    transport = _Transport([_json_reply(_list_payload([row]))])

    manifest = _capture(tmp_path, transport)

    assert manifest["status"] == "error"
    assert manifest["captured_details"] == 0
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "bad_detail",
    [
        _detail_payload(company_id="9999"),
        _detail_payload(rows=[]),
        _detail_payload(rows=[[1, "subject"], [1, "subject"]]),
        _detail_payload(titles=[{"main": "一、公告序號：", "sub": []}]),
        _detail_payload(serial=2),
        _detail_payload(subject="different subject"),
    ],
)
def test_detail_identity_and_shape_failures_are_retained_without_retry(tmp_path: Path, bad_detail: dict[str, Any]):
    reply = _json_reply(bad_detail)
    transport = _Transport([_json_reply(_list_payload([_row()])), reply])

    manifest = _capture(tmp_path, transport)

    assert manifest["status"] == "error"
    assert manifest["captured_details"] == 0
    assert manifest["unprocessed_rows"] == 1
    assert len(transport.calls) == 2
    assert any(path.read_bytes() == reply.body for path in (tmp_path / "evidence").rglob("*.json"))


class _StreamingResponse:
    status_code = 200
    url = "https://mops.twse.com.tw/mops/api/t108sb19"

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    def iter_content(self, chunk_size: int):
        assert chunk_size > 0
        yield from self.chunks

    def __enter__(self) -> _StreamingResponse:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        return None


def test_post_json_posts_once_without_redirects_or_retries(monkeypatch: pytest.MonkeyPatch):
    calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def fake_post(url: str, json: dict[str, Any], **kwargs: Any) -> _StreamingResponse:
        calls.append((url, json, kwargs))
        return _StreamingResponse([b'{"code":', b"200}"])

    monkeypatch.setattr(requests, "post", fake_post)

    reply = evidence.post_json(evidence.LIST_URL, {"companyId": "5511"})

    assert reply == evidence.HttpReply(200, "https://mops.twse.com.tw/mops/api/t108sb19", b'{"code":200}')
    assert calls == [
        (
            evidence.LIST_URL,
            {"companyId": "5511"},
            {"headers": {"User-Agent": evidence.DEFAULT_USER_AGENT}, "stream": True, "allow_redirects": False, "timeout": (20, 20)},
        )
    ]


def test_post_json_rejects_hostile_route_without_calling_requests(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(requests, "post", pytest.fail)

    with pytest.raises(evidence.EvidenceError, match="unsupported_request_url"):
        evidence.post_json("https://mops.twse.com.tw/mops/api/t108sb19_evil", {})


def test_post_json_rejects_oversized_stream(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(requests, "post", lambda *_args, **_kwargs: _StreamingResponse([b"a" * (evidence.MAX_RESPONSE_BYTES + 1)]))

    with pytest.raises(evidence.EvidenceError, match="response_too_large"):
        evidence.post_json(evidence.LIST_URL, {"companyId": "5511"})


def test_capture_url_mismatch_and_transport_timeout_are_not_retried(tmp_path: Path):
    redirected = _json_reply(_list_payload([]), url="https://mops.twse.com.tw/other")
    transport = _Transport([redirected])
    manifest = _capture(tmp_path, transport)
    assert manifest["status"] == "error"
    assert manifest["requests"][0]["error_category"] == "http_status_or_url_mismatch"
    assert len(transport.calls) == 1

    calls: list[tuple[str, dict[str, Any]]] = []

    def timeout(url: str, parameters: dict[str, Any]) -> _Response:
        calls.append((url, parameters))
        raise requests.Timeout("offline timeout")

    manifest = evidence.capture("5511", 105, output_dir=tmp_path / "timeout", transport=timeout)
    assert manifest["status"] == "error"
    assert manifest["requests"][0]["error_category"] == "Timeout"
    assert calls == [(evidence.LIST_URL, {"companyId": "5511", "dataType": "2", "year": "105", "month": "all", "firstDay": "", "lastDay": ""})]


@pytest.mark.parametrize("status,url,code", [(503, evidence.LIST_URL, 500), (200, "https://mops.twse.com.tw/other", 200), (200, evidence.LIST_URL, 500)])
def test_failed_reply_keeps_parseable_application_provenance(tmp_path: Path, status: int, url: str, code: int):
    packet = {"code": code, "datetime": "115/09/09 18:41:11", "result": {}}
    reply = _json_reply(packet, status=status, url=url)
    manifest = _capture(tmp_path, _Transport([reply]))
    request = manifest["requests"][0]
    assert manifest["status"] == "error"
    assert request["application_code"] == code
    assert request["server_response_datetime_raw"] == packet["datetime"]
    assert _raw_path(tmp_path / "evidence", request).read_bytes() == reply.body


def test_raw_write_failure_does_not_publish_final_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    real_write = evidence._write_new

    def fail_raw(path: Path, body: bytes):
        if path.name.startswith("response-"):
            raise PermissionError("offline filesystem failure")
        real_write(path, body)

    monkeypatch.setattr(evidence, "_write_new", fail_raw)
    with pytest.raises(PermissionError):
        _capture(tmp_path, _Transport([_json_reply(_list_payload([]))]))
    assert not (tmp_path / "evidence" / "manifest.json").exists()


@pytest.mark.parametrize("empty", [True, False])
def test_success_never_certifies_history_pit_or_revisions(tmp_path: Path, empty: bool):
    replies = [_json_reply(_list_payload([] if empty else [_row()]))]
    if not empty:
        replies.append(_json_reply(_detail_payload()))
    manifest = _capture(tmp_path, _Transport(replies))
    assert manifest["status"] == ("source_empty" if empty else "complete")
    for key in ("historical_coverage", "historical_public_availability", "revision_completeness"):
        assert manifest[key] == "unverified"


def test_reordered_detail_titles_map_identity_by_name(tmp_path: Path):
    detail = _detail_payload()
    detail["result"]["titles"].reverse()
    detail["result"]["data"][0].reverse()
    manifest = _capture(tmp_path, _Transport([_json_reply(_list_payload([_row()])), _json_reply(detail)]))
    assert manifest["status"] == "complete"
