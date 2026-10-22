import datetime
import logging
import os
import time
from pathlib import Path

import pytest

from shp_utils.logging_utils import StepClock, dated_log_path, log_startup, setup_logging, start_heartbeat


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


def test_step_clock_logs_each_step_duration_once(caplog: pytest.LogCaptureFixture):
    logger = logging.getLogger("step_clock_test")
    clock = StepClock(logger)

    with caplog.at_level(logging.INFO, logger="step_clock_test"):
        clock.begin("步驟 1")
        clock.begin("步驟 2")
        clock.finish()
        clock.finish()

    lines = [record.getMessage() for record in caplog.records]
    assert [line.split(" = ")[0] for line in lines] == ["step_elapsed: 步驟 1", "step_elapsed: 步驟 2"]


def test_heartbeat_logs_until_stopped_and_warns_past_threshold(caplog: pytest.LogCaptureFixture):
    logger = logging.getLogger("heartbeat_test")

    with caplog.at_level(logging.INFO, logger="heartbeat_test"):
        stop = start_heartbeat(logger, interval_seconds=0.01, warn_after_seconds=0.0)
        deadline = time.monotonic() + 5
        while len(caplog.records) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        stop.set()

    assert len(caplog.records) >= 2
    assert all("heartbeat: pid=" in record.getMessage() for record in caplog.records)
    assert all(record.levelno == logging.WARNING for record in caplog.records)


def test_log_startup_records_pid_and_commit(caplog: pytest.LogCaptureFixture):
    logger = logging.getLogger("startup_test")

    with caplog.at_level(logging.INFO, logger="startup_test"):
        log_startup(logger, "unit")

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert f"pid={os.getpid()}" in text
    assert "git_commit=" in text
