# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-explicit-official-price-slice/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-explicit-official-price-slice/tasks.md).
No production action or acquisition is authorized by this record.

## 1. Implementation

- [x] 1.1 Add the explicit read-only exporter and CLI without modifying existing jobs.
- [x] 1.2 Test selection, market identity, nulls, units, empty/partial coverage, hashes, row limits and safe output refusal.

## 2. Verification and documentation

- [x] 2.1 Run focused uv pytest, Ruff and changed-file pre-commit checks; obtain scoped implementation review.
- [x] 2.2 Export a fresh scratch database built from the three captured January 2019 payloads; inspect all 63 rows, units, metadata and hashes without touching existing caches.
- [x] 2.3 Document CLI/schema and limitations; record verification and validate the OpenSpec change.

Submodule commit and parent gitlink update remain separately subject to owner
confirmation. These tasks do not authorize publication, bulk downloads or a
strategy evaluation.
