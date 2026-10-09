# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/tolerate-vanishing-backup-source/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/tolerate-vanishing-backup-source/tasks.md).
No production action or acquisition is authorized by this record.

## Implementation

- [x] Skip a source file that disappears after listing, in both the copy and verify phases.
- [x] Add a regression test covering removal before copy and before verification.
- [x] `uv run pytest tests/test_backup_data.py` and `uv run ruff check` pass.
