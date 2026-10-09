# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-par-value-evidence/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-par-value-evidence/tasks.md).
No production action or acquisition is authorized by this record.

## 1. Source-bound implementation

- [x] 1.1 Implement pure parsers and exact source/identity/quantity validation.
- [x] 1.2 Document the additive evidence contract and unresolved boundaries.

## 2. Verification

- [x] 2.1 Run `uv run python -m pytest tests/test_par_value_evidence.py` on positive, missing and conflicting synthetic cases.
- [x] 2.2 Run `uv run ruff check` and scoped pre-commit on changed files.
- [x] 2.3 Manually inspect parsed held official rows, including rounded prices and empty detail, against raw literals.
- [x] 2.4 Complete focused implementation review and resolve concrete findings.
