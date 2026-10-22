@echo off
REM Wrapper for Windows Task Scheduler: runs the daily update pipeline
REM (Shioaji download, corporate actions, official daily fetch, health check)
REM from the parent repository directory regardless of the task's working directory.
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
REM Wrapper trace: the Python log only starts after `uv run` has synced and imported, so
REM a stall before that (2026-10-22: a ~2h silent start) leaves no other record. These
REM lines go to logs\wrapper_run_daily.log; uv's own stderr (sync progress, import-time
REM tracebacks) goes to logs\wrapper_run_daily.err.log. A start line with no matching
REM end line means the process was killed (e.g. by the Task Scheduler time limit).
if not exist logs mkdir logs
echo %date% %time% wrapper start cmd=run_daily >> "logs\wrapper_run_daily.log"
call uv run scripts/run_daily.py 2>> "logs\wrapper_run_daily.err.log"
set EXITCODE=%ERRORLEVEL%
echo %date% %time% wrapper end exit_code=%EXITCODE% >> "logs\wrapper_run_daily.log"
exit /b %EXITCODE%
