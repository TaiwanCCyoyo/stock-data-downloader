"""Advisory process markers shared by the producer's long-running entry points.

``pid_is_running`` was moved here unchanged from ``backfill_official_history``.
``.run_daily.lock`` is the daily run's counterpart of the backfill's
``.official_backfill.lock``: a JSON file in the data directory naming the process
that is writing (empty when no run holds it). Consumers may treat either file, with a
live ``pid``, as a "producer is writing" signal; see docs/operations.md.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

RUN_DAILY_LOCK_NAME = ".run_daily.lock"

# The OS lock covers one byte far past the JSON record, so readers of the record are never
# blocked by it; the OS drops it when the holding process exits, however it exits.
_LOCK_OFFSET = 1 << 20
# A record's started_at is stamped just after its process starts; a live pid whose process was
# created later than this after the stamp is a reused pid, not the recorded run.
_START_SLACK = timedelta(minutes=1)
# Job handles that bind child processes to this process; never closed (see bind_children_to_this_process).
_JOB_HANDLES: list[int] = []


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
        times = [wintypes.FILETIME() for _ in range(4)]
        if not get_process_times(handle, *(ctypes.byref(time) for time in times)):
            return None
        ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime  # 100ns since 1601-01-01 UTC
        return datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=ticks // 10)
    finally:
        close_handle(handle)


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


def _read_record_fd(fd: int) -> dict[str, object]:
    """Read the JSON record through the lock's own descriptor.

    Never open a second descriptor on the lock file while holding it: POSIX record locks
    belong to the process, and closing *any* descriptor of the file releases them.
    """
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        record = json.loads(os.read(fd, 65536).decode("utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def _record_names_live_writer(record: dict[str, object]) -> bool:
    """Whether a record found under a free OS lock still names its live writer.

    Only a writer that does not take the OS lock leaves such a record while alive: a run_daily
    from before the lock existed. A live pid whose process predates the record's
    ``started_at`` is that writer; one created after it is a reused pid. When the creation
    time cannot be read, a live pid counts as a writer: a refused run is recoverable, two
    writers are not.
    """
    pid = record.get("pid")
    if not isinstance(pid, int) or not pid_is_running(pid):
        return False
    started = record.get("started_at")
    try:
        stamped = datetime.fromisoformat(started) if isinstance(started, str) else None
    except ValueError:
        stamped = None
    created = process_started_at(pid)
    if stamped is None or created is None:
        return True
    return created <= stamped + _START_SLACK


def acquire_run_lock(lock_path: Path, *, git_commit: str, label: str = "run_daily") -> RunLock:
    """Take the exclusive OS lock on ``lock_path`` and record ``{pid, started_at, git_commit}``.

    Two runs starting together cannot both pass: the OS lock is atomic, and a crashed or
    killed holder releases it with its process. A held lock raises ``RunAlreadyActiveError``.
    A record left under a free lock is either a run that died (logged at WARNING and
    replaced) or a live writer that predates the OS lock (refused, see
    ``_record_names_live_writer``).
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT)
    try:
        if not _try_os_lock(fd):
            holder = _read_record_fd(fd).get("pid")
            raise RunAlreadyActiveError(f"another {label} is running (pid={holder}, lock={lock_path})")
        previous = _read_record_fd(fd)
        if previous and _record_names_live_writer(previous):
            raise RunAlreadyActiveError(f"another {label} that does not take the OS lock is still running (pid={previous.get('pid')}, lock={lock_path})")
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
    """Clear this run's record while still holding the lock, then drop the lock by closing.

    The file is kept, empty, rather than deleted: deleting it after the close could remove the
    file a new run has just locked.
    """
    try:
        os.lseek(lock.fd, 0, os.SEEK_SET)
        os.ftruncate(lock.fd, 0)
    except OSError as error:
        logger.warning("could not clear run lock record %s: %s", lock.path, error)
    finally:
        os.close(lock.fd)


def bind_children_to_this_process() -> bool:
    """On Windows, make every child process started from now on end when this process ends.

    The run lock is released with this process; without this a pool worker or other child
    could keep writing after a new run has taken the lock. Puts this process in a job object
    with KILL_ON_JOB_CLOSE whose only handle is never closed, so the OS closes it, killing
    the job's remaining processes, when this process exits however it exits. Returns False
    (and logs) where unsupported; POSIX has no equivalent here, so there the lock covers this
    process only.
    """
    if os.name != "nt":
        logger.info("child processes are not bound to this run on %s; the run lock covers this process only", os.name)
        return False
    import ctypes
    from ctypes import wintypes

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("Reads", "Writes", "Others", "ReadBytes", "WriteBytes", "OtherBytes")]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job_object_limit_kill_on_job_close = 0x2000
    job_object_extended_limit_information = 9

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        logger.warning("child processes not bound to this run: CreateJobObject failed (error %s)", ctypes.get_last_error())
        return False
    limits = _ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = job_object_limit_kill_on_job_close
    if not kernel32.SetInformationJobObject(job, job_object_extended_limit_information, ctypes.byref(limits), ctypes.sizeof(limits)):
        logger.warning("child processes not bound to this run: SetInformationJobObject failed (error %s)", ctypes.get_last_error())
        return False
    if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
        logger.warning("child processes not bound to this run: AssignProcessToJobObject failed (error %s)", ctypes.get_last_error())
        return False
    _JOB_HANDLES.append(job)
    return True
