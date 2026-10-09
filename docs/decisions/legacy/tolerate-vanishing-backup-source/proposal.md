# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/tolerate-vanishing-backup-source/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/tolerate-vanishing-backup-source/proposal.md).
No production action or acquisition is authorized by this record.

## Why

The 2026-09-18 daily backup failed with `FileNotFoundError` on `data/.official_backfill.lock`.
The mirror listed the transient lock file, the backfill started and removed it while the
mirror ran, and the next `stat` raised. The failure is a race between two scheduled tasks,
not a data problem, yet it fails step 10 of the daily run.

## What Changes

- A source file that disappears after it was listed is skipped (logged) during both the copy
  and the verification phases instead of failing the backup.
- Other read errors, missing destination files and size mismatches still fail as before.

## Impact

No data artifacts, schemas, credentials or network behavior change. Nothing is deleted from
the source or the mirror; a previously mirrored copy of a vanished file is kept.
