# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-annual-cash-evidence/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-annual-cash-evidence/proposal.md).
No production action or acquisition is authorized by this record.

## Why

Annual exchange bulletins already contain useful cash evidence, but a combined
rights-and-cash value must not be mistaken for a cash dividend. An offline,
source-bound projection allows reuse without refetching or overwriting data.

## What Changes

- Add a pure bytes-to-records annual cash evidence API for TWSE TWT49U and TPEx exDailyQ.
- Preserve literal values, row locators, raw SHA256, reported precision and unknown timing.
- Use TWSE combined value only for explicitly cash-only rows; mixed/right rows remain
  non-cash-resolved. Use the explicit TPEx cash-dividend column.
- No network, credentials, daily scheduler changes, database writes, revision selection,
  net-cash credit assumptions or changes to existing CSV/SQLite/Parquet contracts.

## Capabilities

### New Capabilities

- `annual-cash-evidence`: Offline typed cash evidence from held annual exchange responses.

### Modified Capabilities

None; the existing corporate-action normalization remains unchanged.

## Impact

One pure helper, focused tests and API documentation. Adds an in-memory JSON-compatible
`annual-cash-evidence.v1` contract; no mandatory output path or acquisition entry point.
No new dependencies or imports from consumers.
