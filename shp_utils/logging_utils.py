import datetime
import logging
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
