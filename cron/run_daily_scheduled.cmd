@echo off
REM Wrapper for Windows Task Scheduler: runs the daily update pipeline
REM (Shioaji download, corporate actions, official daily fetch, health check)
REM from the parent repository directory regardless of the task's working directory.
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
REM Wrapper trace: the Python log only starts after `uv run` has synced and imported, so
REM a stall before that (2026-10-22: a ~2h silent start) leaves no other record. These
REM lines go to logs\wrapper_run_daily_<YYYYMMDD>.log; uv's own stderr (sync progress,
REM import-time tracebacks) goes to logs\wrapper_run_daily_<YYYYMMDD>.err.log. A start line with no matching
REM end line means the process was killed (e.g. by the Task Scheduler time limit).
if not exist logs mkdir logs
REM %date% is locale-dependent, so the day stamp comes from PowerShell; if that fails the
REM files fall back to an undated name rather than losing the trace.
set DAYSTAMP=undated
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set DAYSTAMP=%%d
echo %date% %time% wrapper start cmd=run_daily >> "logs\wrapper_run_daily_%DAYSTAMP%.log"
call uv run scripts/run_daily.py 2>> "logs\wrapper_run_daily_%DAYSTAMP%.err.log"
set EXITCODE=%ERRORLEVEL%
echo %date% %time% wrapper end exit_code=%EXITCODE% >> "logs\wrapper_run_daily_%DAYSTAMP%.log"
exit /b %EXITCODE%
