"""Bounded, provenance-preserving TWSE corporate-detail capture."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import requests

ORIGIN = "https://www.twse.com.tw"
PLAN_SCHEMA = "corporate-detail-plan-v1"
SOURCES = frozenset(("twt49u", "twtauu"))
PATHS = {"twt49u": "/rwd/zh/exRight/TWT49UDetail", "twtauu": "/rwd/zh/reducation/TWTAVUDetail"}
FIELDS = {
    "twt49u": (
        "股票代號",
        "股票名稱",
        "(每股配發現金股利)除息",
        "(增資配股) 除權",
        "A. 按普通股股東持股比例每千股無償配股",
        "B. 員工紅利轉增資",
        "C. (有償) 現金增資",
        "每股認購金額",
        "a. 公開承銷",
        "b. 員工認購",
        " c. 原股東認購",
        "按股東持股比例每千股認購",
    ),
    "twtauu": (
        "股票代號：",
        "股票名稱：",
        "停止買賣日期：",
        "每壹仟股換發新股票：",
        "每股退還股款：",
        "原股每股配發現金股利：",
        "減資並(有償)現金增資：",
        "每股認購金額：",
        "a. 公開承銷：",
        "b. 員工認購：",
        "c. 原股東認購：",
        "按股東持股比例每千股認購：",
    ),
}


class CorporateDetailCaptureError(ValueError):
    pass


@dataclass(frozen=True)
class Event:
    source: str
    raw_path: Path
    raw_sha256: str
    row_index: int
    code: str
    effective_date: str
    key_date: str
    detail_key: str


@dataclass(frozen=True)
class HttpReply:
    status: int
    url: str
    body: bytes


def load_plan(path: Path | str) -> tuple[list[Event], bytes]:
    plan_path = Path(path)
    body = plan_path.read_bytes()
    try:
        plan = json.loads(body)
    except ValueError as error:
        raise CorporateDetailCaptureError("plan is not JSON") from error
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA or not isinstance(plan.get("events"), list) or not plan["events"]:
        raise CorporateDetailCaptureError("plan must be nonempty corporate-detail-plan-v1")
    events = [_event(item, plan_path.parent) for item in plan["events"]]
    if len({(item.source, item.code, item.key_date) for item in events}) != len(events):
        raise CorporateDetailCaptureError("duplicate endpoint keys")
    return events, body


def capture_corporate_details(
    *,
    plan_path: Path | str,
    output_dir: Path | str,
    max_requests: int,
    max_seconds: float,
    timeout: float = 30.0,
    interval: float = 1.0,
    transport: Callable[[str, float], HttpReply] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    _positive("max_requests", max_requests, integer=True)
    _positive("max_seconds", max_seconds)
    _positive("timeout", timeout)
    _positive("interval", interval)
    events, plan_bytes = load_plan(plan_path)
    if len(events) > max_requests:
        raise CorporateDetailCaptureError("plan event count exceeds max_requests")
    target = Path(output_dir)
    if target.exists():
        raise FileExistsError("output directory already exists")
    target.mkdir(parents=True)
    _write(target / "plan.json", plan_bytes)
    start = monotonic()
    manifest: dict[str, Any] = {
        "schema_version": "corporate-detail-capture-v1",
        "status": "incomplete",
        "plan_sha256": _sha(plan_bytes),
        "code_sha256": {
            "helper": _sha(Path(__file__).read_bytes()),
            "cli": _sha((Path(__file__).parents[1] / "scripts" / "capture_corporate_details.py").read_bytes()),
        },
        "budgets": {"max_requests": max_requests, "max_seconds": max_seconds, "timeout": timeout, "interval": interval},
        "input_events": [
            {"source": item.source, "code": item.code, "effective_date": item.effective_date, "raw_sha256": item.raw_sha256, "row_index": item.row_index}
            for item in events
        ],
        "event_count": len(events),
        "attempted_requests": 0,
        "completed_events": 0,
        "historical_availability": None,
        "public_availability": None,
        "delivery_time": None,
        "requests_file": "requests.jsonl",
        "events_file": "events.jsonl",
        "started_at_utc": _now(),
    }
    reason = None
    for number, event in enumerate(events, 1):
        remaining = max_seconds - (monotonic() - start)
        if remaining <= 0:
            reason = "budget_exhausted"
            break
        url = _url(event)
        record: dict[str, Any] = {
            "request_index": number,
            "source": event.source,
            "code": event.code,
            "effective_date": event.effective_date,
            "key_date": event.key_date,
            "request_url": url,
            "raw_path": str(event.raw_path),
            "raw_sha256": event.raw_sha256,
            "row_index": event.row_index,
            "requested_at_utc": _now(),
            "retrieved_at_utc": None,
            "http_status": None,
            "raw_file": None,
            "response_sha256": None,
            "outcome": "failed",
        }
        manifest["attempted_requests"] += 1
        try:
            reply = (transport or _live)(url, min(timeout, remaining))
            record["retrieved_at_utc"] = _now()
            record["http_status"] = reply.status
            raw_file = f"raw-{number:03d}.json"
            _write(target / raw_file, reply.body)
            record["raw_file"] = raw_file
            record["response_sha256"] = _sha(reply.body)
            if reply.status != 200 or reply.url != url:
                raise CorporateDetailCaptureError("http_status_or_redirect")
            _append(target / "events.jsonl", _detail(reply.body, event, record))
            record["outcome"] = "captured"
            manifest["completed_events"] += 1
        except (CorporateDetailCaptureError, requests.RequestException) as error:
            reason = str(error) if isinstance(error, CorporateDetailCaptureError) else type(error).__name__
            record["error"] = reason
        finally:
            _append(target / "requests.jsonl", record)
        if reason:
            break
        if number < len(events):
            remaining = max_seconds - (monotonic() - start)
            if interval > remaining:
                reason = "budget_exhausted"
                break
            if interval:
                sleep(float(interval))
    manifest["status"] = "complete" if manifest["completed_events"] == len(events) else "incomplete"
    manifest["stop_reason"] = reason
    manifest["finished_at_utc"] = _now()
    manifest["output_sha256"] = {
        name: _sha((target / name).read_bytes()) if (target / name).is_file() else None for name in ("plan.json", "requests.jsonl", "events.jsonl")
    }
    _write(target / "manifest.json", _dump(manifest, pretty=True))
    return manifest


def _event(value: Any, root: Path) -> Event:
    if not isinstance(value, dict) or set(value) != {"source", "raw_path", "raw_sha256", "row_index"}:
        raise CorporateDetailCaptureError("invalid plan event fields")
    source, digest, index = value["source"], value["raw_sha256"], value["row_index"]
    if (
        not isinstance(source, str)
        or source not in SOURCES
        or not isinstance(digest, str)
        or len(digest) != 64
        or set(digest) - set("0123456789abcdef")
        or not isinstance(index, int)
        or isinstance(index, bool)
        or index < 0
        or not isinstance(value["raw_path"], str)
    ):
        raise CorporateDetailCaptureError("invalid plan identity")
    raw_path = (root / value["raw_path"]).resolve()
    raw = raw_path.read_bytes()
    if _sha(raw) != digest:
        raise CorporateDetailCaptureError("raw_sha256 mismatch")
    try:
        packet = json.loads(raw)
        fields, rows = packet["fields"], packet["data"]
        row = rows[index]
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise CorporateDetailCaptureError("annual raw row unavailable") from error
    if (
        not isinstance(fields, list)
        or not all(isinstance(field, str) for field in fields)
        or len(fields) != len(set(fields))
        or not isinstance(row, list)
        or len(row) != len(fields)
    ):
        raise CorporateDetailCaptureError("annual raw schema invalid")
    values = dict(zip(fields, row, strict=True))
    code = _code(values.get("股票代號"))
    effective = _date(values.get("資料日期" if source == "twt49u" else "恢復買賣日期"))
    key_date, key_code, key = _key(values.get("詳細資料"))
    if key_code != code or (source == "twt49u" and key_date != effective) or (source == "twtauu" and key_date > effective):
        raise CorporateDetailCaptureError("annual row identity mismatch")
    return Event(source, raw_path, digest, index, code, effective, key_date.replace("-", ""), key)


def _detail(body: bytes, event: Event, request: Mapping[str, Any]) -> dict[str, Any]:
    try:
        packet = json.loads(body)
        fields, rows = packet["fields"], packet["data"]
    except (KeyError, TypeError, ValueError) as error:
        raise CorporateDetailCaptureError("non_json_or_missing_detail_data") from error
    if (
        not isinstance(fields, list)
        or not all(isinstance(field, str) for field in fields)
        or len(fields) != len(set(fields))
        or set(fields) != set(FIELDS[event.source])
        or not isinstance(rows, list)
        or len(rows) != 1
        or not isinstance(rows[0], list)
        or len(rows[0]) != len(fields)
    ):
        raise CorporateDetailCaptureError("unexpected_or_empty_detail_schema")
    if not isinstance(packet.get("stat"), str) or packet["stat"].lower() != "ok":
        raise CorporateDetailCaptureError("detail_status_not_ok")
    literal = dict(zip(fields, rows[0], strict=True))
    if not all(isinstance(item, str) for item in literal.values()):
        raise CorporateDetailCaptureError("detail_literal_values_must_be_strings")
    if _code(literal[FIELDS[event.source][0]]) != event.code:
        raise CorporateDetailCaptureError("detail_response_code_mismatch")
    return {
        "source": event.source,
        "code": event.code,
        "effective_date_from_annual_row": event.effective_date,
        "detail_key": event.detail_key,
        "request_key_date": event.key_date,
        "request_url": request["request_url"],
        "annual_raw_path": str(event.raw_path),
        "annual_raw_sha256": event.raw_sha256,
        "annual_row_index": event.row_index,
        "response_sha256": request["response_sha256"],
        "literal_fields": literal,
        "historical_availability": None,
        "public_availability": None,
        "delivery_time": None,
    }


def _url(event: Event) -> str:
    parameter = "T1" if event.source == "twt49u" else "FILE_DATE"
    return f"{ORIGIN}{PATHS[event.source]}?STK_NO={event.code}&{parameter}={event.key_date}&response=json"


def _live(url: str, timeout: float) -> HttpReply:
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout, allow_redirects=False)
    return HttpReply(response.status_code, str(response.url), response.content)


def _code(value: Any) -> str:
    code = value.strip() if isinstance(value, str) else ""
    if not (4 <= len(code) <= 8 and code.isascii() and code.isalnum()):
        raise CorporateDetailCaptureError("security code invalid")
    return code


def _date(value: Any) -> str:
    try:
        text = value.strip()
        if "年" in text:
            year, tail = text.split("年", 1)
            month, day = tail.replace("日", "").split("月", 1)
        else:
            year, month, day = text.split("/")
        return date(int(year) + 1911, int(month), int(day)).isoformat()
    except (AttributeError, TypeError, ValueError) as error:
        raise CorporateDetailCaptureError("annual effective date invalid") from error


def _key(value: Any) -> tuple[str, str, str]:
    if not isinstance(value, str) or value.count(",") != 1:
        raise CorporateDetailCaptureError("detail key invalid")
    code, compact = (piece.strip() for piece in value.split(","))
    if len(compact) != 8 or not compact.isascii() or not compact.isdigit():
        raise CorporateDetailCaptureError("detail key date invalid")
    try:
        key = date(int(compact[:4]), int(compact[4:6]), int(compact[6:])).isoformat()
    except ValueError as error:
        raise CorporateDetailCaptureError("detail key date invalid") from error
    return key, _code(code), value


def _positive(name: str, value: Any, *, integer: bool = False) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0 or integer and not isinstance(value, int):
        raise CorporateDetailCaptureError(f"{name} must be positive finite")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dump(value: Any, *, pretty: bool = False) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2 if pretty else None) + "\n").encode()


def _write(path: Path, body: bytes) -> None:
    with path.open("xb") as output:
        output.write(body)


def _append(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as output:
        output.write(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
