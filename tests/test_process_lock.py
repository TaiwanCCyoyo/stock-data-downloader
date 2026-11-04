"""Run-lock helpers shared by run_daily (``.run_daily.lock``) and the backfill."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from shp_utils import process_lock
from shp_utils.process_lock import RunAlreadyActiveError, acquire_run_lock, pid_is_running, release_run_lock

REPO_ROOT = Path(__file__).resolve().parents[1]

# Runs in a second process: try to take the lock at argv[1]; exit 3 when refused.
SECOND_ACQUIRE = """
import sys
from pathlib import Path
from shp_utils.process_lock import RunAlreadyActiveError, acquire_run_lock
try:
    acquire_run_lock(Path(sys.argv[1]), git_commit="second")
except RunAlreadyActiveError as error:
    print(error)
    sys.exit(3)
"""

# Runs in a second process: bind children, start a sleeping child, record its pid, then exit.
BOUND_PARENT = """
import os, subprocess, sys
from pathlib import Path
from shp_utils.process_lock import bind_children_to_this_process
assert bind_children_to_this_process()
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
Path(sys.argv[1]).write_text(str(child.pid))
os._exit(0)
"""


def _dead_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def _second_acquire(lock: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-c", SECOND_ACQUIRE, str(lock)], cwd=REPO_ROOT, capture_output=True, text=True, check=False)


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
        result = _second_acquire(lock)
        assert result.returncode == 3, result.stderr
        assert f"pid={os.getpid()}" in result.stdout
        assert json.loads(lock.read_text(encoding="utf-8"))["git_commit"] == "first"
    finally:
        release_run_lock(held)


def test_a_record_left_by_a_dead_run_is_replaced_with_a_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    lock = tmp_path / ".run_daily.lock"
    dead = _dead_pid()
    lock.write_text(json.dumps({"pid": dead}), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        held = acquire_run_lock(lock, git_commit="new")
    try:
        assert f"previous run_daily did not finish cleanly (stale lock pid={dead}" in caplog.text
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


def test_release_clears_the_record_keeps_the_file_and_allows_the_next_run(tmp_path: Path):
    lock = tmp_path / ".run_daily.lock"
    release_run_lock(acquire_run_lock(lock, git_commit="x"))
    assert lock.read_text(encoding="utf-8") == ""

    release_run_lock(acquire_run_lock(lock, git_commit="y"))
    assert lock.read_text(encoding="utf-8") == ""


def test_a_live_writer_without_the_os_lock_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # A run_daily from before the OS lock leaves only its JSON record while alive.
    lock = tmp_path / ".run_daily.lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "started_at": "2026-10-30T10:00:00+00:00"}), encoding="utf-8")
    monkeypatch.setattr(process_lock, "process_started_at", lambda pid: datetime(2026, 10, 30, 9, 59, 50, tzinfo=UTC))

    with pytest.raises(RunAlreadyActiveError, match="does not take the OS lock"):
        acquire_run_lock(lock, git_commit="new")
    assert json.loads(lock.read_text(encoding="utf-8"))["started_at"] == "2026-10-30T10:00:00+00:00"


def test_a_live_pid_created_after_the_record_is_a_reused_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    lock = tmp_path / ".run_daily.lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "started_at": "2026-10-30T10:00:00+00:00"}), encoding="utf-8")
    monkeypatch.setattr(process_lock, "process_started_at", lambda pid: datetime(2026, 10, 31, 10, 0, tzinfo=UTC))

    with caplog.at_level(logging.WARNING):
        release_run_lock(acquire_run_lock(lock, git_commit="new"))
    assert "did not finish cleanly" in caplog.text


@pytest.mark.skipif(os.name == "nt", reason="POSIX record-lock semantics")
def test_posix_lock_survives_the_holder_reading_its_record(tmp_path: Path):
    # Closing any descriptor of the file drops a POSIX record lock; acquire must not do that.
    lock = tmp_path / ".run_daily.lock"
    held = acquire_run_lock(lock, git_commit="first")
    try:
        result = _second_acquire(lock)
        assert result.returncode == 3, result.stderr
    finally:
        release_run_lock(held)


@pytest.mark.skipif(os.name != "nt", reason="job objects are Windows-only")
def test_children_end_when_the_bound_parent_ends(tmp_path: Path):
    marker = tmp_path / "child.pid"
    subprocess.run([sys.executable, "-c", BOUND_PARENT, str(marker)], cwd=REPO_ROOT, check=True, timeout=60)
    child = int(marker.read_text())
    deadline = time.monotonic() + 10
    while pid_is_running(child) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not pid_is_running(child), "the child must end with its bound parent"


def test_pid_probe_basics():
    assert pid_is_running(os.getpid())
    assert not pid_is_running(_dead_pid())
    assert not pid_is_running(0)
    assert not pid_is_running(True)
