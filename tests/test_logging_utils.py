import datetime
import logging
import os
import time
from pathlib import Path
from typing import Any

import pytest

from shp_utils.logging_utils import (
    LAUNCH_EPOCH_ENV,
    TASK_LIMIT_ENV,
    StepClock,
    dated_log_path,
    launch_epoch_from_env,
    log_startup,
    setup_logging,
    start_heartbeat,
    task_limit_from_env,
)


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


def _first_heartbeat(logger_name: str, caplog: pytest.LogCaptureFixture, **kwargs: Any) -> logging.LogRecord:
    logger = logging.getLogger(logger_name)
    with caplog.at_level(logging.INFO, logger=logger_name):
        stop = start_heartbeat(logger, **{"interval_seconds": 0.01, **kwargs})
        deadline = time.monotonic() + 5
        while not caplog.records and time.monotonic() < deadline:
            time.sleep(0.01)
        stop.set()
    assert caplog.records
    return caplog.records[0]


def test_heartbeat_warns_from_launch_time_when_python_started_late(caplog: pytest.LogCaptureFixture):
    # Python has run for milliseconds, but the launcher began 2h50m ago (a slow `uv run` start).
    record = _first_heartbeat("late_start", caplog, warn_after_seconds=2.75 * 3600, launch_epoch=time.time() - 2.83 * 3600)

    assert record.levelno == logging.WARNING
    assert "since_launch=" in record.getMessage()


def test_heartbeat_stays_info_just_before_the_launch_based_threshold(caplog: pytest.LogCaptureFixture):
    record = _first_heartbeat("before_threshold", caplog, warn_after_seconds=2.75 * 3600, launch_epoch=time.time() - 2.7 * 3600)

    assert record.levelno == logging.INFO
    assert "since_launch=" in record.getMessage()


def test_heartbeat_without_launch_time_counts_from_its_own_start(caplog: pytest.LogCaptureFixture):
    record = _first_heartbeat("no_launch", caplog, warn_after_seconds=3600.0)

    assert record.levelno == logging.INFO
    assert "since_launch" not in record.getMessage()


@pytest.mark.parametrize("raw", ["", "abc", "0", "-5", "99999999999"])
def test_launch_epoch_from_env_ignores_missing_or_implausible_values(monkeypatch: pytest.MonkeyPatch, raw: str):
    monkeypatch.setenv(LAUNCH_EPOCH_ENV, raw)

    assert launch_epoch_from_env() is None


def test_launch_epoch_from_env_reads_a_past_unix_time(monkeypatch: pytest.MonkeyPatch):
    past = int(time.time()) - 60
    monkeypatch.setenv(LAUNCH_EPOCH_ENV, str(past))

    assert launch_epoch_from_env() == past


def test_heartbeat_warns_immediately_when_already_past_the_threshold_at_start(caplog: pytest.LogCaptureFixture):
    # The interval is an hour: the first line must not wait for it.
    record = _first_heartbeat("past_at_start", caplog, warn_after_seconds=2.5 * 3600, launch_epoch=time.time() - 2.9 * 3600, interval_seconds=3600.0)

    assert record.levelno == logging.WARNING


def test_heartbeat_reports_an_orphan_once_past_the_task_limit(caplog: pytest.LogCaptureFixture):
    logger = logging.getLogger("orphan_test")
    with caplog.at_level(logging.INFO, logger="orphan_test"):
        stop = start_heartbeat(
            logger, interval_seconds=0.01, launch_epoch=time.time() - 3 * 3600 - 60, task_limit_seconds=3 * 3600, current_step=lambda: "步驟 10"
        )
        deadline = time.monotonic() + 5
        while sum(r.levelno == logging.INFO for r in caplog.records) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        stop.set()

    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert message.startswith("task time limit passed: since_launch=10860s limit=10800s; Task Scheduler has likely ended the task")
    assert "(orphan)" in message
    assert "current_step=步驟 10" in message


def test_heartbeat_does_not_report_before_the_task_limit(caplog: pytest.LogCaptureFixture):
    record = _first_heartbeat("under_limit", caplog, launch_epoch=time.time() - 3500, task_limit_seconds=3600.0)

    assert record.levelno == logging.INFO
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]


def test_heartbeat_ignores_the_task_limit_without_a_launch_time(caplog: pytest.LogCaptureFixture):
    _first_heartbeat("limit_no_launch", caplog, task_limit_seconds=0.0001)

    assert not [r for r in caplog.records if r.levelno == logging.ERROR]


@pytest.mark.parametrize(("raw", "expected"), [("10800", 10800.0), ("", None), ("abc", None), ("0", None), ("-5", None)])
def test_task_limit_from_env(monkeypatch: pytest.MonkeyPatch, raw: str, expected: float | None):
    monkeypatch.setenv(TASK_LIMIT_ENV, raw)

    assert task_limit_from_env() == expected


def test_step_clock_exposes_the_step_in_progress():
    clock = StepClock(logging.getLogger("step_current"))

    assert clock.current is None
    clock.begin("步驟 4")
    assert clock.current == "步驟 4"
    clock.finish()
    assert clock.current is None
