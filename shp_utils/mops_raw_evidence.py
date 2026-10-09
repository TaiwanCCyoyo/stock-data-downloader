"""Small shared engine for bounded, byte-preserving MOPS evidence captures."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from shp_utils.official_api import DEFAULT_USER_AGENT

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class EvidenceError(ValueError):
    """An unsupported or invalid response, not a successful empty history."""


@dataclass(frozen=True)
class HttpReply:
    status: int
    url: str
    body: bytes


def post_json(url: str, parameters: dict[str, Any], *, allowed_urls: tuple[str, ...]) -> HttpReply:
    """Make one fixed-route request without redirects, retries, or oversized bodies."""
    if url not in allowed_urls:
        raise EvidenceError("unsupported_request_url")
    with requests.post(
        url,
        json=parameters,
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=(20, 20),
        allow_redirects=False,
        stream=True,
    ) as response:
        body = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                raise EvidenceError("response_too_large")
            body.extend(chunk)
        return HttpReply(response.status_code, str(response.url), bytes(body))


def now() -> str:
    return datetime.now(UTC).isoformat()


def run_capture(
    *,
    company_id: str,
    scope: dict[str, Any],
    max_details: int,
    target: Path,
    schema_version: str,
    list_url: str,
    detail_url: str,
    list_parameters: dict[str, Any],
    validate_result: Callable[[Any, str], Any],
    parse_list: Callable[[Any, str], list[dict[str, Any]]],
    detail_parameters: Callable[[dict[str, Any], str], dict[str, Any]],
    validate_detail: Callable[[Any, str, dict[str, Any]], None],
    transport: Callable[[str, dict[str, Any]], HttpReply],
    write_new: Callable[[Path, bytes], None],
    injected_transport: bool,
    business_refusal: Callable[[bytes, str], str | None] | None = None,
    select_rows: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    stop_after: Callable[[dict[str, Any], Any], bool] | None = None,
) -> dict[str, Any]:
    """Archive one list and at most ``max_details`` details; source errors become manifests.

    With every optional hook left as ``None`` the behaviour is unchanged: the first
    failed detail ends the capture and later rows stay ``unprocessed``.

    The hooks are opt-in, for callers that capture specific events rather than a
    whole list:

    - ``business_refusal(body, company_id)`` returns a reason when a failed detail
      body is a *proven* business answer (the source says it will not serve this
      disclosure). That row becomes ``unavailable`` and is not retried; the capture
      continues. If the reason is issuer-scoped (prefixed ``issuer:``), the
      remaining rows are marked ``not_attempted_issuer_refused`` instead of being
      asked again, and are counted apart from both refusals and unprocessed rows.
      Anything the hook does not recognise still ends the capture as before.
    - ``select_rows(rows)`` returns the rows to attempt, in order. Rows it omits
      become ``not_selected``.
    - ``stop_after(row, detail_packet)`` returns True once the caller has what it
      needs; remaining selected rows become ``not_attempted_target_found``.

    A manifest produced with any hook carries ``row_accounting``, where every row
    is in exactly one bucket, so refusals are never folded into "not yet asked".
    """
    target.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {
        "schema_version": schema_version,
        "company_id": company_id,
        **scope,
        "max_details": max_details,
        "status": "error",
        "started_at_utc": now(),
        "historical_coverage": "unverified",
        "historical_public_availability": "unverified",
        "revision_completeness": "unverified",
        "capture_mode": "injected_transport" if injected_transport else "live",
        "requests": [],
        "announcements": [],
        "total_listed": None,
        "captured_details": 0,
        "unprocessed_rows": None,
        "output_dir": str(target.resolve()),
    }

    def fetch(url: str, parameters: dict[str, Any]) -> Any:
        record: dict[str, Any] = {
            "request_url": url,
            "request_method": "POST",
            "request_parameters": parameters,
            "started_at_utc": now(),
            "http_status": None,
            "raw_file": None,
            "sha256": None,
            "outcome": "error",
        }
        manifest["requests"].append(record)
        try:
            reply = transport(url, dict(parameters))
            record.update(http_status=reply.status, final_url=reply.url)
            if len(reply.body) > MAX_RESPONSE_BYTES:
                raise EvidenceError("response_too_large")
            raw_file = f"response-{len(manifest['requests']):03d}.json"
            write_new(target / raw_file, reply.body)
            record.update(raw_file=raw_file, sha256=hashlib.sha256(reply.body).hexdigest(), body_bytes=len(reply.body))
            packet = None
            try:
                packet = json.loads(reply.body)
            except ValueError as exc:
                record["parse_error_category"] = type(exc).__name__
            if isinstance(packet, dict):
                record["application_code"] = packet.get("code")
                record["server_response_datetime_raw"] = packet.get("datetime")
            if reply.url != url or reply.status != 200:
                raise EvidenceError("http_status_or_url_mismatch")
            if "parse_error_category" in record:
                raise EvidenceError("invalid_json_body")
            validate_result(packet, company_id)
            record["outcome"] = "application_success"
            return packet
        except (ValueError, requests.RequestException) as exc:
            record["error_category"] = str(exc) if isinstance(exc, EvidenceError) else type(exc).__name__
            raise
        finally:
            record["finished_at_utc"] = now()

    current_row: dict[str, Any] | None = None
    filesystem_failed = False
    hooked = business_refusal is not None or select_rows is not None or stop_after is not None
    if hooked:
        return _run_hooked(
            manifest=manifest,
            fetch=fetch,
            company_id=company_id,
            max_details=max_details,
            target=target,
            list_url=list_url,
            detail_url=detail_url,
            list_parameters=list_parameters,
            parse_list=parse_list,
            detail_parameters=detail_parameters,
            validate_detail=validate_detail,
            write_new=write_new,
            business_refusal=business_refusal,
            select_rows=select_rows,
            stop_after=stop_after,
        )
    try:
        packet = fetch(list_url, list_parameters)
        rows = parse_list(packet, company_id)
        manifest["requests"][-1]["outcome"] = "validated_list"
        manifest["announcements"] = rows
        manifest["total_listed"] = len(rows)
        for current_row in rows[:max_details]:
            params = detail_parameters(current_row, company_id)
            current_row["detail_request_index"] = len(manifest["requests"])
            detail = fetch(detail_url, params)
            validate_detail(detail, company_id, current_row)
            manifest["requests"][-1]["outcome"] = "validated_detail"
            current_row["status"] = "captured"
            manifest["captured_details"] += 1
        manifest["status"] = "source_empty" if not rows else "complete" if len(rows) <= max_details else "partial"
    except (ValueError, requests.RequestException) as exc:
        manifest["error_category"] = str(exc) if isinstance(exc, EvidenceError) else type(exc).__name__
        manifest["status"] = "partial" if manifest["captured_details"] else "error"
        if manifest["requests"] and manifest["requests"][-1]["outcome"] == "application_success":
            manifest["requests"][-1].update(outcome="schema_error", error_category=manifest["error_category"])
        if current_row is not None:
            current_row["status"] = "failed"
    except OSError:
        filesystem_failed = True
        raise
    finally:
        if manifest["total_listed"] is not None:
            manifest["unprocessed_rows"] = manifest["total_listed"] - manifest["captured_details"]
        manifest["finished_at_utc"] = now()
        if not filesystem_failed:
            write_new(target / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    return manifest


ROW_BUCKETS = (
    "captured",
    "unavailable",
    "failed",
    "unprocessed",
    "not_selected",
    "not_attempted_issuer_refused",
    "not_attempted_target_found",
)


def _run_hooked(
    *,
    manifest: dict[str, Any],
    fetch: Callable[[str, dict[str, Any]], Any],
    company_id: str,
    max_details: int,
    target: Path,
    list_url: str,
    detail_url: str,
    list_parameters: dict[str, Any],
    parse_list: Callable[[Any, str], list[dict[str, Any]]],
    detail_parameters: Callable[[dict[str, Any], str], dict[str, Any]],
    validate_detail: Callable[[Any, str, dict[str, Any]], None],
    write_new: Callable[[Path, bytes], None],
    business_refusal: Callable[[bytes, str], str | None] | None,
    select_rows: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None,
    stop_after: Callable[[dict[str, Any], Any], bool] | None,
) -> dict[str, Any]:
    """The opt-in, event-targeted variant of the capture loop. See ``run_capture``."""
    manifest["capture_policy"] = {
        "business_refusal_continues": business_refusal is not None,
        "row_selection": select_rows is not None,
        "stop_after_target": stop_after is not None,
    }
    current_row: dict[str, Any] | None = None
    filesystem_failed = False
    rows: list[dict[str, Any]] = []
    try:
        packet = fetch(list_url, list_parameters)
        rows = parse_list(packet, company_id)
        manifest["requests"][-1]["outcome"] = "validated_list"
        manifest["announcements"] = rows
        manifest["total_listed"] = len(rows)
        chosen = select_rows(rows) if select_rows is not None else list(rows)
        row_ids = {id(row) for row in rows}
        chosen_ids = [id(row) for row in chosen]
        if any(row_id not in row_ids for row_id in chosen_ids) or len(set(chosen_ids)) != len(chosen_ids):
            raise EvidenceError("invalid_selected_rows")
        selected_ids = set(chosen_ids)
        for row in rows:
            if id(row) not in selected_ids:
                row["status"] = "not_selected"
        attempts = chosen[:max_details]
        for row in chosen[max_details:]:
            row["status"] = "unprocessed"
        for position, current_row in enumerate(attempts):
            params = detail_parameters(current_row, company_id)
            current_row["detail_request_index"] = len(manifest["requests"])
            try:
                detail = fetch(detail_url, params)
            except EvidenceError as exc:
                record = manifest["requests"][-1]
                reason = None
                if business_refusal is not None and str(exc) == "application_error" and record.get("raw_file"):
                    reason = business_refusal((target / record["raw_file"]).read_bytes(), company_id)
                if reason is None:
                    raise
                record["outcome"] = "business_refusal"
                current_row.update(status="unavailable", unavailable_reason=reason)
                if reason.startswith("issuer:"):
                    for later in chosen[position + 1 :]:
                        later.update(status="not_attempted_issuer_refused", skipped_because=reason)
                    break
                continue
            validate_detail(detail, company_id, current_row)
            manifest["requests"][-1]["outcome"] = "validated_detail"
            current_row["status"] = "captured"
            manifest["captured_details"] += 1
            if stop_after is not None and stop_after(current_row, detail):
                for later in attempts[position + 1 :]:
                    later["status"] = "not_attempted_target_found"
                break
    except (ValueError, requests.RequestException) as exc:
        manifest["error_category"] = str(exc) if isinstance(exc, EvidenceError) else type(exc).__name__
        if manifest["requests"] and manifest["requests"][-1]["outcome"] == "application_success":
            manifest["requests"][-1].update(outcome="schema_error", error_category=manifest["error_category"])
        # List parsers seed every row as ``unprocessed``; the row being worked on
        # when the capture stops is the one that failed.
        if current_row is not None and current_row.get("status") in (None, "unprocessed"):
            current_row["status"] = "failed"
    except OSError:
        filesystem_failed = True
        raise
    finally:
        for row in rows:
            row.setdefault("status", "unprocessed")
        accounting = {bucket: sum(1 for r in rows if r["status"] == bucket) for bucket in ROW_BUCKETS}
        manifest["row_accounting"] = accounting
        if manifest["total_listed"] is not None:
            manifest["unprocessed_rows"] = accounting["unprocessed"]
        if "error_category" in manifest:
            manifest["status"] = "partial" if manifest["captured_details"] or accounting["unavailable"] else "error"
        elif not rows:
            manifest["status"] = "source_empty" if manifest["total_listed"] == 0 else "error"
        elif accounting["unprocessed"]:
            manifest["status"] = "partial"
        elif accounting["unavailable"] or accounting["not_attempted_issuer_refused"]:
            manifest["status"] = "complete_with_unavailable"
        else:
            manifest["status"] = "complete"
        manifest["finished_at_utc"] = now()
        if not filesystem_failed:
            write_new(target / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    return manifest


_ISSUER_REFUSAL_SUFFIXES = {
    "公開發行公司不繼續公開發行！": "issuer:no_longer_public_issuer",
    "上市公司已下市！": "issuer:listed_company_delisted",
}


def classify_business_refusal(body: bytes, company_id: str) -> str | None:
    """Recognise only refusal bodies that have actually been observed.

    Observed shape (46 of 46 refused detail requests, 2026-09-11): exactly the keys
    ``code``/``datetime``/``message``/``result``, ``code == 500``, ``result is None``,
    and a message ``該 <company_id> <reason>``. The message must name the requested
    company; a refusal naming anyone else is not a proven answer about this one.
    Everything else -- other codes, other keys, an unfamiliar message -- returns
    None, which keeps the collector's stop-on-error behaviour.
    """
    try:
        packet = json.loads(body)
    except ValueError:
        return None
    if not isinstance(packet, dict) or set(packet) != {"code", "datetime", "message", "result"}:
        return None
    if packet["code"] != 500 or packet["result"] is not None or not isinstance(packet["message"], str):
        return None
    prefix = f"該 {company_id} "
    if not packet["message"].startswith(prefix):
        return None
    return _ISSUER_REFUSAL_SUFFIXES.get(packet["message"][len(prefix) :])
