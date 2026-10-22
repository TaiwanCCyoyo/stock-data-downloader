import datetime
import logging
import os
import platform
import subprocess
import threading
import time
from pathlib import Path

from rich.logging import RichHandler

LOG_DIR = Path("logs")


def dated_log_path(log_file: str | Path, today: datetime.date | None = None) -> Path:
    """Resolve a bare log filename to a dated path under LOG_DIR.

    "run_daily.log" -> "logs/run_daily_20260807.log", so each day's run keeps
    its own file under one directory instead of appending forever to a single
    undated log.
    """
    stem_path = Path(log_file)
    day = today or datetime.date.today()
    dated_name = f"{stem_path.stem}_{day:%Y%m%d}{stem_path.suffix}"
    return LOG_DIR / dated_name


def detach_conflicting_file_handlers(log_path: Path) -> None:
    target = log_path.resolve()
    for logger_name in ["shioaji"]:
        logger = logging.getLogger(logger_name)
        for handler in list(logger.handlers):
            if not isinstance(handler, logging.FileHandler):
                continue
            if Path(handler.baseFilename).resolve() != target:
                continue
            logger.removeHandler(handler)
            handler.close()


def setup_logging(log_file: str | Path, level: int = logging.INFO) -> None:
    log_path = dated_log_path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    detach_conflicting_file_handlers(log_path)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    for handler in list(root_logger.handlers):
        if getattr(handler, "_stock_project_handler", False):
            root_logger.removeHandler(handler)
            handler.close()

    console_handler = RichHandler(rich_tracebacks=True, show_path=False)
    setattr(console_handler, "_stock_project_handler", True)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(console_handler)

    file_handler = logging.FileHandler(log_path, encoding="utf-8-sig")
    setattr(file_handler, "_stock_project_handler", True)
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    root_logger.addHandler(file_handler)


def get_logger(log_file: str | Path, name: str | None = None) -> logging.Logger:
    setup_logging(log_file)
    return logging.getLogger(name or Path(log_file).stem)


def get_named_logger(name: str) -> logging.Logger:
    setup_logging(f"{name}.log")
    return logging.getLogger(name)


HEARTBEAT_INTERVAL_SEC = 600.0


def log_startup(logger: logging.Logger, label: str) -> None:
    """Record who is running, as the first lines a run writes.

    The log only starts after imports finish, so a run that stalls before this
    point leaves no trace. The pid and commit let the log be matched to a
    process in Task Scheduler / Task Manager and to the code that produced it.
    """
    logger.info("startup: %s pid=%d python=%s platform=%s", label, os.getpid(), platform.python_version(), platform.platform())
    logger.info("startup: git_commit=%s cwd=%s", _git_commit(), Path.cwd())


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).resolve().parent,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def start_heartbeat(
    logger: logging.Logger,
    *,
    interval_seconds: float = HEARTBEAT_INTERVAL_SEC,
    warn_after_seconds: float | None = None,
) -> threading.Event:
    """Log a heartbeat line every ``interval_seconds`` until the returned event is set.

    A run can be silent for a long time without being broken (a slow query, a
    long download), and from the log alone that looks identical to a stalled
    process. The heartbeat separates the two: lines keep coming while the
    process is alive and scheduled, and stop when it is frozen or killed.
    ``warn_after_seconds`` raises each heartbeat to WARNING once the run is
    older than the scheduler's execution time limit minus some margin, so a run
    approaching a forced kill is visible before it happens.
    """
    stop = threading.Event()
    started = time.monotonic()

    def beat() -> None:
        while not stop.wait(interval_seconds):
            elapsed = time.monotonic() - started
            level = logging.WARNING if warn_after_seconds is not None and elapsed >= warn_after_seconds else logging.INFO
            logger.log(level, "heartbeat: pid=%d elapsed=%.0fs", os.getpid(), elapsed)

    threading.Thread(target=beat, name="heartbeat", daemon=True).start()
    return stop


class StepClock:
    """Logs how long each named pipeline step took, one line per step.

    ``begin`` closes the previous step and opens the next, so a step's duration
    is recorded no matter which branch (success, handled failure, skip) ended
    it. The line is additive: the existing step headers keep their wording.
    """

    def __init__(self, logger: logging.Logger):
        self._logger = logger
        self._name: str | None = None
        self._started = 0.0

    def begin(self, name: str) -> None:
        self.finish()
        self._name = name
        self._started = time.monotonic()

    def finish(self) -> None:
        if self._name is not None:
            self._logger.info("step_elapsed: %s = %.1fs", self._name, time.monotonic() - self._started)
            self._name = None
