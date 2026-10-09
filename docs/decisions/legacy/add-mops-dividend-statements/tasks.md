# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-mops-dividend-statements/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-mops-dividend-statements/tasks.md).
No production action or acquisition is authorized by this record.

## 1. Projection

- [x] 1.1 Implement pure hash/identity-qualified statement projection with explicit field states.
- [x] 1.2 Add focused tests for amounts/periods/dates, blanks/ambiguity/unsupported input, identity and mutation isolation.

## 2. Verification

- [x] 2.1 Inspect retained official samples and record separate statements, matching distribution terms and unresolved amendment links without cash creation.
- [x] 2.2 Run uv run python -m pytest, uv run ruff check, changed-file pre-commit and strict OpenSpec validation.
- [x] 2.3 Complete scoped implementation review and document schema, observed scope and remaining cash/link limitations.
