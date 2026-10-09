# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-mops-dividend-evidence/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-mops-dividend-evidence/tasks.md).
No production action or acquisition is authorized by this record.

## 1. Capture implementation

- [x] 1.1 Implement strict list/detail validation, bounded POST transport and exclusive raw/manifest capture.
- [x] 1.2 Add opt-in CLI and artifact/operating documentation without changing daily orchestration.

## 2. Verification

- [x] 2.1 Add offline fixtures/tests for observed date mismatch and failure, empty, budget and no-overwrite behavior.
- [x] 2.2 Run focused `uv run python -m pytest`, `uv run ruff check` and changed-file pre-commit; review concrete implementation/security risks.
- [x] 2.3 Inspect a capture replayed from retained source samples, verify hashes and date provenance, and record untested coverage.
