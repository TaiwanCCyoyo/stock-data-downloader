from __future__ import annotations

import hashlib
import json
from importlib import import_module
from pathlib import Path

import pytest
import requests

from shp_utils.corporate_detail_capture import (
    FIELDS,
    CorporateDetailCaptureError,
    HttpReply,
    capture_corporate_details,
    load_plan,
)


def make_plan(tmp_path: Path, source: str = "twt49u") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    label = "資料日期" if source == "twt49u" else "恢復買賣日期"
    raw = {"fields": ["股票代號", label, "詳細資料"], "data": [["1598", "112年05月26日", "1598,20230526"]]}
    annual = tmp_path / "annual.json"
    annual.write_text(json.dumps(raw), encoding="utf-8")
    plan = {
        "schema": "corporate-detail-plan-v1",
        "events": [{"source": source, "raw_path": annual.name, "raw_sha256": hashlib.sha256(annual.read_bytes()).hexdigest(), "row_index": 0}],
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def reply(source: str, code: str = "1598") -> bytes:
    fields = list(FIELDS[source])
    return json.dumps({"stat": "OK", "fields": fields, "data": [[code, *["literal"] * (len(fields) - 1)]]}).encode()


def test_capture_preserves_literal_fields_and_unknown_times(tmp_path: Path) -> None:
    plan = make_plan(tmp_path)
    manifest = capture_corporate_details(
        plan_path=plan,
        output_dir=tmp_path / "out",
        max_requests=1,
        max_seconds=10,
        interval=1,
        transport=lambda url, timeout: HttpReply(200, url, reply("twt49u")),
    )
    assert manifest["status"] == "complete"
    event = json.loads((tmp_path / "out" / "events.jsonl").read_text(encoding="utf-8"))
    assert event["literal_fields"]["股票代號"] == "1598"
    assert event["historical_availability"] is None and event["delivery_time"] is None
    assert manifest["output_sha256"]["requests.jsonl"]


def test_hash_drift_rejects_without_output(tmp_path: Path) -> None:
    plan = make_plan(tmp_path)
    body = json.loads(plan.read_text(encoding="utf-8"))
    body["events"][0]["raw_sha256"] = "0" * 64
    plan.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(CorporateDetailCaptureError, match="raw_sha256"):
        capture_corporate_details(plan_path=plan, output_dir=tmp_path / "out", max_requests=1, max_seconds=1, interval=1)
    assert not (tmp_path / "out").exists()


def test_unhashable_plan_source_and_raw_field_are_domain_errors(tmp_path: Path) -> None:
    plan = make_plan(tmp_path)
    body = json.loads(plan.read_text(encoding="utf-8"))
    body["events"][0]["source"] = []
    plan.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(CorporateDetailCaptureError, match="identity"):
        capture_corporate_details(plan_path=plan, output_dir=tmp_path / "source-out", max_requests=1, max_seconds=1, interval=1)

    plan = make_plan(tmp_path / "fields")
    annual = tmp_path / "fields" / "annual.json"
    raw = json.loads(annual.read_text(encoding="utf-8"))
    raw["fields"][0] = []
    annual.write_text(json.dumps(raw), encoding="utf-8")
    body = json.loads(plan.read_text(encoding="utf-8"))
    body["events"][0]["raw_sha256"] = hashlib.sha256(annual.read_bytes()).hexdigest()
    plan.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(CorporateDetailCaptureError, match="schema"):
        capture_corporate_details(plan_path=plan, output_dir=tmp_path / "fields-out", max_requests=1, max_seconds=1, interval=1)


def test_failure_retains_raw_request_receipt_and_stops(tmp_path: Path) -> None:
    plan = make_plan(tmp_path)
    manifest = capture_corporate_details(
        plan_path=plan, output_dir=tmp_path / "out", max_requests=1, max_seconds=10, interval=1, transport=lambda url, timeout: HttpReply(500, url, b"blocked")
    )
    assert manifest["status"] == "incomplete" and manifest["attempted_requests"] == 1
    assert (tmp_path / "out" / "raw-001.json").read_bytes() == b"blocked"
    request = json.loads((tmp_path / "out" / "requests.jsonl").read_text(encoding="utf-8"))
    assert request["outcome"] == "failed"


@pytest.mark.parametrize("source", ["twt49u", "twtauu"])
def test_reordered_schema_binds_named_code(tmp_path: Path, source: str) -> None:
    packet = json.loads(reply(source))
    packet["fields"].reverse()
    packet["data"][0].reverse()
    result = capture_corporate_details(
        plan_path=make_plan(tmp_path, source),
        output_dir=tmp_path / "out",
        max_requests=1,
        max_seconds=10,
        transport=lambda url, timeout: HttpReply(200, url, json.dumps(packet).encode()),
    )
    assert result["status"] == "complete"


@pytest.mark.parametrize("mutation", ["empty", "stat", "non_string", "bad_field", "wrong_code", "redirect", "html"])
def test_bad_response_keeps_partial_manifest(tmp_path: Path, mutation: str) -> None:
    packet = json.loads(reply("twt49u"))
    if mutation == "empty":
        packet["data"] = []
    elif mutation == "stat":
        packet["stat"] = "denied"
    elif mutation == "non_string":
        packet["data"][0][2] = None
    elif mutation == "bad_field":
        packet["fields"][0] = []
    elif mutation == "wrong_code":
        packet["data"][0][0] = "9999"
    body = b"<html>denied</html>" if mutation == "html" else json.dumps(packet).encode()
    result = capture_corporate_details(
        plan_path=make_plan(tmp_path),
        output_dir=tmp_path / "out",
        max_requests=1,
        max_seconds=10,
        transport=lambda url, timeout: HttpReply(302 if mutation == "redirect" else 200, url, body),
    )
    assert result["status"] == "incomplete"
    assert result["completed_events"] == 0
    assert (tmp_path / "out" / "raw-001.json").read_bytes() == body
    assert json.loads((tmp_path / "out" / "manifest.json").read_text())["attempted_requests"] == 1


def test_network_failure_has_no_retrieval_time(tmp_path: Path) -> None:
    def fail(url: str, timeout: float) -> HttpReply:
        raise requests.Timeout("synthetic")

    result = capture_corporate_details(
        plan_path=make_plan(tmp_path),
        output_dir=tmp_path / "out",
        max_requests=1,
        max_seconds=10,
        transport=fail,
    )
    record = json.loads((tmp_path / "out" / "requests.jsonl").read_text())
    assert result["stop_reason"] == "Timeout"
    assert record["requested_at_utc"] and record["retrieved_at_utc"] is None


def test_duplicate_plan_never_requests(tmp_path: Path) -> None:
    path = make_plan(tmp_path)
    plan = json.loads(path.read_text())
    plan["events"] *= 2
    path.write_text(json.dumps(plan))
    with pytest.raises(CorporateDetailCaptureError, match="duplicate"):
        load_plan(path)


def test_budget_clips_timeout_and_stops_before_second_request(tmp_path: Path) -> None:
    path = make_plan(tmp_path)
    raw_path = tmp_path / "annual.json"
    raw = json.loads(raw_path.read_text())
    raw["data"].append(["1598", "112/05/27", "1598,20230527"])
    raw_path.write_text(json.dumps(raw))
    plan = json.loads(path.read_text())
    plan["events"][0]["raw_sha256"] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    plan["events"].append({**plan["events"][0], "row_index": 1})
    path.write_text(json.dumps(plan))
    ticks = iter([0.0, 1.0, 9.5])
    timeouts = []

    def fetch(url: str, timeout: float) -> HttpReply:
        timeouts.append(timeout)
        return HttpReply(200, url, reply("twt49u"))

    result = capture_corporate_details(
        plan_path=path,
        output_dir=tmp_path / "out",
        max_requests=2,
        max_seconds=10,
        timeout=30,
        interval=1,
        transport=fetch,
        monotonic=lambda: next(ticks),
    )
    assert timeouts == [9.0]
    assert result["status"] == "incomplete" and result["completed_events"] == 1
    assert result["stop_reason"] == "budget_exhausted"


@pytest.mark.parametrize("interval", [0, -1, float("nan"), float("inf")])
def test_invalid_pacing_rejected(tmp_path: Path, interval: float) -> None:
    with pytest.raises(CorporateDetailCaptureError, match="interval"):
        capture_corporate_details(plan_path=make_plan(tmp_path), output_dir=tmp_path / "out", max_requests=1, max_seconds=10, interval=interval)
    assert not (tmp_path / "out").exists()


def test_cli_reports_incomplete_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    cli = import_module("capture_corporate_details")

    monkeypatch.setattr("sys.argv", ["capture", "--plan", "unused", "--output", "unused", "--max-requests", "1", "--max-seconds", "10"])
    monkeypatch.setattr(cli, "capture_corporate_details", lambda **kwargs: {"status": "incomplete"})
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 1


def test_retrieval_timestamp_follows_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from shp_utils import corporate_detail_capture as module

    moments = iter(["start", "request", "retrieved", "finish"])
    monkeypatch.setattr(module, "_now", lambda: next(moments))
    capture_corporate_details(
        plan_path=make_plan(tmp_path),
        output_dir=tmp_path / "out",
        max_requests=1,
        max_seconds=10,
        transport=lambda url, timeout: HttpReply(200, url, reply("twt49u")),
    )
    record = json.loads((tmp_path / "out" / "requests.jsonl").read_text())
    assert record["requested_at_utc"] == "request"
    assert record["retrieved_at_utc"] == "retrieved"


def test_reduction_key_can_precede_restoration(tmp_path: Path) -> None:
    path = make_plan(tmp_path, "twtauu")
    annual = tmp_path / "annual.json"
    raw = json.loads(annual.read_text())
    raw["data"][0][1] = "112/06/06"
    annual.write_text(json.dumps(raw))
    plan = json.loads(path.read_text())
    plan["events"][0]["raw_sha256"] = hashlib.sha256(annual.read_bytes()).hexdigest()
    path.write_text(json.dumps(plan))
    events, _ = load_plan(path)
    assert events[0].effective_date == "2023-06-06"
    assert events[0].key_date == "20230526"


def test_existing_output_not_touched(tmp_path: Path) -> None:
    path = make_plan(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("preserved")
    with pytest.raises(FileExistsError):
        capture_corporate_details(plan_path=path, output_dir=output, max_requests=1, max_seconds=10)
    assert sentinel.read_text() == "preserved"


def test_live_transport_disables_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from shp_utils import corporate_detail_capture as module

    calls = []

    def fetch(url: str, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=302, url=url, content=b"redirect")

    monkeypatch.setattr(module.requests, "get", fetch)
    result = module._live("https://www.twse.com.tw/fixed", 3)
    assert result.status == 302
    assert len(calls) == 1 and calls[0]["allow_redirects"] is False
    assert calls[0]["timeout"] == 3
