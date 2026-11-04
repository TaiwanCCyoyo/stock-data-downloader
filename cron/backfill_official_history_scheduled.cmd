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
REM limit counts from here, not from when Python starts. It also reads when Task Scheduler started
REM this task and how long ago, so a late wrapper start (2026-10-27: 18:12 for an 18:00 trigger) is
REM on record; both fall back to ? if the task cannot be read.
set TASK_STARTED=?
set TASK_START_LAG=?
for /f "tokens=1-4 delims=," %%a in ('powershell -NoProfile -Command "$t=[char]63;$l=[char]63;try{$r=(Get-ScheduledTaskInfo -TaskName StockProject-OfficialBackfill -ErrorAction Stop).LastRunTime;$t=Get-Date $r -Format yyyy-MM-ddTHH:mm:ss;$l=[int]((Get-Date)-$r).TotalSeconds}catch{};(Get-Date -Format yyyyMMdd)+[char]44+[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()+[char]44+$t+[char]44+$l"') do (
    set DAYSTAMP=%%a
    set STOCK_PROJECT_LAUNCH_EPOCH=%%b
    set TASK_STARTED=%%c
    set TASK_START_LAG=%%d
)
REM Task Scheduler's ExecutionTimeLimit for this task (23h30m; keep in step with setup_scheduled_tasks.ps1).
REM The limit ends only wscript.exe, so this chain can outlive it: Python logs an ERROR once past
REM this limit and the end line below records elapsed_s / over_limit.
set STOCK_PROJECT_TASK_LIMIT_SECONDS=84600
echo %date% %time% wrapper start cmd=backfill_official_history task_started=%TASK_STARTED% task_start_lag_s=%TASK_START_LAG% >> "logs\wrapper_backfill_official_history_%DAYSTAMP%.log"
call uv run scripts/backfill_official_history.py --max-seconds 82800 2>> "logs\wrapper_backfill_official_history_%DAYSTAMP%.err.log"
set EXITCODE=%ERRORLEVEL%
set ELAPSED=unknown
set OVERLIMIT=0
if defined STOCK_PROJECT_LAUNCH_EPOCH for /f %%a in ('powershell -NoProfile -Command "[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()"') do set /a ELAPSED=%%a-%STOCK_PROJECT_LAUNCH_EPOCH%
if not "%ELAPSED%"=="unknown" if %ELAPSED% GTR %STOCK_PROJECT_TASK_LIMIT_SECONDS% set OVERLIMIT=1
echo %date% %time% wrapper end exit_code=%EXITCODE% elapsed_s=%ELAPSED% over_limit=%OVERLIMIT% >> "logs\wrapper_backfill_official_history_%DAYSTAMP%.log"
exit /b %EXITCODE%
