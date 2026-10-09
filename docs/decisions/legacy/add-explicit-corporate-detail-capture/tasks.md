# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-explicit-corporate-detail-capture/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-explicit-corporate-detail-capture/tasks.md).
No production action or acquisition is authorized by this record.

## 1. Capture utility

- [x] 1.1 Implement source-bound plan validation and observed literal detail schemas.
- [x] 1.2 Implement exclusive bounded capture CLI with raw bytes, request/event receipts and manifest.
- [x] 1.3 Document plan/output contracts, incomplete status and historical availability limits.

## 2. Verification

- [x] 2.1 Run focused uv pytest cases for both schemas, malformed plans, stop/budget behavior and CLI.
- [x] 2.2 Run scoped Ruff/pre-commit and independent implementation review; fix material findings.
- [x] 2.3 Manually inspect a bounded live capture against raw annual-row provenance and saved replies.
