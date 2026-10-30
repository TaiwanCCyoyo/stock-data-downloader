# Operations and Data Safety

[Documentation index](README.md) · [Pipeline commands](pipeline.md)

## Production data and worktrees

The primary checkout's data directory is the production source: `data/` by
default, or the external directory its `.env` names (see
[data location](#data-location)). All mutable data defaults resolve through
`config.config.DATA_DIR`. A worktree's `data/` is an independent physical snapshot. It may omit `raw/`,
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

## Data location

`config.config.DATA_DIR` is `<checkout>/data` unless `SHIOAJI_DATA_DIR` names an
external directory. The process environment wins over the checkout's own `.env`;
the production checkout sets it in its private `.env`, which the scheduled
launchers already load:

```dotenv
SHIOAJI_DATA_DIR=D:/StockData/stock-data-downloader
```

- A configured value must be an absolute, existing, link-free directory that
  neither contains nor lies inside the checkout. Anything else stops every entry
  point at import with `DataDirConfigError`; nothing falls back to `data/` and
  nothing creates the directory, because a typo must not start re-downloading
  quota-limited history into an empty folder.
- Each checkout reads only its own `.env` (`config.ENV_FILE`), never one found by
  searching parent directories. A development worktree under
  `<primary>/.worktrees/<name>/` has no `.env`, so it keeps its own `data/`
  snapshot and does not inherit the production data directory, backup
  destination or credentials. Set the variable in a single process's environment
  only for a deliberate, explicit run.
- Do not set `SHIOAJI_DATA_DIR` as a user or machine environment variable: every
  checkout and worktree would then write production data.
- `config/stock_category.json5` is tracked configuration, packaged beside
  `config.py` in the wheel. `config.STOCK_CATEGORY` always reads that resource,
  independently of the working directory and the configured data root.
- `stock_symbol_mapping.json5` is downloaded data at
  `config.STOCK_SYMBOL_MAPPING = DATA_DIR / "stock_symbol_mapping.json5"`.
  Both the writer and its existing-mapping shrink guard read that same path.
  With the setting above this is
  `D:/StockData/stock-data-downloader/stock_symbol_mapping.json5`. Consumers use
  this filename under the same shared root, without creating another root or
  relying on `<checkout>/data`. No separate mapping setting is needed.
- Updating the code moves only the tracked category resource. It does not move
  acquired data or remove old category/mapping copies in data roots or backups;
  preserve and report any such copies rather than overwriting or deleting them.
- `run_daily.py` and `backfill_official_history.py` log the directory in use and
  where its setting came from at the start of each run.
- Moving an existing data directory is a production data operation: pause the
  scheduled tasks, wait for writers to exit, checkpoint SQLite, move within the
  same volume, verify, then set the variable. The backup mirror compares relative
  paths, sizes and modification times, so a same-volume move does not recopy it.

## Windows scheduling

**Rebuild candidate gate:** before the owner renames the candidate folder and
confirms its formal production location, do not register, replace or resume any
market-data update schedule. Existing legacy-task disabling is handled by a
separate owner-assigned task; do not repeat it from this candidate.

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

### Writer lock files

Each long-running writer keeps an advisory JSON marker in the data directory while it runs:

| File                      | Writer                                 | Content while running                 | After a normal end |
| ------------------------- | -------------------------------------- | ------------------------------------- | ------------------ |
| `.run_daily.lock`         | `scripts/run_daily.py`                 | `{"pid", "started_at", "git_commit"}` | file kept, empty   |
| `.official_backfill.lock` | `scripts/backfill_official_history.py` | `{"pid", "started_at"}`               | file removed       |

A consumer can treat either file whose `pid` names a live process as "the producer is
writing"; an empty or missing file means no run. A record whose process is gone is stale (an
earlier run died) and is not a writer; the next run logs a warning and replaces it.

`run_daily.py` additionally holds an exclusive OS byte-range lock on its file for the whole
run (one byte at offset 1 MiB, past the JSON, so readers are never blocked; `msvcrt` on
Windows, `fcntl` record locks elsewhere, read only through the holding descriptor because
closing any descriptor of the file drops a POSIX record lock):

- A second `run_daily.py` refuses to start (exit 1) while it is held, and two runs starting
  together cannot both pass.
- A crashed or killed run releases it with its process, so a pid left in the record never
  blocks a later run. A record found under a free lock whose live pid predates its
  `started_at` is a writer that does not take the OS lock (a version from before it) and is
  refused; a live pid created after `started_at` is a reused pid and treated as stale.
- The run clears its record before closing and keeps the file, so it never deletes a file a
  new run has just locked.
- On Windows the run puts itself in a kill-on-close job object right after taking the lock,
  so child processes it starts (such as the step 2 conversion pool) end when it ends, however
  it ends. If that binding fails it exits 1 before any step writes. POSIX has no equivalent here: there the lock covers the parent process only.

The files do not stop SQLite readers, and `StockProject-*` task state remains the primary
signal. Deploy a lock-aware version only while no older `run_daily.py` is running: versions
before `.run_daily.lock` leave no marker at all, so nothing can detect them.

Task Scheduler's `ExecutionTimeLimit` ends only `wscript.exe`; the `cmd`/`uv`/Python chain
can keep running as an orphan (2026-10-29: 34 minutes past the 3h limit). The launchers
export `STOCK_PROJECT_TASK_LIMIT_SECONDS`; Python's heartbeat then logs one ERROR
(`task time limit passed: ...`) and the wrapper's `end` line records `elapsed_s` and
`over_limit`. Nothing is stopped automatically.

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
absolute destination outside this repository and outside the data directory in
the primary checkout's private `.env`:

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

The downloaded `stock_symbol_mapping.json5` remains covered by both the data
mirror and the `*.json5` daily archive rule. The tracked category dictionary is
distributed through Git and the wheel, outside the mutable data backup scope.

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
