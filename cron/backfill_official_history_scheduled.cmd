@echo off
REM Wrapper for Windows Task Scheduler: runs one bounded slice of the official
REM daily-price historical backfill. Triggered once a day at 18:25 (see
REM setup_scheduled_tasks.ps1); --max-seconds 82800 (23h) caps the run so it
REM normally finishes before the following daily update. Delayed runs can overlap.
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
call uv run scripts/backfill_official_history.py --max-seconds 82800
exit /b %ERRORLEVEL%
