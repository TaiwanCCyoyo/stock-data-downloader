# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-explicit-corporate-detail-capture/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-explicit-corporate-detail-capture/proposal.md).
No production action or acquisition is authorized by this record.

## Why

Annual TWSE corporate-action tables hold detailed-event keys but omit economic share and
cash terms. The public TWTAVUDetail and TWT49UDetail endpoints expose those terms directly;
an explicit bounded capture can preserve them without inverting reference-price factors.

## What Changes

- Add an opt-in CLI for an explicit event plan with hashed annual raw-row provenance.
- Preserve raw HTTP bytes and a per-request evidence ledger in a new exclusive output root.
- Extract literal named terms, retaining units, unknown historical availability, and code/date
  request identity; do not create executable entitlements or overwrite normalized databases.
- Enforce request/time budgets, pacing, no retry/redirect, and stop-on-failure behavior.
- Non-goals: full-market auto-discovery, daily scheduling, MOPS, credentials, broker activity,
  price acquisition, inferred dividend/share quantities, and PIT/delivery certification.

## Capabilities

### New Capabilities

- `explicit-corporate-detail-capture`: provenance-bound capture of specified TWSE event details.

### Modified Capabilities

None. Existing corporate-actions-index artifact and fetch behavior remain unchanged.

## Impact

New standalone capture script, helper, tests and documentation. New plan/manifest/JSONL/raw
file contracts under caller-selected output paths; no existing CSV/SQLite/Parquet schema
changes, no dependencies, no daily scheduler changes. This is an explicit historical-evidence
capture like the existing explicit slice/evidence utilities, not a new daily acquisition step.
