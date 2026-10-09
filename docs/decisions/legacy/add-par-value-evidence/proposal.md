# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-par-value-evidence/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-par-value-evidence/proposal.md).
No production action or acquisition is authorized by this record.

## Why

Ordinary-stock par-value changes are absent from the existing corporate-action
feeds. Rounded reference-price ratios cannot safely substitute for exact share
exchange ratios, so held official responses need separate source-bound evidence.

## What Changes

- Add pure offline parsers for TWSE TWTB8U resume rows, TWTB7UDetail share terms,
  and TPEx pvChgRslt embedded details.
- Preserve raw identity, field literals, explicit quantities and unresolved detail
  status; validate market/date/code bindings and distinguish three price bases.
- Add focused synthetic tests and inspect already captured real responses.
- Non-goals: network clients, credentials, schedules, cache migrations, trading
  policy, historical-publication inference or universal historical coverage.

## Capabilities

### New Capabilities

- `par-value-evidence`: Recomputable source-bound ordinary-stock exchange terms.

### Modified Capabilities

None.

## Impact

Adds `shp_utils/par_value_evidence.py`, focused tests and artifact documentation.
Adds a JSON-serializable evidence contract; no existing CSV/SQLite/Parquet schema,
source priority, dependency or daily acquisition behavior changes.
