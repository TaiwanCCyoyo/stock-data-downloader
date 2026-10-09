# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-versioned-technical-cache/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-versioned-technical-cache/tasks.md).
No production action or acquisition is authorized by this record.

## 1. Components and identities

- [x] 1.1 Implement normalized-input validation and unrounded technical-components.v1 with hand-checkable warm-up/gap/seed tests.
- [x] 1.2 Implement per-symbol content-addressed input/output storage, immutable manifests, full verification, writer exclusion and correction/reuse tests.

## 2. Integration and discoverability

- [x] 2.1 Add local builder/verification CLI and backward-compatible consolidated-builder integration with functional tests.
- [x] 2.2 Document schema, definitions, producer/consumer entry points, price-basis limits, preservation, storage growth and post-merge deployment.

## 3. Verification and delivery

- [x] 3.1 Run focused uv run pytest and uv run ruff check through normal commit checks; inspect generated synthetic manifest/input/features and record cold/warm measurements.
- [ ] 3.2 Commit task-owned files, publish PR and resolve valid current-head review findings; leave merge to the owner.
- [ ] 3.3 After merge, verify a quiet production window and backup state, update main and additively build/verify the cache without new downloads or price replacement.
