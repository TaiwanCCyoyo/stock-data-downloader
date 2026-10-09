# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-26-add-institutional-data-pipeline/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-26-add-institutional-data-pipeline/proposal.md).
No production action or acquisition is authorized by this record.

## Why

The standalone pipeline does not preserve daily TWSE/TPEx three-major-institution flows,
so later point-in-time research cannot distinguish foreign, investment-trust, and dealer
accumulation. The official dated endpoints and their historical schema signatures are now
documented, making a resumable first-party acquisition path implementable without further
source discovery.

## What Changes

- Add an official TWSE T86 and TPEx dailyTrade fetcher that detects known schema eras by
  field signature and rejects unknown layouts.
- Add `data/institutional.sqlite` with normalized gross/net share counts, source provenance,
  fetch/publication timestamps, and `(market, date)` checkpoints.
- Provide bounded, resumable historical backfill and daily-incremental CLI modes for both
  markets, with raw response snapshots retained for auditability.
- Add an isolated institutional step to `scripts/run_daily.py`; endpoint failure is logged and does
  not abort the remaining daily pipeline.
- Do not add trading logic, downstream strategy integration, credentials, or historical
  social-attention reconstruction.

## Capabilities

### New Capabilities

- `institutional-data-pipeline`: Official point-in-time acquisition, normalization,
  storage, checkpointing, and raw-snapshot contracts for daily institutional flows.

### Modified Capabilities

- `daily-update-orchestration`: The one-click refresh gains a failure-isolated
  institutional-data step.

## Impact

- New production module and tests: `scripts/fetch_institutional.py` and
  `tests/test_fetch_institutional.py`.
- Modified orchestration and tests: `scripts/run_daily.py` and
  `tests/test_run_daily.py`.
- New generated, gitignored artifact: `data/institutional.sqlite` plus raw snapshots under
  `data/raw/institutional/`; no existing CSV, SQLite, or Parquet contract changes.
- External calls use the existing credential-free `shp_utils.official_api` helper and the
  documented TWSE/TPEx public endpoints; no new dependency is introduced.
