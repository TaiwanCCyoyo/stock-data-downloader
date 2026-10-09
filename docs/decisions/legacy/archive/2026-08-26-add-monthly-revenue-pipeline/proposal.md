# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-26-add-monthly-revenue-pipeline/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-26-add-monthly-revenue-pipeline/proposal.md).
No production action or acquisition is authorized by this record.

## Why

The data cache has prices and corporate actions but no reusable monthly-revenue history, so
research cannot test revenue persistence or acceleration without an external, potentially
look-ahead-biased dataset. Taiwan's official MOPS sources expose monthly revenue for listed
and OTC companies, but their latest-batch and historical artifacts provide different time
evidence and must not be collapsed into a fabricated company announcement timestamp.

## What Changes

- Add a standalone monthly-revenue fetcher for official TWSE/MOPS listed and OTC sources.
- Store normalized monthly values, source publication dates, first-observed timestamps, fetch
  timestamps, revisions, and raw snapshots in an additive SQLite artifact.
- Support resumable historical backfill while labeling historical availability evidence
  separately from forward-observed snapshots.
- Add a failure-isolated refresh step to the existing one-click daily pipeline.
- Document the artifact contract, point-in-time limits, commands, and source behavior.

## Capabilities

### New Capabilities

- `monthly-revenue-pipeline`: Official monthly-revenue acquisition, normalization, revision
  history, raw evidence, and point-in-time-safe availability metadata.

### Modified Capabilities

- `daily-update-orchestration`: The one-click daily refresh gains a non-blocking monthly-revenue
  refresh step.

## Non-Goals

- No strategy, ranking rule, backtest-engine join, dashboard, or investment recommendation.
- No broker credentials, paid data vendor, browser automation, or weakening of TLS validation.
- No claim that a batch `report_date` is an individual company's exact filing time. Exact
  historical times remain null unless an official source supplies them; forward snapshots use
  the first local observation as the conservative availability boundary.
- No overwrite or deletion of existing cache artifacts, and no historical raw snapshot invented
  for a payload that was not actually observed.

## Impact

- Adds `data/monthly_revenue.sqlite` and additive raw payloads under
  `data/raw/monthly_revenue/`.
- Adds a Python CLI and tests, and updates the daily pipeline plus English/Traditional Chinese
  usage documentation.
- Uses the existing `requests`, SQLite, logging, configuration, and official-source retry
  helpers; no dependency or existing artifact schema is changed.
