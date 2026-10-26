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

| Date (Asia/Taipei) | Package run | Result |
| --- | --- | --- |
| 2026-10-27 | `20261024T212808+0800-codex-10f5e23f` | See below. |
