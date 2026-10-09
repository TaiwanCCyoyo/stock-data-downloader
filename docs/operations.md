# Operations and Data Safety

[Documentation index](README.md) · [Pipeline commands](pipeline.md)

## Production data and worktrees

The primary checkout's `data/` is the production source. All default paths resolve
through `config.config.DATA_DIR`, anchored to that checkout's repository root.
A worktree's `data/` is an independent physical snapshot. It may omit `raw/`,
minute CSVs and `official_daily.sqlite`; absence is not permission to delete,
download or reconstruct production data. Never make a data symlink or junction.

Preserve CSV, SQLite, Parquet, raw responses, snapshots, download progress,
checkpoints, manifests, source/observation times and failure evidence. Preserve
`.env`, certificates, local settings, `.tmp/` handoffs/research evidence, retired
worktree archives and existing backups. Git ignoring a file does not make it
disposable. Equal filenames do not establish equal contents.

Code and documentation cleanup does not authorize deleting, overwriting or moving
production data, changing schemas, rebuilding databases, resetting progress or
restoring an older snapshot over newer data. Describe the exact impact and obtain
owner approval before those operations. Do not use `git clean -fdx`,
`robocopy /MIR`, broad recursive deletion or stop processes owned by other sessions.

## Windows scheduling

The unattended pipeline uses Windows Task Scheduler, which can operate while agent
applications are closed. Keep production refreshes in the primary checkout.
Both registered actions use `wscript.exe` and a hidden-window launcher:

| Task                          | Trigger              | Launcher in `cron/`                                                                   | Python entry point                                         |
| ----------------------------- | -------------------- | ------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| StockProject-DailyUpdate      | Monday–Friday, 18:00 | `run_daily_scheduled.vbs` → `run_daily_scheduled.cmd`                                 | `scripts/run_daily.py`                                     |
| StockProject-OfficialBackfill | Daily, 18:25         | `backfill_official_history_scheduled.vbs` → `backfill_official_history_scheduled.cmd` | `scripts/backfill_official_history.py --max-seconds 82800` |

The four launchers and installer live in `cron/`. CMD files resolve the parent
repository root; VBS files invoke their adjacent CMD in a hidden window. Both
layers propagate exit codes. Old root launcher paths are removed by this change.

For a new installation, review [setup_scheduled_tasks.ps1](../cron/setup_scheduled_tasks.ps1)
and all four launchers before registering from the intended production checkout:

```powershell
.\cron\setup_scheduled_tasks.ps1
```

For existing tasks, first pause them and preserve their exported definitions. After
merging and pulling into the production checkout, update only the actions:

```powershell
.\cron\setup_scheduled_tasks.ps1 -UpdateActionsOnly
Get-ScheduledTask -TaskName StockProject-* | Select-Object TaskName, State, Actions
```

This mode preserves triggers, principal, settings and enabled state. Verify both
actions reference the production checkout's `cron/` launchers, then re-enable only
when ready to resume downloads. It does not start a test download. During the
2026-09-21 migration both tasks were disabled by owner request; updating actions
and re-enabling are deferred until the owner has merged and pulled.

Registration replaces the named tasks. Do not run it from a development worktree
or as a routine cleanup/verification step. Both tasks catch missed runs with
`StartWhenAvailable` and suppress a second instance of the same task with
`MultipleInstances IgnoreNew`. These settings do not prevent the two different
tasks from overlapping. The 18:25 trigger can overlap a daily run lasting beyond
25 minutes; do not infer a safe data-maintenance window from the nominal times.

Before any authorized data maintenance, inspect actual task actions/state and
running download/backfill processes. Pausing a trigger does not stop an already
running process. Do not terminate another session's process.

```powershell
Get-ScheduledTask -TaskName StockProject-* |
    Select-Object TaskName, State, Actions
Get-ScheduledTask -TaskName StockProject-* | Get-ScheduledTaskInfo
```

## Logs and progress

Logs are appended to `logs/<script>_<YYYYMMDD>.log`. Inspect the primary checkout's
logs for production status; missing worktree logs do not imply a failed schedule.
The broker SDK may also leave its own local log; preserving that file is separate
from arranging tracked source files.

The [daily-log command](../.claude/commands/check-daily-log.md) documents the
pipeline checks and `data/data_quality_report.json` interpretation.
[Data progress](data-progress.md) provides read-only SQLite queries and handles
older checkpoint column names without migrating a database.

## Backup behavior and limitations

The implementation is [scripts/backup_data.py](../scripts/backup_data.py); inspect
its current options and behavior before backup or restore work. Configure an
absolute destination outside this repository in the primary checkout's private
`.env`:

```dotenv
SHIOAJI_BACKUP_DIR=D:/StockDataBackup
```

The daily run backs up last unless a price-cache-writing step failed. An error in
another step still makes the daily run fail overall, but does not necessarily
block backup. Standalone commands, when backup work is authorized:

```powershell
uv run scripts/backup_data.py                 # additive copy and verification
uv run scripts/backup_data.py --verify-only   # compare the existing mirror
uv run scripts/backup_data.py --archive       # also write quarterly archives
```

The mirror copies new or changed files and never deletes destination files. It
verifies presence and byte count; a source advancing during copying is reported
as newer than the snapshot. SQLite `-wal` files are included; `-shm` files are
excluded because SQLite rebuilds shared memory and active writers may lock it.

**This file-by-file mirror is not a guaranteed consistent live SQLite backup.**
Matching sizes or copying both the database and WAL at different moments does
not establish a coherent snapshot. Before any approved database operation, inspect
writers and existing backups, select a SQLite-consistent backup method such as the
online backup API, and validate that snapshot. No such database snapshot or restore
is performed by repository cleanup.

Quarterly archives are built from the mirror:

| Archive                     | Included artifacts                                                                                                     |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `data_day_<YYYYQn>.tar.gz`  | `*_day.csv`, `*.json5`                                                                                                 |
| `data_min_<YYYYQn>.tar.gz`  | `*_min.csv`                                                                                                            |
| `data_meta_<YYYYQn>.tar.gz` | `corporate_actions.sqlite`, `symbol_meta.sqlite`, latest and historical value-chain JSON with relative paths preserved |

These archives exclude `official_daily.sqlite`, `monthly_revenue.sqlite`,
`institutional.sqlite` and revenue/institutional raw responses. The mirror is their
local backup tier; a worktree archive is not a full production-data backup.
Raw observations cannot be recreated with their original observation time.

Archives do not lock the mirror. Avoid another backup updating it during archiving;
using a verified mirror alone does not guarantee immutability. Repeating archive
creation in the same quarter overwrites that quarter's archive, so preserve an
existing archive separately before an approved replacement. Upload and retention
remain manual.

For restoration, first compare source and destination age, content and database
versions. A generic recursive copy can overwrite newer files even without deleting
anything. Preserve newer data, use consistent database snapshots, and validate a
restore in scratch space before any approved production replacement. Never merge
different SQLite versions by simply copying one over the other.
