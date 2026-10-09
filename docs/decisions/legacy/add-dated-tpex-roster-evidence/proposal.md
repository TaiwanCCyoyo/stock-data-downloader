# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-dated-tpex-roster-evidence/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-dated-tpex-roster-evidence/proposal.md).
No production action or acquisition is authorized by this record.

## Why

Current security registers cannot establish historical type membership. The observed
TPEx dated closing-price endpoint can provide auditable dated category rosters, but
its returned date and schema must be checked before treating rows as evidence.

## What Changes

- Add an offline parser of the observed dated TPEx category response.
- Emit source-qualified category evidence, not ordinary-share or PIT certification.
- Permit explicit isolated bounded capture tooling; no live cache or scheduler changes.

## Capabilities

### New Capabilities

- `dated-tpex-roster-evidence`: Validate dated category responses and retain raw identity.

### Modified Capabilities

None.

## Impact

Additive helper and tests only. No existing CSV, SQLite or Parquet contract changes.
No credentials, broker orders, paid sources, daily-run integration or ordinary-share policy.
