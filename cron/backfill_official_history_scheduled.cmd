@echo off
REM Wrapper for Windows Task Scheduler: runs one bounded slice of the official
REM daily-price historical backfill. Triggered once a day at 18:25 (see
REM setup_scheduled_tasks.ps1); --max-seconds 82800 (23h) caps the run so it
REM normally finishes before the following daily update. Delayed runs can overlap.
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
REM Wrapper trace: the Python log only starts after `uv run` has synced and imported, so
REM a stall before that (2026-10-22: a ~2h silent start) leaves no other record. These
REM lines go to logs\wrapper_backfill_official_history.log; uv's own stderr (sync progress, import-time
REM tracebacks) goes to logs\wrapper_backfill_official_history.err.log. A start line with no matching
REM end line means the process was killed (e.g. by the Task Scheduler time limit).
if not exist logs mkdir logs
echo %date% %time% wrapper start cmd=backfill_official_history >> "logs\wrapper_backfill_official_history.log"
call uv run scripts/backfill_official_history.py --max-seconds 82800 2>> "logs\wrapper_backfill_official_history.err.log"
set EXITCODE=%ERRORLEVEL%
echo %date% %time% wrapper end exit_code=%EXITCODE% >> "logs\wrapper_backfill_official_history.log"
exit /b %EXITCODE%
