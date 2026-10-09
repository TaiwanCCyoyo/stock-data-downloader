# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-annual-cash-evidence/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-annual-cash-evidence/tasks.md).
No production action or acquisition is authorized by this record.

## 1. Implementation

- [x] 1.1 Add the pure source-specific bytes-to-cash-evidence helper and API documentation.
- [x] 1.2 Add focused synthetic tests for both sources, missingness, precision, provenance and malformed inputs.

## 2. Verification

- [x] 2.1 Run uv run pytest for the new helper and uv run ruff check through scoped pre-commit.
- [x] 2.2 Manually compare output with held raw examples and record source-specific observed counts without network or canonical writes.
- [x] 2.3 Complete bounded implementation review and strict OpenSpec validation.

Verification on 2026-09-18: 24 helper tests passed; scoped pre-commit passed;
`openspec validate add-annual-cash-evidence --strict` passed. Read-only implementation
review found no helper defect. Twelve held 2018-2023 annual files projected without
new acquisition: TWSE 5,679 rows and TPEx 5,280 rows, including instruments beyond
ordinary shares. These are source record counts, not market eligibility or completeness.
Raw field examples confirm TWSE six-decimal cash-only values and TPEx explicit cash;
mixed TWSE rows remain unresolved by this helper.
