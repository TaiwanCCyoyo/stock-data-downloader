"""Fetch and snapshot the official TPEx industry value-chain taxonomy.

The source publishes current-state HTML rather than historical data. Every
successful observation is therefore kept under an immutable timestamped path;
the latest file is only a convenience pointer to the newest stored evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from config import config
from shp_utils.logging_utils import setup_logging
from shp_utils.official_api import DEFAULT_USER_AGENT

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
SOURCE_NAME = "Taipei Exchange Industry Value Chain Information Platform"
BASE_URL = "https://ic.tpex.org.tw"
CHAIN_URL = f"{BASE_URL}/introduce.php"
SEED_CHAIN_CODE = "D000"
DEFAULT_DATA_DIR = config.DATA_DIR
DEFAULT_LATEST_PATH = DEFAULT_DATA_DIR / "value_chain_classification.json"
DEFAULT_HISTORY_DIR = DEFAULT_DATA_DIR / "value_chain_classification_history"
DEFAULT_MAX_AGE_DAYS = 7
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
CHAIN_CODE_PATTERN = re.compile(r"^[A-Z0-9]{4}$")
SECURITY_CODE_PATTERN = re.compile(r"^[0-9]{4,6}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
LISTING_COUNT_SUFFIX = re.compile(r"\s*[（(]\d+家[)）]\s*$")


@dataclass(frozen=True)
class RefreshResult:
    refreshed: bool
    latest_path: Path
    history_path: Path | None
    snapshot_id: str | None


def _attrs_dict(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    return {key: value or "" for key, value in attrs}


def _clean_text(parts: list[str]) -> str:
    return " ".join("".join(parts).split())


class ValueChainPageParser(HTMLParser):
    """Extract the selector, staged nodes, and Taiwan security links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chain_options: list[tuple[str, str]] = []
        self.chain_title = ""
        self.nodes: list[dict[str, Any]] = []
        self.members_by_node: dict[str, list[dict[str, str]]] = {}

        self._div_depth = 0
        self._capture_kind: str | None = None
        self._capture_depth: int | None = None
        self._capture_code: str | None = None
        self._capture_parent_code: str | None = None
        self._capture_parts: list[str] = []
        self._current_stage: str | None = None

        self._in_h3 = False
        self._h3_parts: list[str] = []
        self._option_value: str | None = None
        self._option_parts: list[str] = []

        self._company_node: str | None = None
        self._company_depth: int | None = None
        self._table_depth = 0
        self._subcompany_node: str | None = None
        self._subcompany_depth: int | None = None
        self._in_listing_label = False
        self._listing_parts: list[str] = []
        self._current_listing_segment = ""
        self._pending_member: tuple[str, str] | None = None
        self._member_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = _attrs_dict(attrs)
        if tag == "div":
            self._div_depth += 1
            classes = set(attributes.get("class", "").split())
            element_id = attributes.get("id", "")
            if "chain-title-panel" in classes:
                self._start_capture("stage")
            elif element_id.startswith("ic_link_"):
                self._start_capture("node", element_id.removeprefix("ic_link_"))
            elif element_id.startswith("sc_link_"):
                self._start_capture(
                    "subnode",
                    element_id.removeprefix("sc_link_"),
                    parent_code=self._company_node,
                )
            elif element_id.startswith("companyList_"):
                self._company_node = element_id.removeprefix("companyList_")
                self._company_depth = self._div_depth
                self.members_by_node.setdefault(self._company_node, [])
        elif tag == "table":
            self._table_depth += 1
            element_id = attributes.get("id", "")
            if element_id.startswith("sc_company_"):
                self._subcompany_node = element_id.removeprefix("sc_company_")
                self._subcompany_depth = self._table_depth
                self.members_by_node.setdefault(self._subcompany_node, [])
        elif tag == "h3":
            self._in_h3 = True
            self._h3_parts = []
        elif tag == "option":
            self._option_value = attributes.get("value", "").strip()
            self._option_parts = []
        elif tag == "b" and self._company_node:
            self._in_listing_label = True
            self._listing_parts = []
        elif tag == "a" and self._company_node:
            code = _security_code_from_href(attributes.get("href", ""))
            if code and self._current_listing_segment.startswith("本國"):
                self._pending_member = (code, attributes.get("title", "").strip())
                self._member_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "div":
            if self._capture_depth == self._div_depth:
                self._finish_capture()
            if self._company_depth == self._div_depth:
                self._company_node = None
                self._company_depth = None
                self._subcompany_node = None
                self._subcompany_depth = None
                self._current_listing_segment = ""
            self._div_depth = max(0, self._div_depth - 1)
        elif tag == "table":
            if self._subcompany_depth == self._table_depth:
                self._subcompany_node = None
                self._subcompany_depth = None
                self._current_listing_segment = ""
            self._table_depth = max(0, self._table_depth - 1)
        elif tag == "h3" and self._in_h3:
            title = _clean_text(self._h3_parts)
            self.chain_title = title.removesuffix("產業鏈簡介").strip()
            self._in_h3 = False
        elif tag == "option" and self._option_value is not None:
            label = _clean_text(self._option_parts)
            self.chain_options.append((self._option_value, label))
            self._option_value = None
        elif tag == "b" and self._in_listing_label:
            label = _clean_text(self._listing_parts)
            self._current_listing_segment = LISTING_COUNT_SUFFIX.sub("", label)
            self._in_listing_label = False
        elif tag == "a" and self._pending_member is not None:
            code, title = self._pending_member
            name = title or _clean_text(self._member_text)
            target_node = self._subcompany_node or self._company_node
            if name and target_node:
                self.members_by_node.setdefault(target_node, []).append({
                    "code": code,
                    "name": name,
                    "listing_segment": self._current_listing_segment,
                })
            self._pending_member = None
            self._member_text = []

    def handle_data(self, data: str) -> None:
        if self._capture_kind:
            self._capture_parts.append(data)
        if self._in_h3:
            self._h3_parts.append(data)
        if self._option_value is not None:
            self._option_parts.append(data)
        if self._in_listing_label:
            self._listing_parts.append(data)
        if self._pending_member is not None:
            self._member_text.append(data)

    def _start_capture(self, kind: str, code: str | None = None, *, parent_code: str | None = None) -> None:
        self._capture_kind = kind
        self._capture_depth = self._div_depth
        self._capture_code = code
        self._capture_parent_code = parent_code
        self._capture_parts = []

    def _finish_capture(self) -> None:
        text = _clean_text(self._capture_parts)
        if self._capture_kind == "stage":
            self._current_stage = text
        elif self._capture_kind in {"node", "subnode"} and self._capture_code:
            if any(node["code"] == self._capture_code for node in self.nodes):
                raise ValueError(f"duplicate node code in source page: {self._capture_code}")
            parent = next(
                (node for node in self.nodes if node["code"] == self._capture_parent_code),
                None,
            )
            if self._capture_kind == "subnode":
                text = LISTING_COUNT_SUFFIX.sub("", text).lstrip("►").strip()
            self.nodes.append({
                "code": self._capture_code,
                "name": text,
                "stage": parent["stage"] if parent else self._current_stage,
                "parent_code": self._capture_parent_code,
                "members": [],
            })
        self._capture_kind = None
        self._capture_depth = None
        self._capture_code = None
        self._capture_parent_code = None
        self._capture_parts = []


def _security_code_from_href(href: str) -> str | None:
    parsed = urlparse(href)
    if parsed.scheme or parsed.netloc:
        if not _is_official_tpex_url(href):
            return None
    if not parsed.path.endswith("company_basic.php"):
        return None
    values = parse_qs(parsed.query, keep_blank_values=True).get("stk_code", [])
    if len(values) != 1:
        return None
    code = values[0].strip()
    return code if SECURITY_CODE_PATTERN.fullmatch(code) else None


def _is_official_tpex_url(url: str) -> bool:
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        return False
    return parsed.scheme == "https" and parsed.hostname == "ic.tpex.org.tw" and port in {None, 443}


def _validate_official_tpex_url(url: str) -> None:
    if not _is_official_tpex_url(url):
        raise ValueError(f"unexpected TPEx value-chain URL: {url!r}")


def discover_chain_codes(html: str) -> list[tuple[str, str]]:
    parser = ValueChainPageParser()
    parser.feed(html)
    discovered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for code, name in parser.chain_options:
        if CHAIN_CODE_PATTERN.fullmatch(code) and code not in seen:
            discovered.append((code, name))
            seen.add(code)
    if not discovered:
        raise ValueError("TPEx value-chain selector contained no chain codes")
    return discovered


def parse_chain_page(html: str, *, chain_code: str, source_url: str, fallback_name: str = "") -> dict[str, Any]:
    parser = ValueChainPageParser()
    parser.feed(html)

    nodes_by_code = {node["code"]: node for node in parser.nodes}
    unknown_lists = sorted(set(parser.members_by_node) - set(nodes_by_code))
    if unknown_lists:
        raise ValueError(f"company lists do not match displayed nodes for {chain_code}: {unknown_lists}")
    for node in parser.nodes:
        node["members"] = _deduplicate_members(parser.members_by_node.get(node["code"], []), node["code"])
        node["member_count"] = len(node["members"])

    chain_name = parser.chain_title or fallback_name.strip()
    if not chain_name:
        raise ValueError(f"missing chain name for {chain_code}")
    if not parser.nodes:
        raise ValueError(f"value chain {chain_code} contained no classification nodes")
    if any(not node["name"] for node in parser.nodes):
        raise ValueError(f"value chain {chain_code} contained an unnamed node")

    return {
        "code": chain_code,
        "name": chain_name,
        "source_url": source_url,
        "source_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
        "nodes": parser.nodes,
        "member_count": sum(node["member_count"] for node in parser.nodes),
    }


def _deduplicate_members(members: list[dict[str, str]], node_code: str) -> list[dict[str, str]]:
    unique: list[dict[str, str]] = []
    seen: dict[str, dict[str, str]] = {}
    for member in members:
        previous = seen.get(member["code"])
        if previous is not None:
            if previous != member:
                raise ValueError(f"conflicting duplicate member {member['code']} in node {node_code}")
            continue
        seen[member["code"]] = member
        unique.append(member)
    return unique


def fetch_html_with_retry(
    url: str,
    *,
    params: dict[str, str],
    retries: int = 3,
    retry_sleep: float = 5.0,
    timeout: float = 30.0,
) -> tuple[str, str]:
    _validate_official_tpex_url(url)
    attempts = max(retries, 0) + 1
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        response: requests.Response | None = None
        try:
            response = requests.get(
                url,
                params=params,
                timeout=timeout,
                headers={"User-Agent": DEFAULT_USER_AGENT},
                allow_redirects=False,
                stream=True,
            )
            if 300 <= response.status_code < 400:
                raise RuntimeError(f"TPEx value-chain endpoint redirected with status {response.status_code}")
            response.raise_for_status()
            _validate_official_tpex_url(response.url)
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) > MAX_RESPONSE_BYTES:
                raise ValueError(f"TPEx value-chain response exceeded {MAX_RESPONSE_BYTES} bytes")
            payload = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if len(payload) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise ValueError(f"TPEx value-chain response exceeded {MAX_RESPONSE_BYTES} bytes")
                payload.extend(chunk)
            return payload.decode("utf-8"), response.url
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= attempts:
                status = response.status_code if response is not None else "no response"
                raise RuntimeError(f"Failed to fetch TPEx value-chain page {params}; status={status}") from exc
            logger.warning("value-chain fetch attempt %d/%d failed for %s: %s", attempt, attempts, params, exc)
            time.sleep(retry_sleep * attempt)
        finally:
            if response is not None:
                response.close()
    raise RuntimeError(f"Failed to fetch TPEx value-chain page {params}") from last_error


def fetch_current_classification(
    *,
    fetched_at: datetime | None = None,
    retries: int = 3,
    retry_sleep: float = 5.0,
    timeout: float = 30.0,
    request_sleep: float = 0.25,
) -> dict[str, Any]:
    fetched_at = _as_utc(fetched_at or datetime.now(UTC))
    seed_html, seed_url = fetch_html_with_retry(
        CHAIN_URL,
        params={"ic": SEED_CHAIN_CODE},
        retries=retries,
        retry_sleep=retry_sleep,
        timeout=timeout,
    )
    chain_options = discover_chain_codes(seed_html)

    chains: list[dict[str, Any]] = []
    for chain_code, fallback_name in chain_options:
        if chain_code == SEED_CHAIN_CODE:
            html, source_url = seed_html, seed_url
        else:
            if request_sleep > 0:
                time.sleep(request_sleep)
            html, source_url = fetch_html_with_retry(
                CHAIN_URL,
                params={"ic": chain_code},
                retries=retries,
                retry_sleep=retry_sleep,
                timeout=timeout,
            )
        chains.append(
            parse_chain_page(
                html,
                chain_code=chain_code,
                source_url=source_url,
                fallback_name=fallback_name,
            )
        )

    return build_snapshot(chains, fetched_at=fetched_at, index_url=seed_url)


def build_snapshot(chains: list[dict[str, Any]], *, fetched_at: datetime, index_url: str) -> dict[str, Any]:
    _validate_chains(chains)
    _validate_official_tpex_url(index_url)
    stable_symbols = _build_symbol_index(chains)
    snapshot_id = _taxonomy_digest(chains)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "name": SOURCE_NAME,
            "index_url": index_url,
            "chain_count": len(chains),
        },
        "fetched_at": _format_utc(fetched_at),
        "snapshot_id": snapshot_id,
        "chains": chains,
        "symbols": stable_symbols,
    }


def _build_symbol_index(chains: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    symbols: dict[str, dict[str, Any]] = {}
    for chain in chains:
        for node in chain["nodes"]:
            for member in node["members"]:
                symbol = symbols.setdefault(
                    member["code"],
                    {"name": member["name"], "listing_segments": set(), "memberships": []},
                )
                if symbol["name"] != member["name"]:
                    raise ValueError(f"conflicting names for security {member['code']}: {symbol['name']!r} vs {member['name']!r}")
                if member["listing_segment"]:
                    symbol["listing_segments"].add(member["listing_segment"])
                symbol["memberships"].append({
                    "chain_code": chain["code"],
                    "chain_name": chain["name"],
                    "node_code": node["code"],
                    "node_name": node["name"],
                    "stage": node["stage"],
                })

    stable_symbols: dict[str, dict[str, Any]] = {}
    for code in sorted(symbols):
        symbol = symbols[code]
        stable_symbols[code] = {
            "name": symbol["name"],
            "listing_segments": sorted(symbol["listing_segments"]),
            "memberships": sorted(
                symbol["memberships"],
                key=lambda item: (item["chain_code"], item["node_code"]),
            ),
        }

    return stable_symbols


def _taxonomy_digest(chains: list[dict[str, Any]]) -> str:
    taxonomy_bytes = json.dumps(chains, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(taxonomy_bytes).hexdigest()


def _validate_chains(chains: object) -> None:
    if not isinstance(chains, list) or not chains:
        raise ValueError("classification snapshot contained no value chains")
    chain_codes: set[str] = set()
    node_count = 0
    member_count = 0
    for chain in chains:
        if not isinstance(chain, dict):
            raise ValueError("classification snapshot contained a non-object value chain")
        code = chain.get("code")
        if not isinstance(code, str) or not CHAIN_CODE_PATTERN.fullmatch(code) or code in chain_codes:
            raise ValueError(f"missing or duplicate chain code: {code!r}")
        chain_codes.add(code)
        if not isinstance(chain.get("name"), str) or not chain["name"]:
            raise ValueError(f"incomplete value-chain metadata for {code}")
        source_url = chain.get("source_url")
        if not isinstance(source_url, str):
            raise ValueError(f"incomplete value-chain metadata for {code}")
        _validate_official_tpex_url(source_url)
        if not isinstance(chain.get("source_sha256"), str) or not SHA256_PATTERN.fullmatch(chain["source_sha256"]):
            raise ValueError(f"incomplete value-chain metadata for {code}")
        nodes = chain.get("nodes")
        if not isinstance(nodes, list):
            raise ValueError(f"invalid nodes container for {code}")
        node_codes: set[str] = set()
        chain_member_count = 0
        for node in nodes:
            if not isinstance(node, dict):
                raise ValueError(f"value chain {code} contained a non-object node")
            node_code = node.get("code")
            if not isinstance(node_code, str) or not CHAIN_CODE_PATTERN.fullmatch(node_code) or node_code in node_codes:
                raise ValueError(f"missing or duplicate node code in {code}: {node_code!r}")
            node_codes.add(node_code)
            if not isinstance(node.get("name"), str) or not node["name"] or "stage" not in node:
                raise ValueError(f"incomplete node metadata for {node_code}")
            if node["stage"] is not None and not isinstance(node["stage"], str):
                raise ValueError(f"invalid stage for {node_code}")
            parent_code = node.get("parent_code")
            if parent_code is not None and not isinstance(parent_code, str):
                raise ValueError(f"invalid parent code for {node_code}")
            members = node.get("members")
            if not isinstance(members, list):
                raise ValueError(f"invalid members container for {node_code}")
            member_codes: set[str] = set()
            for member in members:
                if not isinstance(member, dict):
                    raise ValueError(f"node {node_code} contained a non-object member")
                member_code = member.get("code")
                if not isinstance(member_code, str) or not SECURITY_CODE_PATTERN.fullmatch(member_code) or member_code in member_codes:
                    raise ValueError(f"missing, invalid, or duplicate member code in {node_code}: {member_code!r}")
                member_codes.add(member_code)
                if not isinstance(member.get("name"), str) or not member["name"]:
                    raise ValueError(f"incomplete member metadata for {member_code}")
                if not isinstance(member.get("listing_segment"), str):
                    raise ValueError(f"invalid listing segment for {member_code}")
            if node.get("member_count") != len(members):
                raise ValueError(f"incorrect member count for {node_code}")
            node_count += 1
            chain_member_count += len(members)
        if chain.get("member_count") != chain_member_count:
            raise ValueError(f"incorrect member count for value chain {code}")
        member_count += chain_member_count
    if node_count == 0:
        raise ValueError("classification snapshot contained no nodes")
    if member_count == 0:
        raise ValueError("classification snapshot contained no Taiwan securities")


def validate_snapshot(snapshot: dict[str, Any]) -> datetime:
    if not isinstance(snapshot, dict):
        raise ValueError("classification snapshot root must be an object")
    required = {"schema_version", "source", "fetched_at", "snapshot_id", "chains", "symbols"}
    missing = sorted(required - set(snapshot))
    if missing:
        raise ValueError(f"classification snapshot missing fields: {missing}")
    if snapshot["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"unsupported classification schema version: {snapshot['schema_version']}")
    if not isinstance(snapshot["chains"], list) or not isinstance(snapshot["symbols"], dict):
        raise ValueError("classification snapshot has invalid chains or symbols containers")
    _validate_chains(snapshot["chains"])
    source = snapshot["source"]
    if not isinstance(source, dict):
        raise ValueError("classification snapshot has an invalid source object")
    if source.get("name") != SOURCE_NAME or source.get("chain_count") != len(snapshot["chains"]):
        raise ValueError("classification snapshot has inconsistent source metadata")
    index_url = source.get("index_url")
    if not isinstance(index_url, str):
        raise ValueError("classification snapshot has an invalid source index URL")
    _validate_official_tpex_url(index_url)
    if not isinstance(snapshot["fetched_at"], str):
        raise ValueError("classification snapshot has an invalid fetched_at")
    fetched_at = _parse_utc(snapshot["fetched_at"])
    expected_snapshot_id = _taxonomy_digest(snapshot["chains"])
    if snapshot["snapshot_id"] != expected_snapshot_id:
        raise ValueError("classification snapshot has an inconsistent snapshot_id")
    if snapshot["symbols"] != _build_symbol_index(snapshot["chains"]):
        raise ValueError("classification snapshot has an inconsistent symbol index")
    return fetched_at


def serialize_snapshot(snapshot: dict[str, Any]) -> bytes:
    validate_snapshot(snapshot)
    return (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def latest_is_fresh(latest_path: Path, *, now: datetime, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> bool:
    try:
        snapshot = json.loads(latest_path.read_text(encoding="utf-8"))
        fetched_at = validate_snapshot(snapshot)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    now = _as_utc(now)
    age = now - fetched_at
    return timedelta(0) <= age < timedelta(days=max_age_days)


def refresh_value_chain_classification(
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    force: bool = False,
    now: datetime | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    retries: int = 3,
    retry_sleep: float = 5.0,
    timeout: float = 30.0,
    request_sleep: float = 0.25,
    fetcher: Callable[..., dict[str, Any]] = fetch_current_classification,
) -> RefreshResult:
    now = _as_utc(now or datetime.now(UTC))
    latest_path = data_dir / DEFAULT_LATEST_PATH.name
    if not force and latest_is_fresh(latest_path, now=now, max_age_days=max_age_days):
        logger.info("value-chain classification is fresh; skipping network refresh")
        return RefreshResult(False, latest_path, None, None)

    snapshot = fetcher(
        fetched_at=now,
        retries=retries,
        retry_sleep=retry_sleep,
        timeout=timeout,
        request_sleep=request_sleep,
    )
    payload = serialize_snapshot(snapshot)
    history_path = write_snapshot(payload, snapshot=snapshot, data_dir=data_dir)
    logger.info(
        "stored value-chain classification: chains=%d symbols=%d snapshot_id=%s",
        len(snapshot["chains"]),
        len(snapshot["symbols"]),
        snapshot["snapshot_id"],
    )
    return RefreshResult(True, latest_path, history_path, snapshot["snapshot_id"])


def write_snapshot(payload: bytes, *, snapshot: dict[str, Any], data_dir: Path) -> Path:
    fetched_at = validate_snapshot(snapshot)
    expected = serialize_snapshot(snapshot)
    if payload != expected:
        raise ValueError("snapshot payload does not match the validated snapshot")

    data_dir.mkdir(parents=True, exist_ok=True)
    history_dir = data_dir / DEFAULT_HISTORY_DIR.name
    history_dir.mkdir(parents=True, exist_ok=True)
    history_name = fetched_at.strftime("%Y-%m-%dT%H%M%SZ.json")
    history_path = history_dir / history_name
    created_history = False
    try:
        with history_path.open("xb") as history_file:
            created_history = True
            history_file.write(payload)
            history_file.flush()
            os.fsync(history_file.fileno())
    except Exception:
        if created_history and history_path.exists():
            history_path.unlink()
        raise

    latest_path = data_dir / DEFAULT_LATEST_PATH.name
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=data_dir,
            prefix=f".{latest_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(payload)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = Path(temp_file.name)
        temp_path.replace(latest_path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return history_path


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime values must include a timezone")
    return value.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _as_utc(parsed)


def main() -> int:
    setup_logging("fetch_value_chain_classification.log")
    parser = argparse.ArgumentParser(description="Fetch and snapshot official TPEx value-chain classification.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--force", action="store_true", help="Refresh even when the latest snapshot is fresh.")
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-sleep", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--request-sleep", type=float, default=0.25)
    args = parser.parse_args()

    result = refresh_value_chain_classification(
        data_dir=args.data_dir,
        force=args.force,
        max_age_days=args.max_age_days,
        retries=args.retries,
        retry_sleep=args.retry_sleep,
        timeout=args.timeout,
        request_sleep=args.request_sleep,
    )
    if result.refreshed:
        logger.info("latest_path=%s history_path=%s", result.latest_path, result.history_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
