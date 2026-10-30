"""Advisory process markers shared by the producer's long-running entry points.

``pid_is_running`` was moved here unchanged from ``backfill_official_history``.
``.run_daily.lock`` is the daily run's counterpart of the backfill's
``.official_backfill.lock``: a JSON file in the data directory naming the process
that is (or was) writing. Consumers may treat either file, with a live ``pid``, as a
"producer is writing" signal; see docs/operations.md.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

RUN_DAILY_LOCK_NAME = ".run_daily.lock"


class RunAlreadyActiveError(RuntimeError):
    """Raised when the pid recorded in a run lock still names a live process."""


def pid_is_running(pid: int) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_is_running(pid)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_pid_is_running(pid: int) -> bool:
    """Query a Windows process without sending it a console control event."""
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    error_access_denied = 5
    still_active = 259

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    get_exit_code_process = kernel32.GetExitCodeProcess
    get_exit_code_process.argtypes = [wintypes.HANDLE, wintypes.LPDWORD]
    get_exit_code_process.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = open_process(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied

    try:
        exit_code = wintypes.DWORD()
        if not get_exit_code_process(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        close_handle(handle)


def process_started_at(pid: int) -> datetime | None:
    """Creation time (UTC) of a live process, or None when it cannot be read (or off Windows)."""
    if os.name != "nt" or not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    get_process_times = kernel32.GetProcessTimes
    get_process_times.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    get_process_times.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not get_process_times(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime  # 100ns since 1601-01-01 UTC
        return datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=ticks // 10)
    finally:
        close_handle(handle)


def _pid_reused(pid: int, recorded_started_at: object) -> bool:
    """True when the live ``pid`` was created after the lock was written, so it is not the locking run.

    Windows reuses pids quickly; without this a lock left by a crash could name an unrelated
    process the next day and keep every later run from starting.
    """
    if not isinstance(recorded_started_at, str):
        return False
    try:
        recorded = datetime.fromisoformat(recorded_started_at)
    except ValueError:
        return False
    created = process_started_at(pid)
    # Lock stamps have one-second resolution and are written just after start; allow slack.
    return created is not None and created > recorded + timedelta(minutes=1)


def acquire_run_lock(lock_path: Path, *, git_commit: str, label: str = "run_daily") -> None:
    """Write ``{pid, started_at, git_commit}`` to ``lock_path``.

    A lock whose pid is alive raises ``RunAlreadyActiveError`` and is left untouched. A lock
    whose pid is gone is logged at WARNING (the previous run died or was orphaned past its
    end) and replaced.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.is_file():
        existing_pid = None
        existing_started_at = None
        try:
            existing = json.loads(lock_path.read_text(encoding="utf-8"))
            existing_pid, existing_started_at = existing.get("pid"), existing.get("started_at")
        except (json.JSONDecodeError, OSError, ValueError, AttributeError):
            pass
        if existing_pid is not None and pid_is_running(existing_pid) and _pid_reused(existing_pid, existing_started_at):
            logger.warning("%s lock pid=%s now names a process started after the lock (%s); treating it as stale", label, existing_pid, existing_started_at)
        elif existing_pid is not None and pid_is_running(existing_pid):
            raise RunAlreadyActiveError(
                f"another {label} appears to be running (pid={existing_pid}, lock={lock_path}); "
                f"if no such run is actually alive (e.g. the pid was reused by an unrelated process), delete this lock file and re-run"
            )
        logger.warning("previous %s did not finish cleanly (stale lock pid=%s, lock=%s)", label, existing_pid, lock_path)
    lock_path.write_text(
        json.dumps({"pid": os.getpid(), "started_at": datetime.now(UTC).isoformat(timespec="seconds"), "git_commit": git_commit}),
        encoding="utf-8",
    )


def release_run_lock(lock_path: Path) -> None:
    """Remove the lock only when it still names this process (never another run's)."""
    try:
        recorded = json.loads(lock_path.read_text(encoding="utf-8")).get("pid")
    except (json.JSONDecodeError, OSError, ValueError, AttributeError):
        recorded = None
    if recorded == os.getpid():
        lock_path.unlink(missing_ok=True)
