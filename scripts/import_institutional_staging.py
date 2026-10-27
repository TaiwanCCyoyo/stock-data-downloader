"""Additively import a staged institutional history package into institutional.sqlite.

Every path is explicit; nothing defaults to config.DATA_DIR. See
docs/institutional-history-import.md.

    uv run scripts/import_institutional_staging.py dry-run --package <run> --db-path <db> --out plan.json
    uv run scripts/import_institutional_staging.py backup --db-path <db> --dest <new-dir>
    uv run scripts/import_institutional_staging.py apply --package <run> --db-path <db> --receipt-dir <dir> --deadline <iso>
    uv run scripts/import_institutional_staging.py digest --db-path <db> --out digest.json
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from shp_utils.corporate_action_supplement import in_producer_write_window
from shp_utils.institutional_import import InstitutionalImportError, backup_database, content_digest, load_package, run_import

logger = logging.getLogger(__name__)
DEFAULT_CUTOFF = "2018-12-31"


def running_producer_tasks() -> list[str] | None:
    """Names of running StockProject-* scheduled tasks, or None when that cannot be determined."""
    command = "Get-ScheduledTask -TaskName 'StockProject-*' | Where-Object State -eq 'Running' | ForEach-Object TaskName"
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def batch_gate(deadline: datetime) -> str | None:
    now = datetime.now(UTC)
    if now >= deadline:
        return f"deadline {deadline.isoformat()} reached"
    if in_producer_write_window(now):
        return "inside the weekday 18:00-18:45 Asia/Taipei producer window"
    running = running_producer_tasks()
    if running is None:
        return "could not determine StockProject-* task state"
    if running:
        return f"producer task(s) running: {running}"
    return None


def _code_commit() -> str | None:
    try:
        root = Path(__file__).resolve().parents[1]
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return head + ("-dirty" if dirty else "")


def _write(payload: object, out: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True)
    if out is None:
        sys.stdout.write(text + "\n")
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        logger.info("wrote %s", out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("dry-run", "apply"):
        p = sub.add_parser(name)
        p.add_argument("--package", type=Path, required=True)
        p.add_argument("--db-path", type=Path, required=True)
        p.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    sub.choices["dry-run"].add_argument("--out", type=Path)
    sub.choices["apply"].add_argument("--receipt-dir", type=Path, required=True)
    sub.choices["apply"].add_argument("--deadline", type=datetime.fromisoformat, required=True, help="No batch starts at or after this time (ISO with offset)")
    sub.choices["apply"].add_argument("--batch-units", type=int, default=50)
    backup = sub.add_parser("backup")
    backup.add_argument("--db-path", type=Path, required=True)
    backup.add_argument("--dest", type=Path, required=True, help="New directory; must not exist")
    digest = sub.add_parser("digest")
    digest.add_argument("--db-path", type=Path, required=True)
    digest.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        if args.command == "digest":
            _write(content_digest(args.db_path), args.out)
            return 0
        if args.command == "backup":
            record = backup_database(args.db_path, args.dest)
            logger.info(
                "backup %s verified: integrity=%s digest_match=%s", record["snapshot"], record["integrity_check"], record["content_digest_matches_live"]
            )
            return 0
        package = load_package(args.package)
        logger.info("package %s verified, payload digest %s", package.run_id, package.payload_digest)
        if args.command == "dry-run":
            _write(run_import(package, args.db_path, cutoff=args.cutoff, receipt_dir=None, code_commit=_code_commit()), args.out)
            return 0
        if args.deadline.tzinfo is None:
            parser.error("--deadline needs a UTC offset")
        commit = _code_commit()
        if commit is None or commit.endswith("-dirty"):
            logger.error("refusing to apply from uncommitted code (%s)", commit)
            return 3
        reason = batch_gate(args.deadline)
        if reason:
            logger.error("refusing to start: %s", reason)
            return 3
        summary = run_import(
            package,
            args.db_path,
            cutoff=args.cutoff,
            receipt_dir=args.receipt_dir,
            batch_units=args.batch_units,
            may_start_batch=lambda: batch_gate(args.deadline),
            code_commit=commit,
        )
        logger.info("import finished, stopped=%s", summary["stopped"])
        return 0 if summary["stopped"] is None else 4
    except InstitutionalImportError as exc:
        logger.error("import refused: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
