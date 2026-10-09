import datetime
import logging
from pathlib import Path

import pytest

from shp_utils.logging_utils import dated_log_path, setup_logging


@pytest.fixture(autouse=True)
def _cleanup_root_logger_handlers():
    yield
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if getattr(handler, "_stock_project_handler", False):
            root_logger.removeHandler(handler)
            handler.close()


def test_dated_log_path_inserts_date_before_extension_under_logs_dir():
    result = dated_log_path("run_daily.log", today=datetime.date(2026, 8, 7))

    assert result == Path("logs") / "run_daily_20260807.log"


def test_dated_log_path_preserves_extension():
    result = dated_log_path("backfill_official_history.log", today=datetime.date(2026, 1, 5))

    assert result == Path("logs") / "backfill_official_history_20260105.log"


def test_setup_logging_writes_into_logs_directory_with_dated_filename(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)

    setup_logging("run_daily.log")
    logging.getLogger("test").info("hello")

    expected = tmp_path / "logs" / f"run_daily_{datetime.date.today():%Y%m%d}.log"
    assert expected.exists()
    assert "hello" in expected.read_text(encoding="utf-8-sig")


def test_setup_logging_appends_to_same_dated_file_across_calls_same_day(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)

    setup_logging("run_daily.log")
    logging.getLogger("test").info("first run")
    setup_logging("run_daily.log")
    logging.getLogger("test").info("second run")

    expected = tmp_path / "logs" / f"run_daily_{datetime.date.today():%Y%m%d}.log"
    content = expected.read_text(encoding="utf-8-sig")
    assert "first run" in content
    assert "second run" in content
