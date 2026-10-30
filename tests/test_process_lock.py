"""Run-lock helpers shared by run_daily (``.run_daily.lock``) and the backfill."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shp_utils.process_lock import RunAlreadyActiveError, acquire_run_lock, pid_is_running, release_run_lock


def _dead_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def test_acquire_writes_pid_start_time_and_commit(tmp_path: Path):
    lock = tmp_path / ".run_daily.lock"

    acquire_run_lock(lock, git_commit="abc1234")

    record = json.loads(lock.read_text(encoding="utf-8"))
    assert record["pid"] == os.getpid()
    assert record["git_commit"] == "abc1234"
    assert record["started_at"]


def test_a_live_lock_is_refused_and_left_untouched(tmp_path: Path):
    lock = tmp_path / ".run_daily.lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "started_at": "t", "git_commit": "old"}), encoding="utf-8")

    with pytest.raises(RunAlreadyActiveError, match=str(os.getpid())):
        acquire_run_lock(lock, git_commit="new")

    assert json.loads(lock.read_text(encoding="utf-8"))["git_commit"] == "old"


def test_a_stale_lock_is_replaced_with_a_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    lock = tmp_path / ".run_daily.lock"
    dead = _dead_pid()
    lock.write_text(json.dumps({"pid": dead}), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        acquire_run_lock(lock, git_commit="new")

    assert f"previous run_daily did not finish cleanly (stale lock pid={dead}" in caplog.text
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_an_unreadable_lock_is_replaced(tmp_path: Path):
    lock = tmp_path / ".run_daily.lock"
    lock.write_text("not json", encoding="utf-8")

    acquire_run_lock(lock, git_commit="x")

    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_release_removes_only_its_own_lock(tmp_path: Path):
    lock = tmp_path / ".run_daily.lock"
    acquire_run_lock(lock, git_commit="x")
    release_run_lock(lock)
    assert not lock.exists()

    lock.write_text(json.dumps({"pid": _dead_pid()}), encoding="utf-8")
    release_run_lock(lock)
    assert lock.exists()
    release_run_lock(tmp_path / "absent.lock")


def test_pid_probe_basics():
    assert pid_is_running(os.getpid())
    assert not pid_is_running(_dead_pid())
    assert not pid_is_running(0)
    assert not pid_is_running(True)


@pytest.mark.skipif(os.name != "nt", reason="process creation time is read through the Windows API")
def test_a_live_pid_created_after_the_lock_is_a_reused_pid(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    # This test process started long after 2000-01-01, so the lock cannot be its own.
    lock = tmp_path / ".run_daily.lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "started_at": "2000-01-01T00:00:00+00:00", "git_commit": "old"}), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        acquire_run_lock(lock, git_commit="new")

    assert "now names a process started after the lock" in caplog.text
    assert json.loads(lock.read_text(encoding="utf-8"))["git_commit"] == "new"


@pytest.mark.skipif(os.name != "nt", reason="process creation time is read through the Windows API")
def test_a_live_pid_that_predates_the_lock_still_blocks(tmp_path: Path):
    lock = tmp_path / ".run_daily.lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "started_at": "2999-01-01T00:00:00+00:00", "git_commit": "old"}), encoding="utf-8")

    with pytest.raises(RunAlreadyActiveError):
        acquire_run_lock(lock, git_commit="new")
