# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-mops-dividend-statements/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-mops-dividend-statements/proposal.md).
No production action or acquisition is authorized by this record.

## Why

Raw announcements can combine cash dividends and cash subscriptions, and multiple announcements can describe one distribution. Source statements must be extracted with explicit missing/unsupported states before any later event linking or cash modelling.

## What Changes

- Add a pure, offline projection of one hash-verified ordinary MOPS dividend detail into `mops-dividend-statement.v1`.
- Preserve source identity and raw text alongside stated ordinary-share cash amount, dividend period, record date, stated ex-date and announced payment date.
- Keep blank, absent, ambiguous and unsupported values distinct from explicit zero. Produce no final economic event, deduplication decision or account cash.
- Non-goals: network, file/cache mutation, scheduling, credentials, generic prose interpretation, personal net payments, historical PIT certification or automated amendment linking.

## Capabilities

### New Capabilities

- `mops-dividend-statements`: Evidence-preserving source statement projection.

### Modified Capabilities

None. Existing collectors, CSV/SQLite/Parquet and raw manifests retain their contracts.

## Impact

Adds one Python helper, focused tests and documentation. Uses existing ordinary-detail identity validators and standard-library JSON, hashes, decimals and dates. No new dependency or CLI is required for a pure bytes-to-record transformation. A bounded offline multi-sample inspection checks its usefulness for later linking without merging statements or posting cash.
