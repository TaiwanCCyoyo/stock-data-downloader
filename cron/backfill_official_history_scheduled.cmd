@echo off
REM Wrapper for Windows Task Scheduler: runs one bounded slice of the official
REM daily-price historical backfill. Triggered once a day at 18:25 (see
REM setup_scheduled_tasks.ps1); --max-seconds 82800 (23h) caps the run so it
REM normally finishes before the following daily update. Delayed runs can overlap.
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
REM Wrapper trace: the Python log only starts after `uv run` has synced and imported, so
REM a stall before that (2026-10-22: a ~2h silent start) leaves no other record. These
REM lines go to logs\wrapper_backfill_official_history_<YYYYMMDD>.log; the launched process's stderr (uv sync progress,
REM import-time tracebacks) goes to logs\wrapper_backfill_official_history_<YYYYMMDD>.err.log. A start line with no matching
REM end line means the process was killed (e.g. by the Task Scheduler time limit).
if not exist logs mkdir logs
REM %date% is locale-dependent, so the day stamp comes from PowerShell; if that fails the
REM files fall back to an undated name rather than losing the trace.
set DAYSTAMP=undated
REM The same call yields the launch time (Unix seconds) for Python's heartbeat: the scheduler time
REM limit counts from here, not from when Python starts.
for /f "tokens=1,2 delims=," %%a in ('powershell -NoProfile -Command "(Get-Date -Format yyyyMMdd)+[char]44+[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()"') do (
    set DAYSTAMP=%%a
    set STOCK_PROJECT_LAUNCH_EPOCH=%%b
)
echo %date% %time% wrapper start cmd=backfill_official_history >> "logs\wrapper_backfill_official_history_%DAYSTAMP%.log"
call uv run scripts/backfill_official_history.py --max-seconds 82800 2>> "logs\wrapper_backfill_official_history_%DAYSTAMP%.err.log"
set EXITCODE=%ERRORLEVEL%
echo %date% %time% wrapper end exit_code=%EXITCODE% >> "logs\wrapper_backfill_official_history_%DAYSTAMP%.log"
exit /b %EXITCODE%
