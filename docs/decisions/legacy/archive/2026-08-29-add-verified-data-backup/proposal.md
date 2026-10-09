# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-29-add-verified-data-backup/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-29-add-verified-data-backup/proposal.md).
No production action or acquisition is authorized by this record.

## Why

The `data/` cache has no protected copy. Its price data is acquired under a daily
API quota, so a loss is measured in days of re-downloading, not minutes of restoring.
`backup_data.py` exists but is a weekly manual habit that produces a single
whole-directory zip: it never verifies what it wrote, it is not wired into the daily
run, and nothing detects it having silently stopped.

Two failure modes drove the design and are worth stating, because the obvious
implementation of a backup has both:

- **A mirror that deletes is not a backup.** A conventional mirror (`robocopy /MIR`,
  `rsync --delete`) faithfully replicates a source-side deletion into the backup,
  turning one accident into two lost copies. This actually happened to a consumer of
  this cache: a recursive delete traversed a directory link and removed ~4000
  per-symbol CSVs. A mirroring backup would have propagated it within a day.
- **A reported success is not a verified one.** The same incident involved tools that
  reported success while doing the wrong thing. A backup nobody has checked is an
  assumption, and the moment it matters is the worst time to discover that.

Separately, `run_daily.py` catches every step's exception, logs it, and returns
normally, so the process always exits 0. An unattended scheduler therefore reports a
successful run on a night when the download failed — a monitoring blind spot, and one
that would also make an automated backup silently mirror nothing new forever.

## What Changes

- Rework `backup_data.py` from a whole-directory zip into a verified additive mirror:
  copy new and changed files into `<backup-dir>/data`, never delete on either side,
  then check every source file exists in the destination with a matching byte count
  and fail loudly otherwise.
- Add `--archive`, writing `<backup-dir>/archive/data_day_<YYYYQn>.tar.gz` and
  `data_min_<YYYYQn>.tar.gz` for offsite upload. Split by data type because the halves
  have very different economics: daily bars are tens of MB and are what consumers
  read, while minute bars dominate volume, so a retention window can keep many
  quarters of one and few of the other. Built from the verified mirror so a concurrent
  refresh cannot land a half-written file inside an archive.
- Resolve the destination from `--backup-dir` or `$SHIOAJI_BACKUP_DIR`, with no
  default. It must be an absolute path outside this repository, and the code refuses a
  destination inside the tree being backed up. `config.BACKUP_DIR` (repo-relative) is
  no longer used for this.
- Load `.env` at the start of `run_daily.py` instead of relying on a later step to do
  it, so machine-local settings are available to every step regardless of which run.
- Add the backup as step 7 of `run_daily.py`, skipped when any cache-writing step
  (1-4) failed, because mirroring a truncated file over a good backup copy is the one
  way an additive backup can still lose data. Steps 5-6 do not block it: step 5 is
  already specified to degrade rather than abort, and step 6 only writes a report.
  Downloading nothing is not a failure — that is a non-trading day.
- Make `run_daily.py` return, and exit with, a non-zero status when any step failed.

## Impact

- Affected specs: `daily-update-orchestration` (new step, exit-code contract), new
  `data-backup` capability.
- Affected code: `backup_data.py`, `run_daily.py`, `.env.example`, `README.md`,
  `docs/zh-TW/README.md`.
- **Behavior change for existing users:** `run_daily.py` now exits non-zero on a failed
  step. Anything treating its exit code as "did the process start" will begin seeing
  failures it previously ignored — which is the point, but it is a visible change.
- Backup stays inert until `SHIOAJI_BACKUP_DIR` is set, so nothing starts writing to an
  unexpected location on upgrade.
- `backup_data.py` no longer produces `backups/*.zip`. The archives replace it, in a
  location chosen by the operator.
