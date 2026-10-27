"""Status publication reads actual synthetic artifacts and preserves prior output."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shp_utils import data_status

NOW = datetime(2026, 10, 24, 0, 0, tzinfo=UTC)
RECENT = "2026-10-23T18:00:00+00:00"


def create_sqlite(root: Path, dataset: data_status.Dataset, *, empty: bool = False) -> None:
    period_column = "fetched_at" if dataset.id == "symbol_meta" else dataset.period
    columns = dict.fromkeys(["code", period_column, dataset.updated], "TEXT")
    with sqlite3.connect(root / dataset.file) as connection:
        connection.execute(f"CREATE TABLE {dataset.table} ({', '.join(name + ' ' + kind for name, kind in columns.items())})")
        if not empty:
            periods = ["2026-09", "2026-09"] if dataset.id == "monthly_revenue" else ["2026-10-22", "2026-10-23"]
            for code, period in zip(["2330", "2317"], periods, strict=True):
                values = {"code": code, period_column: period, dataset.updated: RECENT}
                connection.execute(f"INSERT INTO {dataset.table} VALUES ({','.join('?' for _ in columns)})", [values[name] for name in columns])


def create_parquet(root: Path) -> None:
    path = root / "price_daily.parquet"
    pq.write_table(pa.table({"Code": ["2330", "2330", "2317"], "Date": ["2026-10-22", "2026-10-23", "2026-10-23"]}), path)
    os.utime(path, (NOW.timestamp(), NOW.timestamp()))


def indexed(root: Path, *, failed_datasets: set[str] | None = None) -> dict[str, dict]:
    payload = data_status.collect_data_status(root, now=NOW, failed_datasets=failed_datasets)
    return {entry["id"]: entry for entry in payload["datasets"]}


def test_all_artifacts_report_measured_periods_counts_and_timestamps(tmp_path: Path) -> None:
    for dataset in data_status.DATASETS:
        if dataset.table:
            create_sqlite(tmp_path, dataset)
    create_parquet(tmp_path)
    entries = indexed(tmp_path)
    assert set(entries) == {dataset.id for dataset in data_status.DATASETS}
    assert all(entry["status"] == "ok" for entry in entries.values())
    assert entries["price_daily"]["rows"] == 3
    assert entries["price_daily"]["symbols"] == 2
    assert entries["official_daily"]["data_period"] == {"from": "2026-10-22", "to": "2026-10-23"}
    assert entries["monthly_revenue"]["data_period"] == {"from": "2026-09", "to": "2026-09"}
    assert entries["symbol_meta"]["data_period"] == {"from": "2026-10-23", "to": "2026-10-23"}
    assert entries["institutional"]["last_updated"] == RECENT


def test_missing_empty_and_corrupt_are_separate_from_known_zero(tmp_path: Path) -> None:
    create_sqlite(tmp_path, data_status.DATASETS[0], empty=True)
    (tmp_path / "institutional.sqlite").write_bytes(b"not sqlite")
    entries = indexed(tmp_path)
    assert all(entry["status"] == "missing" for entry in entries.values())
    assert entries["official_daily"]["rows"] == 0
    assert entries["official_daily"]["symbols"] == 0
    assert entries["institutional"]["rows"] is None
    assert entries["price_daily"]["rows"] is None


def test_recent_backfill_of_old_period_remains_stale(tmp_path: Path) -> None:
    dataset = data_status.DATASETS[0]
    create_sqlite(tmp_path, dataset)
    with sqlite3.connect(tmp_path / dataset.file) as connection:
        connection.execute(f"UPDATE {dataset.table} SET date='2020-01-01'")
    assert indexed(tmp_path)[dataset.id]["status"] == "stale"


def test_failed_refresh_and_old_observation_are_stale(tmp_path: Path) -> None:
    dataset = data_status.DATASETS[0]
    create_sqlite(tmp_path, dataset)
    assert indexed(tmp_path, failed_datasets={dataset.id})[dataset.id]["status"] == "stale"
    with sqlite3.connect(tmp_path / dataset.file) as connection:
        connection.execute(f"UPDATE {dataset.table} SET fetched_at='2026-09-01T00:00:00+00:00'")
    assert indexed(tmp_path)[dataset.id]["status"] == "stale"


def test_sqlite_reads_committed_rows_still_in_wal(tmp_path: Path) -> None:
    dataset = data_status.DATASETS[0]
    create_sqlite(tmp_path, dataset)
    connection = sqlite3.connect(tmp_path / dataset.file)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(f"INSERT INTO {dataset.table} VALUES ('2454', '2026-10-23', ?)", [RECENT])
        connection.commit()
        assert indexed(tmp_path)[dataset.id]["rows"] == 3
    finally:
        connection.close()


def test_changed_artifact_is_not_published_as_healthy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dataset = data_status.DATASETS[0]
    create_sqlite(tmp_path, dataset)
    original = data_status._sqlite_stats

    def changing(path: Path, definition: data_status.Dataset, deadline: float) -> dict:
        stats = original(path, definition, deadline)
        path.write_bytes(path.read_bytes() + b"changed")
        return stats

    monkeypatch.setattr(data_status, "_sqlite_stats", changing)
    entry = indexed(tmp_path)[dataset.id]
    assert entry["status"] == "missing"
    assert entry["rows"] is None
    assert entry["unavailable_reason"] == "changed_during_read"


def test_atomic_failure_keeps_previous_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = tmp_path / "data-status.json"
    destination.write_bytes(b"previous status")

    def failure(*_args: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(data_status.os, "replace", failure)
    with pytest.raises(OSError, match="replace failed"):
        data_status.write_data_status(tmp_path, now=NOW)
    assert destination.read_bytes() == b"previous status"
    assert not list(tmp_path.glob(".data-status-*"))


def test_successful_publication_is_readable_json(tmp_path: Path) -> None:
    payload = data_status.write_data_status(tmp_path, now=NOW)
    assert json.loads((tmp_path / "data-status.json").read_text(encoding="utf-8")) == payload
    assert payload["schema"] == "producer-data-status.v1"
    assert payload["generated_at"] == NOW.isoformat(timespec="seconds")


def test_one_mid_read_change_is_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dataset = data_status.DATASETS[0]
    create_sqlite(tmp_path, dataset)
    original = data_status._sqlite_stats
    calls = []

    def changing_once(path: Path, definition: data_status.Dataset, deadline: float) -> dict:
        calls.append(deadline)
        stats = original(path, definition, deadline)
        if len(calls) == 1:
            path.write_bytes(path.read_bytes() + b"changed")
        return stats

    monkeypatch.setattr(data_status, "_sqlite_stats", changing_once)
    entry = indexed(tmp_path)[dataset.id]
    assert entry["status"] == "ok" and len(calls) == 2
    assert calls[0] == calls[1]  # the retry spends the same budget, it does not get a new one
    assert "unavailable_reason" not in entry


def test_unavailable_entries_name_their_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    create_sqlite(tmp_path, data_status.DATASETS[0])

    def deadline(_path: Path, _definition: data_status.Dataset, _deadline: float) -> dict:
        raise sqlite3.OperationalError("interrupted")

    monkeypatch.setattr(data_status, "_sqlite_stats", deadline)
    entries = indexed(tmp_path)
    assert entries["official_daily"]["unavailable_reason"] == "read_deadline"
    assert entries["official_daily"]["status"] == "missing" and entries["official_daily"]["rows"] is None
    assert entries["price_daily"]["unavailable_reason"] == "absent"


@pytest.mark.parametrize("period_index", [False, True])
def test_last_updated_observes_the_newest_period(tmp_path: Path, period_index: bool) -> None:
    dataset = next(item for item in data_status.DATASETS if item.id == "institutional")
    create_sqlite(tmp_path, dataset)
    with sqlite3.connect(tmp_path / dataset.file) as connection:
        if period_index:
            connection.execute(f"CREATE INDEX by_period ON {dataset.table} ({dataset.period}, code)")
        connection.execute(f"INSERT INTO {dataset.table} VALUES ('1101', '2012-05-02', '2026-10-23T23:00:00+00:00')")
    entry = indexed(tmp_path)[dataset.id]
    assert entry["last_updated"] == RECENT
    assert entry["rows"] == 3 and entry["symbols"] == 3
    assert entry["data_period"] == {"from": "2012-05-02", "to": "2026-10-23"}


def test_retry_does_not_start_after_the_shared_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dataset = data_status.DATASETS[0]
    create_sqlite(tmp_path, dataset)
    clock = [1000.0]
    monkeypatch.setattr(data_status.time, "monotonic", lambda: clock[0])
    calls = []

    def slow_and_changing(path: Path, _definition: data_status.Dataset, deadline: float) -> dict:
        calls.append(deadline)
        clock[0] += 121.0  # one attempt spends the whole shared budget
        path.write_bytes(path.read_bytes() + b"changed")
        return {}

    monkeypatch.setattr(data_status, "_sqlite_stats", slow_and_changing)
    entry = indexed(tmp_path)[dataset.id]
    assert calls == [1000.0 + data_status.SQLITE_READ_SECONDS]
    assert entry["unavailable_reason"] == "read_deadline"


def test_null_code_is_not_counted_as_a_symbol(tmp_path: Path) -> None:
    dataset = next(item for item in data_status.DATASETS if item.id == "symbol_meta")
    with sqlite3.connect(tmp_path / dataset.file) as connection:
        connection.execute("CREATE TABLE symbol_meta (code TEXT PRIMARY KEY, name TEXT, fetched_at TEXT)")
        connection.execute("INSERT INTO symbol_meta VALUES (NULL, 'unknown', ?)", [RECENT])
    entry = indexed(tmp_path)[dataset.id]
    assert entry["status"] == "missing" and entry["unavailable_reason"] == "unreadable"
    with sqlite3.connect(tmp_path / dataset.file) as connection:
        connection.execute("INSERT INTO symbol_meta VALUES ('2330', 'TSMC', ?)", [RECENT])
    entry = indexed(tmp_path)[dataset.id]
    assert entry["status"] == "ok" and entry["rows"] == 2 and entry["symbols"] == 1
