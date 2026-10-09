# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-dated-tpex-roster-evidence/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-dated-tpex-roster-evidence/tasks.md).
No production action or acquisition is authorized by this record.

## 1. Parser

- [x] 1.1 Implement named-field, date, hash and row validation.
- [x] 1.2 Test valid, empty, wrong-date, duplicate and malformed responses using uv run pytest.

## 2. Evidence

- [x] 2.1 Inspect saved real responses and compare parsed codes with raw rows.
- [x] 2.2 Run uv run ruff check and scoped pre-commit; retain isolated capture evidence.
