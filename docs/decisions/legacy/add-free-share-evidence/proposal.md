# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-free-share-evidence/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-free-share-evidence/proposal.md).
No production action or acquisition is authorized by this record.

## Why

Held corporate-action responses state free shares per thousand, but these quantities must not be confused with paid subscriptions or a price-adjustment ratio. Provide exact, source-bound quantities without inventing delivery or historical availability.

## What Changes

- Add pure offline extraction for TPEx exdailyq annual responses and TWSE TWT49U detail responses.
- Preserve source hashes, literals, row identity, exact rational quantity, and missing/invalid states.
- Keep entitlement availability, delivery and fractional cash settlement unresolved.

## Capabilities

### New Capabilities

- `free-share-evidence`: Offline source-specific free-share quantity evidence.

### Modified Capabilities

None.

## Impact

New helper, focused tests and documentation only. Adds a JSON-serializable evidence contract; existing CSV, SQLite and Parquet contracts are unchanged. No network, credentials, scheduler changes, generated-data overwrite, investment policy, or account simulation.
