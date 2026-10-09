# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-mops-material-evidence/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-mops-material-evidence/proposal.md).
No production action or acquisition is authorized by this record.

## Why

Dividend record-date announcements do not necessarily include later payment changes. A verified historical material disclosure contains a regional, conditional postponement that must remain available as raw evidence rather than becoming an unconditional cash event.

## What Changes

- Add an opt-in, company/month-bounded historical material-disclosure collector for the observed MOPS `t05st01` and `t05st01_detail` contract.
- Preserve raw responses, request identities, hashes, displayed publication date/time, full explanation and unprocessed rows in a new `mops-material-evidence.v1` manifest.
- Share bounded capture mechanics with the existing dividend collector while retaining its public API and manifest contract.
- Non-goals: payment normalization, actual investor credit dates, complete historical/PIT certification, automatic daily scheduling, broad backfill, credentials, trading or modification of existing data.

## Capabilities

### New Capabilities

- `mops-material-evidence`: Bounded raw historical material-disclosure capture with explicit evidence limits.

### Modified Capabilities

None. Existing CSV, SQLite, Parquet and dividend-evidence contracts remain unchanged.

## Impact

Adds a standalone CLI, source parser, tests and documentation. A shared helper may replace duplicated raw-capture mechanics. Uses the existing requests dependency and fixed official HTTPS routes only. Default new output is beneath `config.DATA_DIR/raw/mops/material_announcements`; explicit fresh output directories support bounded verification without touching caches.
