# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-mops-rights-evidence/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-mops-rights-evidence/proposal.md).
No production action or acquisition is authorized by this record.

## Why

Already-held MOPS distribution notices include ordinary-share bonus and subscription terms not represented by the cash-only projector. Exact share quantities can be reused without new acquisition or inference from cash amounts.

## What Changes

- Add a pure projection extending the validated dividend-statement context with explicitly labeled ordinary-share rights fields.
- Preserve exact earnings/reserve grants, subscription ratio/price/class, unknowns and competing revisions without selecting a version.

## Capabilities

### New Capabilities

- `mops-rights-evidence`: Source-bound ordinary-share distribution and subscription statements.

### Modified Capabilities

None.

## Impact

New helper, focused tests and documentation. Additive JSON evidence API only; no CSV, SQLite or Parquet changes. No network calls, credentials, scheduler integration, entitlement execution, data overwrites or timing inference.
