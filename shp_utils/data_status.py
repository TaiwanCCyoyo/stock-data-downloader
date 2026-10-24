"""Publish small, read-only descriptions of the existing local data artifacts."""

from __future__ import annotations

import calendar
import json
import logging
import os
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow.compute as pc
import pyarrow.parquet as pq

LOGGER = logging.getLogger(__name__)
SCHEMA = "producer-data-status.v1"
SQLITE_READ_SECONDS = 120


@dataclass(frozen=True)
class Dataset:
    id: str
    name: str
    file: str
    table: str = ""
    period: str = "date"
    updated: str = "fetched_at"
    days: int = 7
    observation_only: bool = False


DATASETS = (
    Dataset("official_daily", "每日官方股價", "official_daily.sqlite", "official_daily_price"),
    Dataset("price_daily", "整合每日股價", "price_daily.parquet"),
    Dataset("institutional", "三大法人每日買賣", "institutional.sqlite", "institutional_daily"),
    Dataset("monthly_revenue", "每月營收", "monthly_revenue.sqlite", "monthly_revenue_current", "revenue_month", "last_fetched_at", 45),
    Dataset("corporate_actions", "除權息與減資", "corporate_actions.sqlite", "corporate_actions", "ex_date", observation_only=True),
    Dataset("symbol_meta", "股票名稱與產業", "symbol_meta.sqlite", "symbol_meta", "substr(fetched_at, 1, 10)", days=14, observation_only=True),
)


def _identity(path: Path) -> tuple[tuple[int, int] | None, ...]:
    result: list[tuple[int, int] | None] = []
    for source in (path, Path(str(path) + "-wal")):
        try:
            info = source.stat()
            result.append((info.st_size, info.st_mtime_ns))
        except FileNotFoundError:
            result.append(None)
    return tuple(result)


def _timestamp(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        raise ValueError("observation timestamp has no timezone")
    return timestamp.astimezone(UTC)


def _sqlite_stats(path: Path, dataset: Dataset) -> dict[str, Any]:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    # This is a once-per-refresh inventory, not an interactive request. The
    # official-price artifact's full aggregate exceeds 30 seconds on local disk.
    deadline = time.monotonic() + SQLITE_READ_SECONDS
    try:
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10_000)
        # Expressions come only from the static definitions above, never user input.
        row = connection.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT code), MIN({dataset.period}), MAX({dataset.period}), MAX({dataset.updated}) FROM {dataset.table}"
        ).fetchone()
        assert row is not None
        return {"rows": row[0], "symbols": row[1], "data_period": {"from": row[2], "to": row[3]}, "last_updated": row[4]}
    finally:
        connection.close()


def _parquet_stats(path: Path) -> dict[str, Any]:
    symbols: set[str] = set()
    first: date | None = None
    last: date | None = None
    rows = 0
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(columns=["Code", "Date"], batch_size=131_072):
        if batch.column("Code").null_count or batch.column("Date").null_count:
            raise ValueError("price identity columns contain nulls")
        symbols.update(str(code) for code in pc.call_function("unique", [batch.column("Code")]).to_pylist())
        bounds = pc.call_function("min_max", [batch.column("Date")]).as_py()
        if bounds["min"] is not None:
            low = date.fromisoformat(str(bounds["min"])[:10])
            high = date.fromisoformat(str(bounds["max"])[:10])
            first = low if first is None else min(first, low)
            last = high if last is None else max(last, high)
        rows += batch.num_rows
    return {
        "rows": rows,
        "symbols": len(symbols),
        "data_period": {"from": first.isoformat() if first else None, "to": last.isoformat() if last else None},
        "last_updated": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds"),
    }


def _fresh(stats: dict[str, Any], dataset: Dataset, now: datetime) -> bool:
    updated = _timestamp(stats["last_updated"])
    tolerance = timedelta(days=dataset.days)
    if updated > now + timedelta(minutes=5) or now - updated > tolerance:
        return False
    if dataset.observation_only:
        return True
    last = stats["data_period"]["to"]
    if dataset.id == "monthly_revenue":
        year, month = (int(part) for part in last.split("-"))
        end = date(year, month, calendar.monthrange(year, month)[1])
    else:
        end = date.fromisoformat(last)
    return timedelta(0) <= now.date() - end <= tolerance


def collect_data_status(data_dir: Path, *, now: datetime | None = None, failed_datasets: set[str] | None = None) -> dict[str, Any]:
    """Read statistics in isolation; missing or unreadable data never look healthy."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("timezone-aware status time required")
    moment = moment.astimezone(UTC)
    failed = failed_datasets or set()
    entries = []
    for dataset in DATASETS:
        started = time.monotonic()
        entry: dict[str, Any] = {
            "id": dataset.id,
            "name": dataset.name,
            "data_period": {"from": None, "to": None},
            "last_updated": None,
            "symbols": None,
            "rows": None,
            "status": "missing",
        }
        source = data_dir / dataset.file
        try:
            if not source.is_file():
                raise FileNotFoundError(dataset.file)
            before = _identity(source)
            stats = _sqlite_stats(source, dataset) if dataset.table else _parquet_stats(source)
            if before != _identity(source):
                raise ValueError("artifact changed while gathering status")
            if stats["rows"]:
                if stats["symbols"] < 1 or not all(stats["data_period"].values()) or not stats["last_updated"]:
                    raise ValueError("nonempty artifact has incomplete identity metadata")
                entry["status"] = "ok" if _fresh(stats, dataset, moment) and dataset.id not in failed else "stale"
            entry.update(stats)
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            LOGGER.warning("Data status unavailable dataset=%s: %s", dataset.id, exc)
        entries.append(entry)
        LOGGER.info("Data status dataset=%s state=%s rows=%s seconds=%.3f", dataset.id, entry["status"], entry["rows"], time.monotonic() - started)
    LOGGER.info("Collected data status for %d artifacts", len(entries))
    return {"schema": SCHEMA, "generated_at": moment.isoformat(timespec="seconds"), "datasets": entries}


def write_data_status(data_dir: Path, *, now: datetime | None = None, failed_datasets: set[str] | None = None) -> dict[str, Any]:
    """Atomically replace only the summary; preserve its previous bytes on failure."""
    payload = collect_data_status(data_dir, now=now, failed_datasets=failed_datasets)
    destination = data_dir / "data-status.json"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=data_dir, prefix=".data-status-", suffix=".json", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        LOGGER.info("Published data status (%d datasets)", len(payload["datasets"]))
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return payload
