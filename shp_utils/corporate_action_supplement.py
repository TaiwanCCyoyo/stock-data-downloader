"""Normalize a published corporate-action supplement batch into queryable tables.

A supplement batch is an immutable directory under
``<DATA_DIR>/corporate_action_supplements/<batch_id>/`` whose ``publication.json``
lists every member with its byte count and SHA-256. This module verifies those
bytes, re-reads the machine-readable source responses with the existing pure
readers, and returns rows for four additive tables in ``corporate_actions.sqlite``.
Nothing in ``corporate_actions`` or ``corporate_action_raw_ranges`` is read or
written.

Source terms stay as the source stated them: exact decimal strings in source
units, one row per (document, code, date, term). Two sources that disagree in a
late decimal place produce two rows; choosing, averaging or reconciling them is
consumer policy. Derived factors (share or price ratios), inferred effective
dates and research admission decisions are never stored here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

from shp_utils import mops_dividend_evidence as mops_list
from shp_utils.corporate_detail_capture import FIELDS as DETAIL_FIELDS
from shp_utils.mops_rights_evidence import project_rights_statement

logger = logging.getLogger(__name__)

IMPORTER = "corporate-action-supplement-import.v1"
PUBLICATION_SCHEMAS = {"annual-source-evidence.v1"}
CANDIDATE_SCHEMAS = {"annual-winner-supplement-candidate.v1"}
SUPPLEMENT_DIRNAME = "corporate_action_supplements"
MAX_MEMBER_BYTES = 32_000_000
MAX_BATCH_BYTES = 256_000_000
TAIPEI = timezone(timedelta(hours=8))

_MISSING = {"", "--", "---", "NA", "N/A"}
_NUMBER = re.compile(r"^(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)(?:\.[0-9]+)?$")
_BATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# Source field name -> (term, unit, literal suffix that must be present).
TWSE_DETAIL_TERMS = {
    "(每股配發現金股利)除息": ("cash_dividend_per_share", "NTD_per_share", "元／股"),
    "A. 按普通股股東持股比例每千股無償配股": ("free_shares_per_1000", "shares_per_1000_held", "股"),
    "B. 員工紅利轉增資": ("employee_bonus_shares", "shares", "股"),
    "C. (有償) 現金增資": ("cash_capital_increase_shares", "shares", "股"),
    "每股認購金額": ("subscription_price", "NTD_per_share", "元／股"),
    "a. 公開承銷": ("public_offering_shares", "shares", "股"),
    "b. 員工認購": ("employee_subscription_shares", "shares", "股"),
    " c. 原股東認購": ("shareholder_subscription_shares", "shares", "股"),
    "按股東持股比例每千股認購": ("subscription_shares_per_1000", "shares_per_1000_held", "股"),
}
TPEX_EXDAILYQ_TERMS = {
    "現金股利": ("cash_dividend_per_share", "NTD_per_share", ""),
    "每仟股無償配股": ("free_shares_per_1000", "shares_per_1000_held", ""),
    "現金增資股數": ("cash_capital_increase_shares", "shares", ""),
    "現金增資認購價": ("subscription_price", "NTD_per_share", ""),
    "公開承銷股數": ("public_offering_shares", "shares", ""),
    "員工認購股數": ("employee_subscription_shares", "shares", ""),
    "原股東認購股數": ("shareholder_subscription_shares", "shares", ""),
    "按持股比例仟股認購": ("subscription_shares_per_1000", "shares_per_1000_held", ""),
}
TPEX_KIND_EVENT_TYPES = {"除權": "EX_RIGHT", "除息": "CASH_DIVIDEND", "除權息": "EX_RIGHT_AND_DIVIDEND"}
# mops-rights-statement.v1 field -> (term, unit). `bonus_per_share` is the
# projector's own exact sum of the two free-share components and is not repeated.
MOPS_FIELDS = {
    "cash_per_share": ("cash_dividend_per_share", "NTD_per_share"),
    "record_date": ("record_date", "iso_date"),
    "announced_payment_date": ("scheduled_cash_payment_date", "iso_date"),
}
MOPS_RIGHTS_FIELDS = {
    "stock_dividend_money_per_share": ("stock_dividend_money_per_share", "NTD_per_share"),
    "earnings_free_per_thousand": ("earnings_free_shares_per_1000", "shares_per_1000_held"),
    "reserves_free_per_thousand": ("reserve_free_shares_per_1000", "shares_per_1000_held"),
    "subscription_price": ("subscription_price", "NTD_per_share"),
    "subscription_per_thousand": ("subscription_shares_per_1000", "shares_per_1000_held"),
    "subscription_class": ("subscription_class", "share_class"),
}
# Curated candidate facts transcribed from documents without a parser
# (exchange notices in HTML or PDF). Attribute -> (term, unit).
CURATED_FACTS = {
    "shares_reduced_per_1000_existing": ("shares_reduced_per_1000", "shares_per_1000_held"),
    "old_par_value": ("old_par_value", "NTD_per_share"),
    "new_par_value": ("new_par_value", "NTD_per_share"),
    "new_shares_per_old_share": ("new_shares_per_old_share", "shares_per_share_held"),
    "new_share_reference_price": ("reference_price_rule", "formula_text"),
    "new_shares_per_1000_old_shares": ("free_shares_per_1000", "shares_per_1000_held"),
    "cash_dividend_per_share": ("cash_dividend_per_share", "NTD_per_share"),
}
# "a -> b" pairs split into before/after terms.
CURATED_PAIRS = {
    "ordinary_shares_before_after": ("ordinary_shares_before", "ordinary_shares_after", "shares"),
    "private_placement_shares_before_after": ("private_placement_shares_before", "private_placement_shares_after", "shares"),
}
# Explicit source facts that are deliberately not corporate-action terms.
CURATED_EXCLUDED = {
    "market_closed": "represented as a trading_interruptions row",
    "precollection_trading_method_start": "trading-method change, not a corporate action",
}
# Date roles that name the event day a curated notice describes, in preference
# order. Study-specific roles (for example a pinned input's date) are not used.
CURATED_DATE_ROLES = ("announced_ex_date", "new_shares_start_trading", "resumption_date", "effective_date")
INTERRUPTION_TYPES = {"trading_suspension": "trading_suspension", "market_wide_closure": "market_closure"}

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS corporate_action_supplement_batches (
        batch_id TEXT PRIMARY KEY,
        publication_schema TEXT NOT NULL,
        publication_sha256 TEXT NOT NULL,
        member_count INTEGER NOT NULL,
        member_bytes INTEGER NOT NULL,
        root_relative TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        importer TEXT NOT NULL,
        imported_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS corporate_action_source_documents (
        batch_id TEXT NOT NULL,
        member_path TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        bytes INTEGER NOT NULL,
        document_kind TEXT NOT NULL,
        publisher TEXT,
        evidence_id TEXT,
        source_url TEXT,
        retrieved_at_utc TEXT,
        PRIMARY KEY (batch_id, member_path)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS corporate_action_terms (
        batch_id TEXT NOT NULL,
        member_path TEXT NOT NULL,
        market TEXT NOT NULL,
        code TEXT NOT NULL,
        event_date TEXT NOT NULL,
        event_date_role TEXT NOT NULL,
        event_type TEXT,
        term TEXT NOT NULL,
        value TEXT,
        unit TEXT NOT NULL,
        value_status TEXT NOT NULL,
        extraction TEXT NOT NULL,
        source_kind TEXT NOT NULL,
        source_announced_raw TEXT,
        raw_literal TEXT,
        PRIMARY KEY (batch_id, member_path, code, event_date, term)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_corporate_action_terms_code_date
    ON corporate_action_terms (code, event_date)
    """,
    """
    CREATE TABLE IF NOT EXISTS trading_interruptions (
        batch_id TEXT NOT NULL,
        market TEXT NOT NULL,
        code TEXT NOT NULL,
        interruption_type TEXT NOT NULL,
        start_date TEXT NOT NULL,
        end_date TEXT,
        resumption_date TEXT,
        extraction TEXT NOT NULL,
        evidence_member_paths TEXT NOT NULL,
        note TEXT,
        PRIMARY KEY (batch_id, market, code, interruption_type, start_date)
    )
    """,
)


class SupplementError(ValueError):
    """The batch cannot be imported without guessing."""


@dataclass
class Batch:
    batch_id: str
    root: Path
    publication: dict[str, Any]
    publication_sha256: str
    members: dict[str, bytes]


@dataclass
class Rows:
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    terms: list[dict[str, Any]] = field(default_factory=list)
    interruptions: list[dict[str, Any]] = field(default_factory=list)


# --------------------------------------------------------------------------- batch


def load_batch(data_dir: Path, batch_id: str) -> Batch:
    """Read every listed member once and verify its size and SHA-256."""
    if not _BATCH_ID.fullmatch(batch_id):
        raise SupplementError(f"invalid batch id: {batch_id!r}")
    root = (Path(data_dir) / SUPPLEMENT_DIRNAME / batch_id).resolve()
    publication_raw = (root / "publication.json").read_bytes()
    publication = _json(publication_raw, "publication.json")
    if publication.get("schema") not in PUBLICATION_SCHEMAS:
        raise SupplementError(f"unsupported publication schema: {publication.get('schema')!r}")
    if publication.get("batch_id") != batch_id:
        raise SupplementError("publication batch_id does not match its directory")
    listed = publication.get("members")
    if not isinstance(listed, dict) or not listed:
        raise SupplementError("publication lists no members")
    members: dict[str, bytes] = {}
    total = 0
    for name, identity in listed.items():
        path = _member_path(root, name)
        size, digest = identity.get("bytes"), identity.get("sha256")
        if not isinstance(size, int) or not 0 <= size <= MAX_MEMBER_BYTES:
            raise SupplementError(f"member size out of bounds: {name}")
        with path.open("rb") as stream:
            body = stream.read(size + 1)
        total += len(body)
        if total > MAX_BATCH_BYTES:
            raise SupplementError("batch exceeds size limit")
        if len(body) != size or hashlib.sha256(body).hexdigest() != digest:
            raise SupplementError(f"member identity mismatch: {name}")
        members[name] = body
    logger.info("verified %d members (%d bytes) of %s", len(members), total, batch_id)
    return Batch(batch_id, root, publication, hashlib.sha256(publication_raw).hexdigest(), members)


def _member_path(root: Path, name: str) -> Path:
    pure = PurePosixPath(name)
    if not name or pure.is_absolute() or ".." in pure.parts or "\\" in name or ":" in name:
        raise SupplementError(f"unsafe member path: {name!r}")
    path = (root / pure).resolve()
    if not path.is_relative_to(root):
        raise SupplementError(f"member escapes batch root: {name!r}")
    return path


# --------------------------------------------------------------------------- extraction


def extract(batch: Batch) -> Rows:
    """Build table rows from the verified member bytes alone."""
    rows = Rows()
    candidate_name = batch.publication.get("candidate")
    candidate = _json(batch.members[candidate_name], candidate_name) if candidate_name else None
    if candidate is not None and candidate.get("schema_version") not in CANDIDATE_SCHEMAS:
        raise SupplementError(f"unsupported candidate schema: {candidate.get('schema_version')!r}")
    sources = _candidate_sources(batch, candidate_name, candidate)
    markets = _code_markets(candidate)

    for name in sorted(batch.members):
        if not name.endswith("manifest.json"):
            continue
        manifest = _json(batch.members[name], name)
        base = name.rsplit("/", 1)[0] if "/" in name else ""
        if manifest.get("schema_version") == "mops-dividend-evidence.v1":
            _mops(batch, base, manifest, sources, markets, rows)
        elif manifest.get("schema_version") == "corporate-detail-capture-v1":
            _twse_detail(batch, base, sources, rows)
        elif "/bulletin/exDailyQ" in str(manifest.get("request_url", "")):
            _tpex_exdailyq(batch, base, manifest, sources, rows)

    if candidate is not None and candidate_name:
        _curated(candidate, candidate_name, sources, rows)
    _check_unique(rows)
    return rows


def _candidate_sources(batch: Batch, candidate_name: str | None, candidate: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Map candidate evidence ids to their member path, bound by byte identity."""
    if candidate is None:
        return {}
    prefix = candidate_name.rsplit("/", 1)[0] + "/" if candidate_name and "/" in candidate_name else ""
    sources: dict[str, dict[str, Any]] = {}
    for source in candidate.get("sources", []):
        identifier = source.get("evidence_id")
        body = source.get("raw_body") or {}
        member = prefix + str(body.get("path", ""))
        if not identifier or identifier in sources:
            raise SupplementError(f"invalid or duplicate evidence id: {identifier!r}")
        raw = batch.members.get(member)
        if raw is None or len(raw) != body.get("bytes") or hashlib.sha256(raw).hexdigest() != body.get("sha256"):
            raise SupplementError(f"candidate source not bound to a verified member: {identifier}")
        sources[identifier] = {**source, "member_path": member}
    return sources


def _document(rows: Rows, batch: Batch, member: str, kind: str, sources: dict[str, dict[str, Any]], **extra: Any) -> None:
    body = batch.members[member]
    by_path = {item["member_path"]: item for item in sources.values()}
    source = by_path.get(member, {})
    rows.documents[member] = {
        "member_path": member,
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
        "document_kind": kind,
        "publisher": extra.get("publisher") or source.get("publisher"),
        "evidence_id": source.get("evidence_id"),
        "source_url": extra.get("source_url") or source.get("source_url"),
        "retrieved_at_utc": extra.get("retrieved_at_utc") or source.get("retrieved_at_utc"),
    }


def _mops(batch: Batch, base: str, manifest: dict[str, Any], sources: dict[str, dict[str, Any]], markets: dict[str, str], rows: Rows) -> None:
    company = manifest.get("company_id")
    requests = manifest.get("requests") or []
    if not any(r.get("outcome") == "validated_detail" for r in requests):
        return
    if company not in markets:
        # MOPS serves both markets and its detail does not say which one lists the issuer.
        raise SupplementError(f"market of MOPS issuer is not established by a candidate event: {base}")
    lists = [r for r in requests if r.get("outcome") == "validated_list"]
    if len(lists) != 1:
        raise SupplementError(f"MOPS capture needs exactly one validated list: {base}")
    list_packet = _json(batch.members[f"{base}/{lists[0]['raw_file']}"], base)
    announcements = mops_list._announcements(list_packet, company)
    for request in requests:
        if request.get("outcome") != "validated_detail":
            continue
        member = f"{base}/{request['raw_file']}"
        body = batch.members[member]
        matches = [a for a in announcements if a["detail_parameters"] == request.get("request_parameters")]
        if len(matches) != 1:
            raise SupplementError(f"MOPS detail does not bind to one list row: {member}")
        record = project_rights_statement(body, company_id=company, announcement=matches[0], expected_sha256=hashlib.sha256(body).hexdigest())
        fields, rights = record["fields"], record["rights_fields"]
        ex_date = fields["stated_ex_date"]
        if ex_date["status"] != "stated":
            logger.warning("MOPS detail %s states no ex-date; its terms are not linkable and are skipped", member)
            continue
        _document(
            rows,
            batch,
            member,
            "mops_t108sb19_detail",
            sources,
            publisher="MOPS",
            source_url=request.get("final_url"),
            retrieved_at_utc=request.get("finished_at_utc"),
        )
        common = {
            "member_path": member,
            "market": markets[company],
            "code": company,
            "event_date": ex_date["value"],
            "event_date_role": "stated_ex_date",
            "event_type": None,
            "extraction": "parsed",
            "source_kind": "mops_t108sb19_detail",
            "source_announced_raw": record.get("source_displayed_announcement_date_raw"),
        }
        for name, (term, unit) in MOPS_FIELDS.items():
            rows.terms.append(_term(common, term, unit, fields[name]))
        for name, (term, unit) in MOPS_RIGHTS_FIELDS.items():
            rows.terms.append(_term(common, term, unit, rights[name]))


def _term(common: dict[str, Any], term: str, unit: str, projected: dict[str, Any]) -> dict[str, Any]:
    raw = projected.get("raw")
    return {
        **common,
        "term": term,
        "value": projected["value"] if projected["status"] == "stated" else None,
        "unit": unit,
        "value_status": projected["status"],
        "raw_literal": json.dumps(raw, ensure_ascii=False) if isinstance(raw, list) else raw,
    }


def _code_markets(candidate: dict[str, Any] | None) -> dict[str, str]:
    markets: dict[str, str] = {}
    for event in (candidate or {}).get("events", []):
        code, market = event.get("code"), event.get("market")
        if code and markets.setdefault(code, market) != market:
            raise SupplementError(f"candidate events disagree on the market of {code}")
    return markets


def _twse_detail(batch: Batch, base: str, sources: dict[str, dict[str, Any]], rows: Rows) -> None:
    requests = _jsonl(batch.members[f"{base}/requests.jsonl"], base)
    events = _jsonl(batch.members[f"{base}/events.jsonl"], base)
    if len(requests) != len(events):
        raise SupplementError(f"TWSE detail receipts are misaligned: {base}")
    for request, event in zip(requests, events, strict=True):
        if request.get("source") != "twt49u" or request.get("outcome") != "captured":
            raise SupplementError(f"unsupported TWSE detail receipt: {base}")
        member = f"{base}/{request['raw_file']}"
        body = batch.members[member]
        if hashlib.sha256(body).hexdigest() != request.get("response_sha256") or event.get("response_sha256") != request.get("response_sha256"):
            raise SupplementError(f"TWSE detail body is not the receipted response: {member}")
        packet = _json(body, member)
        fields, data = packet.get("fields"), packet.get("data")
        if packet.get("stat") != "ok" or tuple(fields or ()) != DETAIL_FIELDS["twt49u"] or not isinstance(data, list) or len(data) != 1:
            raise SupplementError(f"unexpected TWT49U detail shape: {member}")
        literals: dict[str, Any] = dict(zip(DETAIL_FIELDS["twt49u"], data[0], strict=True))
        code = str(literals["股票代號"]).strip()
        if code != request.get("code") or code != event.get("code"):
            raise SupplementError(f"TWT49U detail code mismatch: {member}")
        # The detail does not echo its date; it is bound by the annual row the capture requested.
        _iso(event.get("effective_date_from_annual_row"), member)
        _document(
            rows,
            batch,
            member,
            "twse_twt49u_detail",
            sources,
            publisher="TWSE",
            source_url=request.get("request_url"),
            retrieved_at_utc=request.get("retrieved_at_utc"),
        )
        common = {
            "member_path": member,
            "market": "TWSE",
            "code": code,
            "event_date": event["effective_date_from_annual_row"],
            "event_date_role": "annual_row_effective_date",
            "event_type": None,
            "extraction": "parsed",
            "source_kind": "twse_twt49u_detail",
            "source_announced_raw": None,
        }
        for name, (term, unit, suffix) in TWSE_DETAIL_TERMS.items():
            rows.terms.append(_literal_term(common, term, unit, literals[name], suffix))


def _tpex_exdailyq(batch: Batch, base: str, manifest: dict[str, Any], sources: dict[str, dict[str, Any]], rows: Rows) -> None:
    member = f"{base}/{manifest['raw_file']}" if base else manifest["raw_file"]
    body = batch.members[member]
    if hashlib.sha256(body).hexdigest() != manifest.get("sha256"):
        raise SupplementError(f"exDailyQ body is not the receipted response: {member}")
    tables = _json(body, member).get("tables")
    if not isinstance(tables, list) or len(tables) != 1:
        raise SupplementError(f"exDailyQ needs one table: {member}")
    fields, data = tables[0].get("fields"), tables[0].get("data")
    required = {"除權息日期", "代號", "權/息", *TPEX_EXDAILYQ_TERMS}
    if not isinstance(fields, list) or not required.issubset(fields) or not isinstance(data, list):
        raise SupplementError(f"exDailyQ fields are not the observed signature: {member}")
    if not data:
        # An empty response is not evidence about the schema or about events.
        logger.info("exDailyQ %s is empty; nothing to import", member)
        return
    _document(
        rows,
        batch,
        member,
        "tpex_exdailyq",
        sources,
        publisher="TPEx",
        source_url=manifest.get("request_url"),
        retrieved_at_utc=manifest.get("finished_at_utc"),
    )
    for row in data:
        literals = dict(zip(fields, row, strict=True))
        kind = str(literals["權/息"]).strip()
        if kind not in TPEX_KIND_EVENT_TYPES:
            raise SupplementError(f"unrecognized exDailyQ kind {kind!r}: {member}")
        common = {
            "member_path": member,
            "market": "TPEx",
            "code": str(literals["代號"]).strip(),
            "event_date": _roc_slash(literals["除權息日期"], member),
            "event_date_role": "exchange_ex_date",
            "event_type": TPEX_KIND_EVENT_TYPES[kind],
            "extraction": "parsed",
            "source_kind": "tpex_exdailyq",
            "source_announced_raw": None,
        }
        for name, (term, unit, suffix) in TPEX_EXDAILYQ_TERMS.items():
            rows.terms.append(_literal_term(common, term, unit, literals[name], suffix))


def _literal_term(common: dict[str, Any], term: str, unit: str, literal: Any, suffix: str) -> dict[str, Any]:
    raw = literal if isinstance(literal, str) else None
    text = (raw or "").strip()
    status, value = "invalid", None
    if raw is None:
        status = "invalid"
    elif text in _MISSING:
        status = "missing"
    elif not suffix or text.endswith(suffix):
        number = text.removesuffix(suffix).strip() if suffix else text
        if _NUMBER.fullmatch(number):
            status, value = "stated", number.replace(",", "")
    return {**common, "term": term, "value": value, "unit": unit, "value_status": status, "raw_literal": raw}


def _curated(candidate: dict[str, Any], candidate_name: str, sources: dict[str, dict[str, Any]], rows: Rows) -> None:
    """Transcribed notice facts and interruption intervals; parsed documents are cross-checked."""
    parsed_paths = {doc["member_path"] for doc in rows.documents.values()}
    parsed = {(t["member_path"], t["code"], t["term"]): t for t in rows.terms if t["value_status"] == "stated"}
    for event in candidate.get("events", []):
        code = event.get("code") or ""
        market = event.get("market")
        if market not in {"TWSE", "TPEx"}:
            raise SupplementError(f"candidate event market unknown: {event.get('event_id')}")
        for fact in event.get("source_facts", []):
            attribute = fact.get("attribute")
            if not str(fact.get("fact_status", "")).startswith("source_explicit"):
                continue
            if attribute in CURATED_EXCLUDED:
                continue
            documents = [_source(sources, identifier)["member_path"] for identifier in fact.get("evidence_ids", [])]
            if not documents:
                raise SupplementError(f"curated fact cites no evidence: {fact.get('fact_id')}")
            mapped = CURATED_FACTS.get(attribute)
            pair = CURATED_PAIRS.get(attribute)
            if mapped is None and pair is None:
                raise SupplementError(f"unmapped explicit source fact: {attribute}")
            values = _curated_values(fact, mapped, pair)
            if all(path in parsed_paths for path in documents):
                # Machine-readable sources are imported by their parser; the transcription must agree.
                for term, _, value in values:
                    for path in documents:
                        if _parsed_decimal(parsed, path, code, term) != Decimal(value):
                            raise SupplementError(f"transcription disagrees with parsed source: {fact.get('fact_id')} {path}")
                continue
            if any(path in parsed_paths for path in documents):
                raise SupplementError(f"curated fact mixes parsed and unparsed evidence: {fact.get('fact_id')}")
            role, day = _curated_date(event)
            for path in documents:
                rows.documents.setdefault(path, _curated_document(path, sources))
                for term, unit, value in values:
                    rows.terms.append({
                        "member_path": path,
                        "market": market,
                        "code": code,
                        "event_date": day,
                        "event_date_role": role,
                        "event_type": event.get("event_type"),
                        "term": term,
                        "value": value,
                        "unit": unit,
                        "value_status": "stated",
                        "extraction": "curated_transcription",
                        "source_kind": "official_notice",
                        "source_announced_raw": None,
                        "raw_literal": fact.get("raw_value"),
                    })
        for interval in event.get("market_intervals", []):
            kind = INTERRUPTION_TYPES.get(interval.get("type"))
            if kind is None:
                raise SupplementError(f"unsupported interval type: {interval.get('type')}")
            if interval.get("start_inclusive") is not True or (interval.get("end_date") is not None and interval.get("end_inclusive") is not True):
                raise SupplementError(f"interval endpoints must be inclusive: {event.get('event_id')}")
            documents = [_source(sources, identifier)["member_path"] for identifier in interval.get("evidence_ids", [])]
            if not documents:
                raise SupplementError(f"interval cites no evidence: {event.get('event_id')}")
            for path in documents:
                rows.documents.setdefault(path, _curated_document(path, sources))
            start = _iso(interval.get("start_date"), event["event_id"])
            end = None if interval.get("end_date") is None else _iso(interval["end_date"], event["event_id"])
            resumption = None if interval.get("resumption_date") is None else _iso(interval["resumption_date"], event["event_id"])
            if end is not None and (end < start or (resumption is not None and resumption <= end)):
                raise SupplementError(f"interval dates out of order: {event.get('event_id')}")
            rows.interruptions.append({
                "market": market,
                "code": code,
                "interruption_type": kind,
                "start_date": start,
                "end_date": end,
                "resumption_date": resumption,
                "extraction": "curated_transcription",
                "evidence_member_paths": json.dumps(sorted(documents)),
                "note": interval.get("endpoint_semantics"),
            })
    logger.info("curated rows read from %s", candidate_name)


def _parsed_decimal(parsed: dict[tuple[str, str, str], dict[str, Any]], path: str, code: str, term: str) -> Decimal | None:
    match = parsed.get((path, code, term))
    if match is not None:
        return Decimal(match["value"])
    if term == "free_shares_per_1000":
        # MOPS states earnings and reserve grants separately; exchanges state their sum.
        parts = [parsed.get((path, code, name)) for name in ("earnings_free_shares_per_1000", "reserve_free_shares_per_1000")]
        if all(part is not None for part in parts):
            return sum((Decimal(part["value"]) for part in parts if part is not None), Decimal(0))
    return None


def _curated_values(fact: dict[str, Any], mapped: tuple[str, str] | None, pair: tuple[str, str, str] | None) -> list[tuple[str, str, str]]:
    raw = str(fact.get("raw_value", "")).strip()
    if pair is not None:
        parts = [part.strip() for part in raw.split("->")]
        if len(parts) != 2 or not all(_NUMBER.fullmatch(part) for part in parts):
            raise SupplementError(f"before/after pair is not two numbers: {fact.get('fact_id')}")
        return [(pair[0], pair[2], parts[0].replace(",", "")), (pair[1], pair[2], parts[1].replace(",", ""))]
    assert mapped is not None
    term, unit = mapped
    if unit != "formula_text" and not _NUMBER.fullmatch(raw):
        raise SupplementError(f"curated numeric fact is not a decimal: {fact.get('fact_id')}")
    return [(term, unit, raw.replace(",", "") if unit != "formula_text" else raw)]


def _curated_date(event: dict[str, Any]) -> tuple[str, str]:
    roles = {item.get("role"): item.get("date") for item in event.get("date_roles", [])}
    for role in CURATED_DATE_ROLES:
        if roles.get(role):
            return role, _iso(roles[role], event.get("event_id"))
    raise SupplementError(f"curated event has no notice date role: {event.get('event_id')}")


def _curated_document(path: str, sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    source = next(item for item in sources.values() if item["member_path"] == path)
    return {
        "member_path": path,
        "sha256": source["raw_body"]["sha256"],
        "bytes": source["raw_body"]["bytes"],
        "document_kind": source.get("evidence_kind") or "official_document",
        "publisher": source.get("publisher"),
        "evidence_id": source.get("evidence_id"),
        "source_url": source.get("source_url"),
        "retrieved_at_utc": source.get("retrieved_at_utc"),
    }


def _source(sources: dict[str, dict[str, Any]], identifier: str) -> dict[str, Any]:
    if identifier not in sources:
        raise SupplementError(f"unregistered evidence id: {identifier}")
    return sources[identifier]


def _check_unique(rows: Rows) -> None:
    keys = [(t["member_path"], t["code"], t["event_date"], t["term"]) for t in rows.terms]
    if len(keys) != len(set(keys)):
        raise SupplementError("duplicate term rows")
    spans = [(i["market"], i["code"], i["interruption_type"], i["start_date"]) for i in rows.interruptions]
    if len(spans) != len(set(spans)):
        raise SupplementError("duplicate interruption rows")


# --------------------------------------------------------------------------- write


def init_tables(conn: sqlite3.Connection) -> None:
    for statement in SCHEMA:
        conn.execute(statement)


def content_digest(batch: Batch, rows: Rows) -> str:
    payload = {
        "batch_id": batch.batch_id,
        "publication_sha256": batch.publication_sha256,
        "documents": sorted(rows.documents.values(), key=lambda d: d["member_path"]),
        "terms": sorted(rows.terms, key=lambda t: (t["member_path"], t["code"], t["event_date"], t["term"])),
        "interruptions": sorted(rows.interruptions, key=lambda i: (i["market"], i["code"], i["interruption_type"], i["start_date"])),
        "importer": IMPORTER,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def write(conn: sqlite3.Connection, batch: Batch, rows: Rows, *, imported_at: str) -> str:
    """Insert one batch atomically. Re-importing identical content is a no-op; changed content is refused."""
    digest = content_digest(batch, rows)
    conn.execute("BEGIN IMMEDIATE")
    try:
        init_tables(conn)
        existing = conn.execute("SELECT content_sha256 FROM corporate_action_supplement_batches WHERE batch_id = ?", (batch.batch_id,)).fetchone()
        if existing is not None:
            conn.execute("ROLLBACK")
            if existing[0] != digest:
                raise SupplementError(f"batch {batch.batch_id} is already imported with different content; publish a new batch id")
            return "unchanged"
        conn.execute(
            "INSERT INTO corporate_action_supplement_batches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                batch.batch_id,
                batch.publication["schema"],
                batch.publication_sha256,
                len(batch.members),
                sum(len(body) for body in batch.members.values()),
                f"{SUPPLEMENT_DIRNAME}/{batch.batch_id}",
                digest,
                IMPORTER,
                imported_at,
            ),
        )
        conn.executemany(
            "INSERT INTO corporate_action_source_documents VALUES "
            "(:batch_id, :member_path, :sha256, :bytes, :document_kind, :publisher, :evidence_id, :source_url, :retrieved_at_utc)",
            [{"batch_id": batch.batch_id, **doc} for doc in rows.documents.values()],
        )
        conn.executemany(
            "INSERT INTO corporate_action_terms VALUES (:batch_id, :member_path, :market, :code, :event_date, :event_date_role, "
            ":event_type, :term, :value, :unit, :value_status, :extraction, :source_kind, :source_announced_raw, :raw_literal)",
            [{"batch_id": batch.batch_id, **term} for term in rows.terms],
        )
        conn.executemany(
            "INSERT INTO trading_interruptions VALUES (:batch_id, :market, :code, :interruption_type, :start_date, :end_date, "
            ":resumption_date, :extraction, :evidence_member_paths, :note)",
            [{"batch_id": batch.batch_id, **span} for span in rows.interruptions],
        )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return "inserted"


def in_producer_write_window(now: datetime) -> bool:
    """Weekday 18:00-18:45 Asia/Taipei, when the scheduled daily update writes."""
    local = now.astimezone(TAIPEI)
    return local.weekday() < 5 and (18, 0) <= (local.hour, local.minute) <= (18, 45)


def summarize(rows: Rows) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for term in rows.terms:
        key = f"{term['extraction']}:{term['value_status']}"
        by_status[key] = by_status.get(key, 0) + 1
    return {
        "documents": len(rows.documents),
        "terms": len(rows.terms),
        "terms_by_extraction_status": dict(sorted(by_status.items())),
        "codes": sorted({t["code"] for t in rows.terms}),
        "interruptions": len(rows.interruptions),
    }


# --------------------------------------------------------------------------- helpers


def _json(body: bytes, name: str) -> dict[str, Any]:
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SupplementError(f"invalid JSON: {name}") from error
    if not isinstance(value, dict):
        raise SupplementError(f"JSON object required: {name}")
    return value


def _jsonl(body: bytes, name: str) -> list[dict[str, Any]]:
    return [_json(line, name) for line in body.splitlines() if line.strip()]


def _iso(value: Any, context: Any) -> str:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise SupplementError(f"invalid ISO date in {context}: {value!r}") from error
    if parsed.isoformat() != value:
        raise SupplementError(f"invalid ISO date in {context}: {value!r}")
    return value


def _roc_slash(value: Any, context: str) -> str:
    match = re.fullmatch(r"\s*([0-9]{2,3})/([0-9]{2})/([0-9]{2})\s*", str(value))
    if match is None:
        raise SupplementError(f"invalid ROC date in {context}: {value!r}")
    try:
        return date(int(match[1]) + 1911, int(match[2]), int(match[3])).isoformat()
    except ValueError as error:
        raise SupplementError(f"invalid ROC date in {context}: {value!r}") from error


__all__ = [
    "Batch",
    "IMPORTER",
    "Rows",
    "SupplementError",
    "extract",
    "in_producer_write_window",
    "init_tables",
    "load_batch",
    "summarize",
    "write",
]
