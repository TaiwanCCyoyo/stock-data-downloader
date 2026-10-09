# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-free-share-evidence/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-free-share-evidence/tasks.md).
No production action or acquisition is authorized by this record.

## 1. Implement

- [x] 1.1 Add pure source-bound extractors and document their evidence-only API.
- [x] 1.2 Add focused tests for ratios, identities, malformed input and unknown timing.

## 2. Verify

- [x] 2.1 Run focused uv pytest and changed-file pre-commit including Ruff.
- [x] 2.2 Inspect held source examples and serialized output without modifying data.
- [x] 2.3 Resolve concrete implementation review findings and record limitations.
