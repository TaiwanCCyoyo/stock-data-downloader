@echo off
REM Wrapper for Windows Task Scheduler: runs the daily update pipeline
REM (Shioaji download, corporate actions, official daily fetch, health check)
REM from the parent repository directory regardless of the task's working directory.
cd /d "%~dp0.."
if errorlevel 1 exit /b 1
call uv run scripts/run_daily.py
exit /b %ERRORLEVEL%
