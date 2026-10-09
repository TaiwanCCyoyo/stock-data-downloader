# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-26-add-institutional-data-pipeline/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-26-add-institutional-data-pipeline/tasks.md).
No production action or acquisition is authorized by this record.

## 1. Artifact and parsers

- [x] 1.1 Add `scripts/fetch_institutional.py` with the SQLite schema, raw-snapshot/checkpoint contract, and symbol-metadata classification.
- [x] 1.2 Implement exact-signature TWSE 12/16/19-field normalization and unknown-signature rejection.
- [x] 1.3 Implement populated-table discovery plus exact-signature TPEx 16/24-field normalization and unknown-signature rejection.

## 2. Collection entry points

- [x] 2.1 Implement atomic idempotent upserts and `(market, date)` checkpoint status transitions.
- [x] 2.2 Add daily and inclusive historical CLI modes with throttling, resume, force, and request/time budgets using `fetch_json_with_retry`.
- [x] 2.3 Preserve raw JSON snapshots with SHA-256 and conservative `published_at`/`fetched_at` provenance.

## 3. Daily orchestration

- [x] 3.1 Add the rolling institutional refresh to `scripts/run_daily.py` as an independently logged step.
- [x] 3.2 Add a regression test proving an institutional fetch failure does not abort later daily steps.

## 4. Automated tests

- [x] 4.1 Add fixture tests for all documented TWSE and TPEx schema eras, null semantics, and unknown-signature rejection.
- [x] 4.2 Add SQLite/raw-snapshot tests for idempotency, checkpoint resume, current-day partial handling, and metadata fallback.

## 5. Validation

- [x] 5.1 Run targeted tests and the full pytest suite.
- [x] 5.2 Run Ruff and pre-commit against every changed file.
- [x] 5.3 Run a bounded live two-market E2E, inspect the SQLite rows/checkpoints/raw hashes, and record any skipped verification.
