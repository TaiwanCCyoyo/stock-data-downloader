# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-29-add-verified-data-backup/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-29-add-verified-data-backup/tasks.md).
No production action or acquisition is authorized by this record.

# Tasks

## 1. Backup module

- [x] 1.1 Replace the whole-directory zip in `backup_data.py` with an additive mirror into `<backup-dir>/data` that never deletes.
- [x] 1.2 Verify after every copy that each source file exists in the destination with a matching byte count; fail loudly with the offending paths.
- [x] 1.3 Resolve the destination from `--backup-dir` or `$SHIOAJI_BACKUP_DIR`, with no default; refuse a destination inside the repository.
- [x] 1.4 Add `--verify-only` to re-check an existing backup without copying.
- [x] 1.5 Add `--archive`, writing quarter-labelled `data_day` / `data_min` tarballs under `<backup-dir>/archive`, built from the mirror.

## 2. Daily run integration

- [x] 2.1 Load `.env` at the start of `run_daily.main()`.
- [x] 2.2 Add the backup as step 7, skipped when a cache-writing step (1-4) failed.
- [x] 2.3 Track failures and return 0/1 from `main()`; exit with that status.
- [x] 2.4 Add `--backup-dir` / `--archive` passthrough arguments.

## 3. Tests

- [x] 3.1 `test_backup_data.py`: mirror copies everything including subdirectories; a source deletion does not propagate; second run is incremental; a changed file is recopied.
- [x] 3.2 Verification detects a missing file and a size mismatch; `--verify-only` raises on an incomplete backup.
- [x] 3.3 Destination resolution: explicit beats environment; unset is a skip, not a failure; inside-repo is refused.
- [x] 3.4 Archives split daily from minute bars, are quarter-labelled, and round-trip on extraction; archiving without a mirror is an error.
- [x] 3.5 `test_run_daily.py`: clean run exits 0 and backs up; a failed step exits 1; a failed cache-writing step skips the backup; a failed non-cache step does not; zero downloads is not a failure.

## 4. Documentation

- [x] 4.1 Document `SHIOAJI_BACKUP_DIR` in `.env.example`.
- [x] 4.2 Rewrite the backup sections of `README.md` and `docs/zh-TW/README.md`.

## 5. Verification

- [x] 5.1 `uv run pytest` passes (124 tests).
- [x] 5.2 `uv run ruff format` / `uv run ruff check` clean.
- [x] 5.3 Run against the real destination and confirm it continues an existing mirror rather than starting a second tree (observed: 0 copied, 4126 verified).
