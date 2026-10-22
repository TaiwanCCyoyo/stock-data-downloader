"""Exercise Windows launcher chains with an offline uv replacement."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher integration tests")

LAUNCHERS = (
    ("run_daily_scheduled", "run scripts/run_daily.py"),
    ("backfill_official_history_scheduled", "run scripts/backfill_official_history.py --max-seconds 82800"),
)


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
            f"@echo off\n> invocation.txt echo %CD%\n>> invocation.txt echo %*\nexit /b {exit_code}\n",
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
    # The wrapper trace brackets the run so a killed or stalled launch is visible in logs/.
    trace = (repository / "logs" / f"wrapper_{launcher.removesuffix('_scheduled')}.log").read_text(encoding="mbcs")
    assert "wrapper start" in trace
    assert f"wrapper end exit_code={exit_code}" in trace


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
