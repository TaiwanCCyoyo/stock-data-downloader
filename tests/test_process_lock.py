"""Run-lock helpers shared by run_daily (``.run_daily.lock``) and the backfill."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shp_utils.process_lock import acquire_run_lock, pid_is_running, release_run_lock

REPO_ROOT = Path(__file__).resolve().parents[1]


def _dead_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def test_acquire_writes_pid_start_time_and_commit(tmp_path: Path):
    lock = tmp_path / ".run_daily.lock"

    held = acquire_run_lock(lock, git_commit="abc1234")
    try:
        record = json.loads(lock.read_text(encoding="utf-8"))
        assert record["pid"] == os.getpid()
        assert record["git_commit"] == "abc1234"
        assert record["started_at"]
    finally:
        release_run_lock(held)


def test_a_held_lock_refuses_another_process_and_keeps_its_record(tmp_path: Path):
    lock = tmp_path / ".run_daily.lock"
    held = acquire_run_lock(lock, git_commit="first")
    try:
        # A second process sees the OS lock; its own acquire must fail without rewriting the record.
        code = (
            "import sys; from pathlib import Path; "
            "from shp_utils.process_lock import RunAlreadyActiveError, acquire_run_lock\n"
            "try:\n    acquire_run_lock(Path(sys.argv[1]), git_commit='second')\n"
            "except RunAlreadyActiveError as error:\n    print(error); sys.exit(3)\n"
        )
        result = subprocess.run([sys.executable, "-c", code, str(lock)], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
        assert result.returncode == 3, result.stderr
        assert f"pid={os.getpid()}" in result.stdout
        assert json.loads(lock.read_text(encoding="utf-8"))["git_commit"] == "first"
    finally:
        release_run_lock(held)


def test_a_record_left_by_a_dead_run_is_replaced_with_a_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    # Also covers a reused pid: the OS lock, not the recorded pid, decides whether a run is alive.
    lock = tmp_path / ".run_daily.lock"
    lock.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        held = acquire_run_lock(lock, git_commit="new")
    try:
        assert f"previous run_daily did not finish cleanly (stale lock pid={os.getpid()}" in caplog.text
        assert json.loads(lock.read_text(encoding="utf-8"))["git_commit"] == "new"
    finally:
        release_run_lock(held)


def test_an_unreadable_record_is_replaced(tmp_path: Path):
    lock = tmp_path / ".run_daily.lock"
    lock.write_text("not json", encoding="utf-8")

    held = acquire_run_lock(lock, git_commit="x")
    try:
        assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid()
    finally:
        release_run_lock(held)


def test_release_removes_the_lock_and_allows_the_next_run(tmp_path: Path):
    lock = tmp_path / ".run_daily.lock"
    release_run_lock(acquire_run_lock(lock, git_commit="x"))
    assert not lock.exists()

    release_run_lock(acquire_run_lock(lock, git_commit="y"))
    assert not lock.exists()


def test_pid_probe_basics():
    assert pid_is_running(os.getpid())
    assert not pid_is_running(_dead_pid())
    assert not pid_is_running(0)
    assert not pid_is_running(True)
