# Importing a staged institutional history package

[Documentation index](README.md) · [Institutional pipeline contract](contracts/institutional-data-pipeline/spec.md) · [Operations](operations.md)

Historical institutional reports can be acquired into an isolated staging package
(`<DATA_DIR>/staging/institutional-pre2019/<run-id>/`) with the unchanged
`fetch_institutional.py backfill --db-path ... --raw-dir ...`. The staging package is
not a production source. `scripts/import_institutional_staging.py` is the only
supported way to add its verified units to `institutional.sqlite`.

## Why the fetcher is not the merge tool

The fetcher replaces a `(market, date)` unit (delete, then `ON CONFLICT DO UPDATE`).
That is right for a re-fetch and wrong for a merge: it would silently overwrite a row
another feed already owns. The importer only ever runs plain `INSERT`s, so any key
already present aborts the transaction instead of changing it.

## Behavior

- **Inputs are explicit.** `--package`, `--db-path` and, for writes, `--receipt-dir`
  are required. Nothing falls back to `config.DATA_DIR`, and the importer never
  creates the target database or runs the fetcher's schema initializer against it.
- **Package integrity first.** Every manifest file's size and SHA-256, and the
  manifest's payload digest, are recomputed before planning or writing. The staging
  database is opened `immutable=1`, so the sealed package gains no sidecar files.
- **Candidates.** Only `manifest.importable_units` whose staging checkpoint is `ok`
  with a positive matching row count, a matching raw digest and a date on or before
  `--cutoff` are considered. `partial`/`empty`/`error` units are never imported and
  never promoted.
- **Schema.** Column names, types, nullability and keys of both tables and the
  `schema_version` must match. A mismatch stops the import; no migration is run.
- **Per unit, all or nothing.** A unit is inserted only when the target has neither
  a checkpoint nor rows for that `(market, date)` and no row for any of its
  `(date, code)` keys. Equivalent existing content (every column except
  `fetched_at` and `instrument_type`, which are reported separately) is skipped.
  Different content, a different market on the same `(date, code)`, extra target
  rows, rows without a checkpoint or a checkpoint with another status are conflicts:
  the unit is left untouched and reported.
- **Raw first.** Each inserted unit's raw snapshot is copied into the ordinary raw
  tree, `raw/institutional/<market>/<file>` (the fetcher's own layout and file name,
  which carries the acquisition timestamp), with exclusive creation and a re-read
  digest check. There is no separate imports tree: imported history is maintained
  with the rest of the artifact. An existing file is reused only when its digest
  matches; a different file of that name is a conflict and is never replaced. The
  checkpoint's `raw_path` is that DB-relative location, so nothing refers to staging.
  `fetched_at` keeps the acquisition time; the import time lives in the receipt.
- **Bounded transactions.** Units are committed in batches under `BEGIN IMMEDIATE`;
  the plan is re-checked inside the transaction. Any failure rolls the batch back.
  Raw files already preserved for a rolled-back batch are listed in the receipt and
  kept.
- **Re-runnable.** A unit whose checkpoint already carries the same `raw_path`
  and digest and whose rows are equivalent is reported as already imported, so a
  crash between a commit and its receipt entry cannot duplicate work.
- **Receipt.** `import-receipt.jsonl` (append-only, fsynced per event) and
  `import-summary.json` in the explicit `--receipt-dir` (outside the data
  directory; the durable outcome is summarized in the import record below) record the package run id and payload digest, code commit,
  per-batch units and rows, skips, conflicts, rejections and the units left
  unresolved.
- **Writer safety.** Writes refuse to start a batch inside the weekday 18:00-18:45
  Asia/Taipei window, while any `StockProject-*` task is running, or after
  `--deadline`.

`backup` writes a SQLite online-backup snapshot of the target into a new directory,
checks its integrity and compares its per-market/year content digest with the live
database. `digest` prints that content digest, which is how an import proves that
pre-existing data did not change.

```powershell
uv run scripts/import_institutional_staging.py dry-run --package <run> --db-path <db> --out <plan.json>
uv run scripts/import_institutional_staging.py backup --db-path <db> --dest <new-dir>
uv run scripts/import_institutional_staging.py apply --package <run> --db-path <db> --receipt-dir <dir> --deadline <iso>
uv run scripts/import_institutional_staging.py digest --db-path <db> --out <digest.json>
```

Importing never refreshes `data-status.json`; run `scripts/write_data_status.py`
afterwards outside the write window. Staging packages and backups are kept until the
owner approves their cleanup.

## Import record

### 2026-10-27: pre-2019 history, package `20261024T212808+0800-codex-10f5e23f`

Acquired 2026-10-24/25 with the unchanged fetcher at producer commit `ee4171b`
(package payload digest `64c7fef2...c58d5`); imported 2026-10-27 00:42-00:49 by
`import_institutional_staging.py` at `35d3037`. The raw snapshots were then moved
from a temporary `raw/institutional/imports/<run-id>/` tree into the ordinary
`raw/institutional/<market>/` tree (commit `826503a` makes that the importer's
behavior), and their `raw_path` values updated in one transaction.

| Market | Year | Rows | Dates | Codes |
| --- | --- | ---: | ---: | ---: |
| TWSE | 2012 (from 05-02) | 132,210 | 172 | 865 |
| TWSE | 2013 | 192,078 | 246 | 901 |
| TWSE | 2014 | 204,631 | 248 | 918 |
| TWSE | 2015 | 205,576 | 244 | 945 |
| TWSE | 2016 | 204,278 | 244 | 991 |
| TWSE | 2017 | 216,495 | 246 | 1,046 |
| TWSE | 2018 | 231,011 | 247 | 1,089 |
| TPEx | 2014 (from 12-01) | 8,983 | 24 | 540 |
| TPEx | 2015 | 101,687 | 244 | 700 |
| TPEx | 2016 | 113,737 | 244 | 732 |
| TPEx | 2017 | 122,096 | 246 | 756 |
| TPEx | 2018 | 120,532 | 247 | 796 |

2,652 `ok` units and 1,853,314 rows were inserted; 0 skipped, 0 conflicts, 0
rejected (the target held no pre-2019 rows or checkpoints). Verification: every
pre-existing market/year group of both tables (2019-2026, including the 18 open
2026 `partial` checkpoints) has the same row count and all-column SHA-256 as the
pre-import online backup; `schema_meta` and `sqlite_master` are unchanged; every
imported unit equals its staged rows and checkpoint, and every raw file matches its
recorded digest; `PRAGMA integrity_check` is `ok`.

What the import does not establish:

- **TWSE lower bound.** T86 answered 2012-04-30 with an explicit floor message
  naming 101/05/02 (2012-05-02, digest `c54064f8...cbae1c`). That bounds this
  endpoint only, not every official TWSE source.
- **TPEx lower bound is unknown.** The earliest nonempty report is 2014-12-01. All
  106 price-calendar dates 2014-07-01 to 2014-11-28 returned empty and were not
  imported (no checkpoint written, so a later backfill may retry them). Dates before
  2014-07-01 were never scanned; `FIRST_TPEX_DATE` is a scan bound, not evidence.
- **2018-12-29 to 12-31.** Both markets returned empty for these three calendar
  days (6 units). The price calendar ends 2018-12-28; the empties were not imported
  and are not certified as holidays.
- **Not point-in-time.** `published_at` is null and `fetched_at` is the 2026-10-24
  acquisition time, not historical publication time. Because `data-status.json`
  reports the maximum `fetched_at`, its institutional `last_updated` may now come
  from this backfill rather than the newest daily fetch.
- **Identity.** `instrument_type` is `unknown` for every imported row: current
  symbol metadata is not evidence of historical classification, so filters on
  `instrument_type = 'stock'` exclude all pre-2019 rows. Names are the source-era
  names.
- **Combined period.** `data-status.json` reports one institutional period across
  both markets (`from` 2012-05-02); TPEx starts 2014-12-01.

The staging package, its copy in the backup mirror and the pre-import backup were
deleted with owner approval after this verification.
