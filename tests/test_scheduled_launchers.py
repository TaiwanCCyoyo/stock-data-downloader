"""Exercise Windows launcher chains with an offline uv replacement."""

import datetime
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher integration tests")

LAUNCHERS = (
    ("run_daily_scheduled", "run scripts/run_daily.py"),
    ("backfill_official_history_scheduled", "run scripts/backfill_official_history.py --max-seconds 82800"),
)
# Task Scheduler ExecutionTimeLimit per launcher (setup_scheduled_tasks.ps1), exported for Python's orphan check.
TASK_LIMIT_SECONDS = {"run_daily_scheduled": 3 * 3600, "backfill_official_history_scheduled": 23 * 3600 + 30 * 60}


@pytest.mark.parametrize(("launcher", "expected_arguments"), LAUNCHERS)
@pytest.mark.parametrize("suffix", ["cmd", "vbs"])
@pytest.mark.parametrize("exit_code", [0, 23])
def test_launchers_use_repository_root_and_propagate_exit_code(tmp_path: Path, launcher: str, expected_arguments: str, suffix: str, exit_code: int) -> None:
    source = Path(__file__).resolve().parents[1] / "cron"
    repository = tmp_path / "mock repository with spaces"
    cron = repository / "cron"
    cron.mkdir(parents=True)
    unrelated = tmp_path / "unrelated working directory"
    unrelated.mkdir()

    # Each reachable working directory has a fake uv: a broken cd must never
    # fall through to the real downloader installed on the machine's PATH.
    for directory in (repository, cron, unrelated):
        (directory / "uv.cmd").write_text(
            (
                f"@echo off\n> invocation.txt echo %CD%\n>> invocation.txt echo %*\n"
                f">> invocation.txt echo %STOCK_PROJECT_LAUNCH_EPOCH%\n>> invocation.txt echo %STOCK_PROJECT_TASK_LIMIT_SECONDS%\n"
                f"exit /b {exit_code}\n"
            ),
            encoding="ascii",
        )
    for extension in ("cmd", "vbs"):
        shutil.copyfile(source / f"{launcher}.{extension}", cron / f"{launcher}.{extension}")

    entry = cron / f"{launcher}.{suffix}"
    if suffix == "cmd":
        command = ["cmd.exe", "/d", "/c", "call", str(entry)]
    else:
        command = ["cscript.exe", "//nologo", "//B", str(entry)]
    result = subprocess.run(command, cwd=unrelated, capture_output=True, timeout=20)

    assert result.returncode == exit_code, (result.stdout, result.stderr)
    assert not (unrelated / "invocation.txt").exists()
    assert not (cron / "invocation.txt").exists()
    lines = (repository / "invocation.txt").read_text(encoding="mbcs").splitlines()
    assert Path(lines[0]) == repository
    assert lines[1] == expected_arguments
    # Python's heartbeat measures the scheduler limit from this launch time, not from its own start.
    assert abs(int(lines[2]) - time.time()) < 120
    assert int(lines[3]) == TASK_LIMIT_SECONDS[launcher]
    # The wrapper trace brackets the run so a killed or stalled launch is visible in logs/.
    day = datetime.date.today().strftime("%Y%m%d")
    trace = (repository / "logs" / f"wrapper_{launcher.removesuffix('_scheduled')}_{day}.log").read_text(encoding="mbcs")
    assert "wrapper start" in trace
    # The end line records the run time and whether it outlived the scheduler limit.
    assert re.search(rf"wrapper end exit_code={exit_code} elapsed_s=\d+ over_limit=0", trace)


@pytest.mark.parametrize("missing_task", [False, True])
def test_action_migration_only_updates_existing_actions(tmp_path: Path, missing_task: bool) -> None:
    cron = tmp_path / "production checkout" / "cron"
    cron.mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "cron/setup_scheduled_tasks.ps1", cron / "setup_scheduled_tasks.ps1")
    harness = tmp_path / "verify.ps1"
    harness.write_text(
        r"""
param([string]$Installer, [string]$Missing)
$ErrorActionPreference = "Stop"
$changes = [System.Collections.Generic.List[object]]::new()
function New-ScheduledTaskAction {
    param($Execute, $Argument, $WorkingDirectory)
    [pscustomobject]@{ Execute=$Execute; Arguments=$Argument; WorkingDirectory=$WorkingDirectory }
}
function Get-ScheduledTask {
    param($TaskName, $TaskPath)
    if ($Missing -eq "True" -and $TaskName -eq "StockProject-OfficialBackfill") { throw "Missing task" }
    [pscustomobject]@{TaskName=$TaskName;TaskPath=$TaskPath}
}
function Set-ScheduledTask {
    [CmdletBinding()]
    param($TaskName, $TaskPath, $Action)
    $changes.Add([pscustomobject]@{Name=$TaskName;Path=$TaskPath;Action=$Action})
}
function Register-ScheduledTask { throw "Must not register tasks during migration" }
function Start-ScheduledTask { throw "Must not start tasks" }
function Enable-ScheduledTask { throw "Must not enable tasks" }
function Disable-ScheduledTask { throw "Must not change enabled state" }
function New-ScheduledTaskTrigger { throw "Must not replace triggers" }
function New-ScheduledTaskSettingsSet { throw "Must not replace settings" }
$failed = $false
try { & $Installer -UpdateActionsOnly } catch { $failed = $true; $failureMessage = $_.Exception.Message }
[pscustomobject]@{Failed=$failed;Error=$failureMessage;Changes=$changes.ToArray()} | ConvertTo-Json -Depth 5 -Compress
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-File", str(harness), "-Installer", str(cron / "setup_scheduled_tasks.ps1"), "-Missing", str(missing_task)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout.splitlines()[-1])
    assert record["Failed"] is missing_task, record
    changes = record["Changes"]
    if missing_task:
        assert changes == []
    else:
        assert [change["Name"] for change in changes] == ["StockProject-DailyUpdate", "StockProject-OfficialBackfill"]
        for change, (launcher, _) in zip(changes, LAUNCHERS, strict=True):
            assert change["Path"] == "\\"
            assert change["Action"] == {
                "Execute": "wscript.exe",
                "Arguments": f'"{cron / (launcher + ".vbs")}"',
                "WorkingDirectory": str(cron.parent),
            }
