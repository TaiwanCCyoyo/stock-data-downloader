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
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

RUN_DAILY_LOCK_NAME = ".run_daily.lock"


class RunAlreadyActiveError(RuntimeError):
    """Raised when another process holds the run lock."""


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


# The OS lock covers one byte far past the JSON record, so readers of the record are never
# blocked by it; the OS drops it when the holding process exits, however it exits.
_LOCK_OFFSET = 1 << 20


class RunLock:
    """An acquired run lock; keep it until the run ends, then pass it to ``release_run_lock``."""

    def __init__(self, path: Path, fd: int) -> None:
        self.path = path
        self.fd = fd


def _try_os_lock(fd: int) -> bool:
    os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, _LOCK_OFFSET)
    except OSError:
        return False
    return True


def _read_record(path: Path) -> dict[str, object]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def acquire_run_lock(lock_path: Path, *, git_commit: str, label: str = "run_daily") -> RunLock:
    """Take the exclusive OS lock on ``lock_path`` and record ``{pid, started_at, git_commit}``.

    Two runs starting together cannot both pass: the OS lock is atomic, and a crashed or
    killed holder releases it with its process, so a pid left in the file never blocks a
    later run (even when Windows has reused that pid). A held lock raises
    ``RunAlreadyActiveError``; a record left by a run that did not finish is logged at WARNING.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT)
    try:
        if not _try_os_lock(fd):
            holder = _read_record(lock_path).get("pid")
            raise RunAlreadyActiveError(f"another {label} is running (pid={holder}, lock={lock_path})")
        previous = _read_record(lock_path)
        if previous:
            logger.warning("previous %s did not finish cleanly (stale lock pid=%s, lock=%s)", label, previous.get("pid"), lock_path)
        record = json.dumps({"pid": os.getpid(), "started_at": datetime.now(UTC).isoformat(timespec="seconds"), "git_commit": git_commit}).encode("utf-8")
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, record)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        raise
    return RunLock(lock_path, fd)


def release_run_lock(lock: RunLock) -> None:
    """Remove this run's record, then drop the OS lock by closing the file."""
    try:
        os.lseek(lock.fd, 0, os.SEEK_SET)
        os.ftruncate(lock.fd, 0)
    except OSError as error:
        logger.warning("could not clear run lock record %s: %s", lock.path, error)
    finally:
        os.close(lock.fd)
    try:
        if not _read_record(lock.path):
            lock.path.unlink(missing_ok=True)
    except OSError:
        # Another run took the lock between close and unlink; its file stays.
        pass
