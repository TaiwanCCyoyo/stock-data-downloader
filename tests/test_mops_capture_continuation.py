"""Offline tests for the opt-in, event-targeted MOPS capture loop.

The default loop stops at the first failed detail, which leaves every later,
independent announcement unasked. The opt-in hooks let a caller continue past a
*proven* business refusal while every other failure still stops the capture.
These tests pin both halves: the default is unchanged, and only refusals whose
exact observed shape is recognised are allowed to continue.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from shp_utils import mops_dividend_evidence as evidence
from shp_utils import mops_raw_evidence as raw

SUBJECT = "本公司105年分派現金股利基準日公告"


_Response = raw.HttpReply


def _reply(payload: dict[str, Any], url: str, status: int = 200) -> _Response:
    return _Response(status, url, json.dumps(payload, ensure_ascii=False).encode())


def _row(serial: int) -> list[Any]:
    return [
        "105/06/28",
        str(serial),
        SUBJECT,
        "",
        {
            "parameters": {
                "declarationDate": "1050624",
                "etn_no": "",
                "companyId": "5511",
                "serialNumber": serial,
                "detailReportKind": "A",
            },
            "apiName": "t108sb19_detail",
        },
        "無",
    ]


def _list(serials: list[int]) -> _Response:
    titles: list[dict[str, Any]] = [{"main": n, "sub": []} for n in ("公告日期", "申報序號", "主旨", "種類", "內容", "修正後現金股利發放日")]
    payload = {
        "code": 200,
        "result": {"companyId": "5511", "recordDateAnnouncement": {"titles": titles, "data": [_row(s) for s in serials]}},
    }
    return _reply(payload, evidence.LIST_URL)


def _detail(serial: int) -> _Response:
    payload = {
        "code": 200,
        "result": {
            "companyId": "5511",
            "titles": [{"main": "一、公告序號：", "sub": []}, {"main": "二、主旨：", "sub": []}],
            "data": [[serial, SUBJECT]],
        },
    }
    return _reply(payload, evidence.DETAIL_URL)


def _refusal(message: str = "該 5511 公開發行公司不繼續公開發行！", **overrides: Any) -> _Response:
    payload: dict[str, Any] = {"code": 500, "datetime": "115/09/11 20:00:00", "message": message, "result": None}
    payload.update(overrides)
    return _reply(payload, evidence.DETAIL_URL)


class _Transport:
    def __init__(self, replies: list[_Response]) -> None:
        self.replies = list(replies)
        self.calls = 0

    def __call__(self, url: str, parameters: dict[str, Any]) -> _Response:
        self.calls += 1
        return self.replies.pop(0)


def _capture(tmp_path: Path, replies: list[_Response], **kwargs: Any) -> tuple[dict[str, Any], _Transport]:
    transport = _Transport(replies)
    manifest = evidence.capture("5511", 105, output_dir=tmp_path / "out", transport=transport, **kwargs)
    return manifest, transport


def _statuses(manifest: dict[str, Any]) -> list[str]:
    return [row["status"] for row in manifest["announcements"]]


def _assert_partition(manifest: dict[str, Any]) -> None:
    accounting = manifest["row_accounting"]
    assert sum(accounting.values()) == manifest["total_listed"]
    assert set(accounting) == set(raw.ROW_BUCKETS)


# --- the default path must not change ---------------------------------------


def test_default_capture_still_stops_at_the_first_refused_detail(tmp_path: Path):
    manifest, transport = _capture(tmp_path, [_list([1, 2]), _refusal()])

    assert manifest["status"] == "error"
    assert manifest["error_category"] == "application_error"
    assert _statuses(manifest) == ["failed", "unprocessed"]
    assert manifest["unprocessed_rows"] == 2  # the original contract: total - captured
    assert "row_accounting" not in manifest
    assert "capture_policy" not in manifest
    assert transport.calls == 2


# --- proven refusals continue -----------------------------------------------


def test_an_issuer_refusal_marks_the_row_and_skips_the_rest_of_that_issuer(tmp_path: Path):
    manifest, transport = _capture(tmp_path, [_list([1, 2, 3]), _refusal()], continue_after_business_refusal=True)

    assert _statuses(manifest) == ["unavailable", "not_attempted_issuer_refused", "not_attempted_issuer_refused"]
    assert manifest["announcements"][0]["unavailable_reason"] == "issuer:no_longer_public_issuer"
    assert manifest["status"] == "complete_with_unavailable"
    assert manifest["requests"][-1]["outcome"] == "business_refusal"
    assert manifest["row_accounting"]["unprocessed"] == 0
    assert manifest["unprocessed_rows"] == 0
    assert transport.calls == 2  # the refused issuer is not asked again
    _assert_partition(manifest)


def test_an_issuer_refusal_marks_selected_rows_beyond_the_detail_budget(tmp_path: Path):
    manifest, transport = _capture(
        tmp_path,
        [_list([1, 2, 3]), _refusal()],
        max_details=1,
        continue_after_business_refusal=True,
    )

    assert _statuses(manifest) == ["unavailable", "not_attempted_issuer_refused", "not_attempted_issuer_refused"]
    assert manifest["unprocessed_rows"] == 0
    assert transport.calls == 2
    _assert_partition(manifest)


def test_a_delisted_issuer_refusal_is_recognised_separately(tmp_path: Path):
    manifest, _ = _capture(tmp_path, [_list([1]), _refusal("該 5511 上市公司已下市！")], continue_after_business_refusal=True)

    assert manifest["announcements"][0]["unavailable_reason"] == "issuer:listed_company_delisted"
    assert manifest["status"] == "complete_with_unavailable"


def test_a_row_scoped_refusal_continues_with_the_next_independent_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(raw, "classify_business_refusal", lambda body, company_id: "event:detail_unavailable")
    manifest, transport = _capture(tmp_path, [_list([1, 2]), _refusal(), _detail(2)], continue_after_business_refusal=True)

    assert _statuses(manifest) == ["unavailable", "captured"]
    assert manifest["captured_details"] == 1
    assert manifest["status"] == "complete_with_unavailable"
    assert transport.calls == 3
    _assert_partition(manifest)


# --- everything else still stops --------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        _refusal("該 9999 公開發行公司不繼續公開發行！"),  # names a different company
        _refusal("該 5511 系統維護中！"),  # an unfamiliar message
        _refusal(extra="field"),  # an unobserved key
        _refusal(result={}),  # result is not null
    ],
)
def test_an_unrecognised_application_error_still_ends_the_capture(tmp_path: Path, reply: _Response):
    manifest, transport = _capture(tmp_path, [_list([1, 2]), reply], continue_after_business_refusal=True)

    assert manifest["status"] == "error"
    assert _statuses(manifest) == ["failed", "unprocessed"]
    assert manifest["row_accounting"]["unprocessed"] == 1
    assert transport.calls == 2
    _assert_partition(manifest)


@pytest.mark.parametrize("status", [403, 429, 500])
def test_an_http_error_is_never_treated_as_a_business_refusal(tmp_path: Path, status: int):
    blocked = _Response(status, evidence.DETAIL_URL, _refusal().body)
    manifest, _ = _capture(tmp_path, [_list([1, 2]), blocked], continue_after_business_refusal=True)

    assert manifest["status"] == "error"
    assert manifest["error_category"] == "http_status_or_url_mismatch"
    assert _statuses(manifest) == ["failed", "unprocessed"]


def test_a_schema_error_after_a_refusal_stops_and_keeps_the_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(raw, "classify_business_refusal", lambda body, company_id: "event:detail_unavailable")
    broken = _reply({"code": 200, "result": {"companyId": "5511", "titles": [], "data": []}}, evidence.DETAIL_URL)
    manifest, _ = _capture(tmp_path, [_list([1, 2, 3]), _refusal(), broken], continue_after_business_refusal=True)

    assert _statuses(manifest) == ["unavailable", "failed", "unprocessed"]
    assert manifest["status"] == "partial"  # a refusal is an observed answer, so this is not a bare error
    _assert_partition(manifest)


# --- selection and early stop ----------------------------------------------


def test_rows_the_selector_omits_are_not_selected_not_unprocessed(tmp_path: Path):
    manifest, transport = _capture(
        tmp_path,
        [_list([1, 2, 3]), _detail(2)],
        select_rows=lambda rows: [r for r in rows if r["serial_number_raw"] == "2"],
    )

    assert _statuses(manifest) == ["not_selected", "captured", "not_selected"]
    assert manifest["row_accounting"]["not_selected"] == 2
    assert manifest["row_accounting"]["unprocessed"] == 0
    assert manifest["status"] == "complete"
    assert transport.calls == 2


@pytest.mark.parametrize(
    "select_rows",
    [
        lambda rows: [dict(rows[0])],
        lambda rows: [rows[0], rows[0]],
        lambda rows: [{"status": "unprocessed"}],
    ],
    ids=("copy", "duplicate", "foreign"),
)
def test_selector_rejects_non_unique_or_non_original_rows_before_details(
    tmp_path: Path,
    select_rows: Any,
):
    manifest, transport = _capture(tmp_path, [_list([1, 2])], select_rows=select_rows)

    assert manifest["status"] == "error"
    assert manifest["error_category"] == "invalid_selected_rows"
    assert _statuses(manifest) == ["unprocessed", "unprocessed"]
    assert transport.calls == 1
    assert len(manifest["requests"]) == 1
    _assert_partition(manifest)


def test_stop_after_leaves_the_rest_marked_target_found(tmp_path: Path):
    manifest, transport = _capture(tmp_path, [_list([1, 2, 3]), _detail(1)], stop_after=lambda row, detail: True)

    assert _statuses(manifest) == ["captured", "not_attempted_target_found", "not_attempted_target_found"]
    assert manifest["status"] == "complete"
    assert transport.calls == 2
    _assert_partition(manifest)


def test_rows_beyond_max_details_stay_unprocessed_and_make_the_unit_partial(tmp_path: Path):
    manifest, transport = _capture(tmp_path, [_list([1, 2, 3]), _detail(1)], max_details=1, select_rows=list)

    assert _statuses(manifest) == ["captured", "unprocessed", "unprocessed"]
    assert manifest["status"] == "partial"
    assert transport.calls == 2


def test_an_empty_list_is_source_empty(tmp_path: Path):
    manifest, _ = _capture(tmp_path, [_list([])], continue_after_business_refusal=True)

    assert manifest["status"] == "source_empty"
    assert manifest["total_listed"] == 0


# --- the classifier on its own ------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("該 5511 公開發行公司不繼續公開發行！", "issuer:no_longer_public_issuer"),
        ("該 5511 上市公司已下市！", "issuer:listed_company_delisted"),
        ("該 1234 上市公司已下市！", None),
        ("該 5511 上市公司已下市", None),
        ("5511 上市公司已下市！", None),
    ],
)
def test_classify_business_refusal_matches_only_the_observed_messages(message: str, expected: str | None):
    body = json.dumps({"code": 500, "datetime": "x", "message": message, "result": None}, ensure_ascii=False).encode()
    assert raw.classify_business_refusal(body, "5511") == expected


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        json.dumps({"code": 200, "datetime": "x", "message": "該 5511 上市公司已下市！", "result": None}, ensure_ascii=False).encode(),
        json.dumps({"code": "500", "datetime": "x", "message": "該 5511 上市公司已下市！", "result": None}, ensure_ascii=False).encode(),
        json.dumps({"code": 500, "message": "該 5511 上市公司已下市！", "result": None}, ensure_ascii=False).encode(),
    ],
)
def test_classify_business_refusal_rejects_every_other_shape(body: bytes):
    assert raw.classify_business_refusal(body, "5511") is None


def test_every_observed_refusal_body_from_2026_09_11_is_recognised():
    """The classifier was written from these bodies; this keeps it honest against them."""
    root = Path(__file__).resolve().parents[2] / ".tmp"
    manifests = sorted(root.glob("claude-secondary-data-*/s1-dividend-statements/captures/*/manifest.json"))
    if not manifests:
        pytest.skip("the 2026-09-11 capture tree is not present on this machine")
    seen = 0
    for path in manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for record in manifest["requests"]:
            if record.get("error_category") != "application_error" or not record.get("raw_file"):
                continue
            body = (path.parent / record["raw_file"]).read_bytes()
            assert raw.classify_business_refusal(body, manifest["company_id"]) is not None, path
            seen += 1
    assert seen == 46
