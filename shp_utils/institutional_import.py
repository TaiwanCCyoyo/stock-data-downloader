"""Additive import of a staged institutional history package.

See docs/institutional-history-import.md. Plain INSERTs only: any key already in the
target either matches (skipped) or is a conflict (reported, never overwritten).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shp_utils.institutional_schema import MARKETS, ROW_COLUMNS

logger = logging.getLogger(__name__)

LOG_COLUMNS = (
    "market",
    "date",
    "source_url",
    "status",
    "row_count",
    "schema_signature",
    "published_at",
    "fetched_at",
    "raw_path",
    "sha256",
    "error_detail",
    "final_url",
)
# Observation metadata, not report content: compared and reported separately.
NON_CONTENT_COLUMNS = frozenset({"fetched_at", "instrument_type"})
SCHEMA_VERSION = "3"
_RUN_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z+_.-]*$")
_STAGED_RAW = re.compile(r"^raw/institutional/(twse|tpex)/([0-9A-Za-z+_.-]+\.json)$")


class InstitutionalImportError(RuntimeError):
    """The package or target cannot be imported safely; nothing was changed by this step."""


@dataclass(frozen=True)
class Package:
    root: Path
    run_id: str
    manifest: dict[str, Any]
    payload_digest: str


@dataclass
class UnitPlan:
    market: str
    date: str
    kind: str  # insert | already_imported | equivalent_skip | conflict
    rows: int
    detail: str = ""
    metadata_differences: dict[str, int] = field(default_factory=dict)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_package(root: Path, *, verify_files: bool = True) -> Package:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    files = manifest["files"]
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if digest != manifest["payload_digest"]["value"]:
        raise InstitutionalImportError(f"manifest payload digest mismatch: recomputed {digest}")
    run_id = str(manifest["run_id"])
    if not _RUN_ID.match(run_id) or run_id in {".", ".."}:
        raise InstitutionalImportError(f"unsafe run id {run_id!r}")
    if verify_files:
        bad = [
            entry["path"]
            for entry in files
            if not (root / entry["path"]).is_file()
            or (root / entry["path"]).stat().st_size != entry["bytes"]
            or file_sha256(root / entry["path"]) != entry["sha256"]
        ]
        if bad:
            raise InstitutionalImportError(f"{len(bad)} package file(s) missing or changed, first: {bad[:5]}")
    return Package(root, run_id, manifest, digest)


def open_staging(package: Package) -> sqlite3.Connection:
    path = package.root / "institutional.sqlite"
    return sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro&immutable=1", uri=True)


def open_target(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise InstitutionalImportError(f"target database not found: {db_path} (the importer never creates it)")
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def check_schema(staging: sqlite3.Connection, target: sqlite3.Connection) -> None:
    for table in ("institutional_daily", "institutional_fetch_log"):
        left, right = _table_shape(staging, table), _table_shape(target, table)
        if left != right:
            raise InstitutionalImportError(f"{table} schema differs: staging {left} target {right}")
    for name, conn in (("staging", staging), ("target", target)):
        row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        if row is None or row[0] != SCHEMA_VERSION:
            raise InstitutionalImportError(f"{name} schema_version is {row and row[0]!r}, expected {SCHEMA_VERSION}")
    missing = set(ROW_COLUMNS) - set(_table_shape(target, "institutional_daily")) | set(LOG_COLUMNS) - set(_table_shape(target, "institutional_fetch_log"))
    if missing:
        raise InstitutionalImportError(f"target lacks columns {sorted(missing)}")


def _table_shape(conn: sqlite3.Connection, table: str) -> dict[str, tuple[str, int, int]]:
    return {str(r[1]): (str(r[2]).upper(), int(r[3]), int(r[5])) for r in conn.execute(f"PRAGMA table_info({table})")}


def target_raw_path(staged_raw_path: str) -> str:
    """The same DB-relative location the fetcher uses, so imported raw joins the ordinary tree."""
    match = _STAGED_RAW.match(staged_raw_path)
    if match is None:
        raise InstitutionalImportError(f"unexpected staged raw path {staged_raw_path!r}")
    return f"raw/institutional/{match.group(1)}/{match.group(2)}"


def select_candidates(package: Package, staging: sqlite3.Connection, cutoff: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    unresolved = {(u["market"], u["date"]) for u in package.manifest.get("unresolved_units", [])}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for unit in package.manifest["importable_units"]:
        key = (unit["market"], unit["date"])
        log = staging.execute(
            "SELECT status, row_count, raw_path, sha256, final_url FROM institutional_fetch_log WHERE market = ? AND date = ?",
            key,
        ).fetchone()
        stored = staging.execute("SELECT COUNT(*) FROM institutional_daily WHERE market = ? AND date = ?", key).fetchone()[0]
        reason = None
        if key in seen:
            reason = "duplicate manifest unit"
        elif unit["market"] not in MARKETS:
            reason = "unknown market"
        elif unit["date"] > cutoff:
            reason = f"after cutoff {cutoff}"
        elif key in unresolved:
            reason = "listed as unresolved"
        elif log is None or log[0] != "ok" or log[1] != unit["rows"] or log[1] <= 0 or stored != log[1]:
            reason = f"staging checkpoint not a complete ok unit: {log and log[:2]} stored={stored}"
        elif (log[2], log[3]) != (unit["raw_path"], unit["sha256"]) or not (log[4] or "").strip():
            reason = "staging checkpoint provenance differs from manifest"
        else:
            raw = package.root / unit["raw_path"]
            if not raw.is_file() or file_sha256(raw) != unit["sha256"]:
                reason = "staged raw missing or digest mismatch"
        seen.add(key)
        if reason:
            rejected.append({"market": key[0], "date": key[1], "rows": unit.get("rows"), "reason": reason})
        else:
            accepted.append(unit)
    beyond = staging.execute("SELECT COUNT(*) FROM institutional_daily WHERE date > ?", (cutoff,)).fetchone()[0]
    if beyond:
        rejected.append({"market": None, "date": None, "rows": beyond, "reason": f"staging rows after cutoff {cutoff} (never read)"})
    return accepted, rejected


def plan_unit(target: sqlite3.Connection, staging: sqlite3.Connection, unit: dict[str, Any], run_id: str, target_root: Path) -> UnitPlan:
    market, day = unit["market"], unit["date"]
    columns = ", ".join(ROW_COLUMNS)
    staged = {
        r[2]: dict(zip(ROW_COLUMNS, r, strict=True))
        for r in staging.execute(f"SELECT {columns} FROM institutional_daily WHERE market = ? AND date = ?", (market, day))
    }
    existing = {r[2]: dict(zip(ROW_COLUMNS, r, strict=True)) for r in target.execute(f"SELECT {columns} FROM institutional_daily WHERE date = ?", (day,))}
    log = target.execute(
        "SELECT status, row_count, raw_path, sha256 FROM institutional_fetch_log WHERE market = ? AND date = ?",
        (market, day),
    ).fetchone()
    new_raw = target_raw_path(unit["raw_path"])
    plan = UnitPlan(market, day, "insert", len(staged))

    extra = [code for code, row in existing.items() if row["market"] == market and code not in staged]
    if extra:
        return _conflict(plan, f"target has {len(extra)} {market} row(s) absent from the staged report, e.g. {extra[:3]}")
    present = 0
    for code, row in staged.items():
        other = existing.get(code)
        if other is None:
            continue
        present += 1
        if other["market"] != market:
            return _conflict(plan, f"({day}, {code}) already stored for market {other['market']}")
        changed = [c for c in ROW_COLUMNS if c not in NON_CONTENT_COLUMNS and other[c] != row[c]]
        if changed:
            return _conflict(plan, f"({day}, {code}) content differs in {changed[:5]}")
        for c in NON_CONTENT_COLUMNS:
            if other[c] != row[c]:
                plan.metadata_differences[c] = plan.metadata_differences.get(c, 0) + 1

    raw_dest = target_root / new_raw
    if raw_dest.exists() and file_sha256(raw_dest) != unit["sha256"]:
        return _conflict(plan, f"{new_raw} exists with a different digest")

    if log is None:
        if present:
            return _conflict(plan, f"target holds {present} row(s) for this unit without a checkpoint")
        return plan
    if present != len(staged) or log[0] != "ok" or log[1] != len(staged):
        return _conflict(plan, f"target checkpoint {log[:2]} with {present}/{len(staged)} matching rows")
    if (log[2], log[3]) == (new_raw, unit["sha256"]):
        plan.kind = "already_imported"
    else:
        plan.kind = "equivalent_skip"
        plan.detail = f"equivalent content already checkpointed from {log[2]}"
    return plan


def _conflict(plan: UnitPlan, detail: str) -> UnitPlan:
    plan.kind, plan.detail = "conflict", detail
    return plan


def preserve_raw(package: Package, unit: dict[str, Any], target_root: Path) -> tuple[str, bool]:
    """Copy one staged snapshot into the target's raw tree. Returns (path, created)."""
    rel = target_raw_path(unit["raw_path"])
    dest = target_root / rel
    if dest.exists():
        if file_sha256(dest) != unit["sha256"]:
            raise InstitutionalImportError(f"{rel} exists with a different digest; not replaced")
        return rel, False
    data = (package.root / unit["raw_path"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != unit["sha256"]:
        raise InstitutionalImportError(f"staged {unit['raw_path']} digest changed")
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_name(f".{dest.name}.{os.getpid()}.partial")
    with temp.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    if file_sha256(temp) != unit["sha256"]:
        raise InstitutionalImportError(f"{temp} verification failed; kept for inspection")
    if dest.exists():
        raise InstitutionalImportError(f"{rel} appeared during copy; staged copy kept at {temp.name}")
    os.rename(temp, dest)
    return rel, True


def insert_unit(target: sqlite3.Connection, staging: sqlite3.Connection, unit: dict[str, Any], run_id: str) -> int:
    key = (unit["market"], unit["date"])
    columns = ", ".join(ROW_COLUMNS)
    rows = staging.execute(f"SELECT {columns} FROM institutional_daily WHERE market = ? AND date = ?", key).fetchall()
    target.executemany(f"INSERT INTO institutional_daily ({columns}) VALUES ({', '.join('?' * len(ROW_COLUMNS))})", rows)
    log_columns = ", ".join(LOG_COLUMNS)
    log = dict(
        zip(LOG_COLUMNS, staging.execute(f"SELECT {log_columns} FROM institutional_fetch_log WHERE market = ? AND date = ?", key).fetchone(), strict=True)
    )
    log["raw_path"] = target_raw_path(log["raw_path"])
    target.execute(f"INSERT INTO institutional_fetch_log ({log_columns}) VALUES ({', '.join('?' * len(LOG_COLUMNS))})", [log[c] for c in LOG_COLUMNS])
    return len(rows)


class Receipt:
    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "import-receipt.jsonl"

    def write(self, event: str, **payload: Any) -> None:
        record = {"event": event, "at": datetime.now(UTC).isoformat(timespec="seconds"), **payload}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def summarize(plans: Iterable[UnitPlan], rejected: Iterable[dict[str, Any]], unresolved: Iterable[dict[str, Any]]) -> dict[str, Any]:
    table: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    for plan in plans:
        cell = table[plan.market][plan.date[:4]]
        cell[f"{plan.kind}_units"] += 1
        cell[f"{plan.kind}_rows"] += plan.rows
    for item in rejected:
        if item["market"]:
            cell = table[item["market"]][item["date"][:4]]
            cell["rejected_units"] += 1
            cell["rejected_rows"] += item["rows"] or 0
    for item in unresolved:
        table[item["market"]][item["date"][:4]]["unresolved_units"] += 1
    return {market: {year: dict(sorted(counts.items())) for year, counts in sorted(years.items())} for market, years in sorted(table.items())}


def run_import(
    package: Package,
    target_db: Path,
    *,
    cutoff: str,
    receipt_dir: Path | None,
    batch_units: int = 50,
    may_start_batch: Callable[[], str | None] = lambda: None,
    code_commit: str | None = None,
    fail_after_insert: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Plan (receipt_dir None) or apply the import. Returns the summary."""
    target_root = target_db.resolve().parent
    with closing(open_staging(package)) as staging, closing(open_target(target_db)) as target:
        check_schema(staging, target)
        accepted, rejected = select_candidates(package, staging, cutoff)
        plans = [plan_unit(target, staging, unit, package.run_id, target_root) for unit in accepted]
        unresolved = package.manifest.get("unresolved_units", [])
        logger.info(
            "package %s: %d candidate unit(s), %d rejected, plan %s", package.run_id, len(accepted), len(rejected), dict(Counter(p.kind for p in plans))
        )
        base = {
            "run_id": package.run_id,
            "payload_digest": package.payload_digest,
            "target_db": str(target_db),
            "cutoff": cutoff,
            "code_commit": code_commit,
            "candidates": len(accepted),
            "rejected": rejected,
            "unresolved_units": len(unresolved),
        }
        if receipt_dir is None:
            return {**base, "mode": "dry-run", "plans": [vars(p) for p in plans], "by_market_year": summarize(plans, rejected, unresolved)}

        receipt = Receipt(receipt_dir)
        receipt.write("start", **base)
        for plan in plans:
            if plan.kind != "insert":
                receipt.write(plan.kind, market=plan.market, date=plan.date, rows=plan.rows, detail=plan.detail, metadata_differences=plan.metadata_differences)
        by_key = {(u["market"], u["date"]): u for u in accepted}
        pending = [by_key[(p.market, p.date)] for p in plans if p.kind == "insert"]
        final: dict[tuple[str, str], UnitPlan] = {(p.market, p.date): p for p in plans}
        stopped = None
        for start in range(0, len(pending), batch_units):
            reason = may_start_batch()
            if reason:
                stopped = reason
                receipt.write("stopped", reason=reason, remaining_units=len(pending) - start)
                break
            batch = pending[start : start + batch_units]
            preserved = [preserve_raw(package, unit, target_root) for unit in batch]
            receipt.write("raw_preserved", files=[{"path": p, "created": c} for p, c in preserved])
            committed: list[dict[str, Any]] = []
            target.execute("BEGIN IMMEDIATE")
            try:
                for unit in batch:
                    recheck = plan_unit(target, staging, unit, package.run_id, target_root)
                    if recheck.kind != "insert":
                        raise InstitutionalImportError(f"{unit['market']} {unit['date']} changed to {recheck.kind}: {recheck.detail}")
                    rows = insert_unit(target, staging, unit, package.run_id)
                    if fail_after_insert is not None:
                        fail_after_insert(unit)
                    committed.append({"market": unit["market"], "date": unit["date"], "rows": rows})
                target.execute("COMMIT")
            except BaseException as exc:
                target.execute("ROLLBACK")
                receipt.write("batch_rolled_back", error=str(exc), units=[f"{u['market']} {u['date']}" for u in batch], raw_kept=[p for p, _ in preserved])
                raise
            receipt.write("batch_committed", units=committed, rows=sum(u["rows"] for u in committed))
            logger.info(
                "committed batch of %d unit(s), %d row(s); %d unit(s) left",
                len(committed),
                sum(u["rows"] for u in committed),
                len(pending) - start - len(batch),
            )
        committed_keys = set()
        for line in receipt.path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record["event"] == "batch_committed":
                committed_keys |= {(u["market"], u["date"]) for u in record["units"]}
        for key, plan in final.items():
            if plan.kind == "insert" and key not in committed_keys:
                plan.kind = "not_started"
            elif plan.kind == "insert":
                plan.kind = "inserted"
        summary = {**base, "mode": "apply", "stopped": stopped, "by_market_year": summarize(final.values(), rejected, unresolved)}
        receipt.write("finish", stopped=stopped, by_market_year=summary["by_market_year"])
        (receipt_dir / "import-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        return summary


def content_digest(db_path: Path) -> dict[str, Any]:
    """Per (table, market, year) row count and SHA-256 over every column, plus schema digests."""
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        conn.execute("BEGIN")
        result: dict[str, Any] = {}
        for table, order in (("institutional_daily", "market, date, code"), ("institutional_fetch_log", "market, date")):
            columns = [str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")]
            groups: dict[str, Any] = {}
            current, digest, count = None, None, 0
            for row in conn.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY {order}"):
                record = dict(zip(columns, row, strict=True))
                key = f"{record['market']}/{record['date'][:4]}"
                if key != current:
                    if current is not None:
                        groups[current] = {"rows": count, "sha256": digest.hexdigest()}  # type: ignore[union-attr]
                    current, digest, count = key, hashlib.sha256(), 0
                digest.update(json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n")  # type: ignore[union-attr]
                count += 1
            if current is not None:
                groups[current] = {"rows": count, "sha256": digest.hexdigest()}  # type: ignore[union-attr]
            result[table] = groups
        for name, sql in (
            ("schema_meta", "SELECT key, value, updated_at FROM schema_meta ORDER BY key"),
            ("sqlite_master", "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"),
        ):
            result[name] = hashlib.sha256(json.dumps(conn.execute(sql).fetchall(), ensure_ascii=False).encode("utf-8")).hexdigest()
        conn.execute("COMMIT")
    return result


def backup_database(db_path: Path, dest_dir: Path) -> dict[str, Any]:
    """SQLite online backup into a new directory, then integrity check and digest comparison."""
    data_dir = db_path.resolve().parent
    dest_dir = dest_dir.resolve()
    if dest_dir == data_dir or data_dir in dest_dir.parents or dest_dir in data_dir.parents:
        raise InstitutionalImportError(f"backup destination {dest_dir} overlaps the data directory {data_dir}")
    dest_dir.mkdir(parents=True, exist_ok=False)
    snapshot = dest_dir / db_path.name
    started = datetime.now(UTC)
    with closing(sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)) as source, closing(sqlite3.connect(snapshot)) as copy:
        source.backup(copy)
    with closing(sqlite3.connect(f"file:{snapshot.as_posix()}?mode=ro", uri=True)) as check:
        integrity = [r[0] for r in check.execute("PRAGMA integrity_check")]
        journal = check.execute("PRAGMA journal_mode").fetchone()[0]
    live, copied = content_digest(db_path), content_digest(snapshot)
    record = {
        "source": str(db_path),
        "snapshot": str(snapshot),
        "method": "sqlite3 online backup API from a mode=ro source connection",
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "bytes": snapshot.stat().st_size,
        "sha256": file_sha256(snapshot),
        "journal_mode": journal,
        "integrity_check": integrity,
        "content_digest_matches_live": live == copied,
        "content_digest": copied,
    }
    (dest_dir / "backup-manifest.json").write_text(json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    if integrity != ["ok"] or live != copied:
        raise InstitutionalImportError(f"backup verification failed: integrity={integrity} digest_match={live == copied}")
    return record
