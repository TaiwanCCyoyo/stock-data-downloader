# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-24-add-value-chain-classification-snapshots/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-24-add-value-chain-classification-snapshots/proposal.md).
No production action or acquisition is authorized by this record.

## Why

The existing TWSE/TPEx `industry_category` metadata is too broad to represent tradable
value-chain cohorts such as memory IC, passive components, PCB, glass-fiber cloth, and
satellite technology. The Taipei Exchange publishes a finer official industry value-chain
taxonomy, but the project does not yet capture it as reproducible, dated local data.

## What Changes

- Add a standalone fetcher for the Taipei Exchange Industry Value Chain Information Platform.
- Persist a stable latest JSON artifact plus append-only dated snapshots so consumers can use
  only classifications that were known by a given research date.
- Record source URLs, fetch timestamps, chain/node identifiers, domestic security codes,
  names, listing segments, and inverse symbol-to-node membership.
- Refresh the artifact from `run_daily.py` when the latest successful snapshot is at least
  seven days old; an unavailable classification source remains a non-price-cache failure.
- Document the CLI, artifact contract, cadence, and look-ahead limitation.

Non-goals:

- No Shioaji credentials, broker access, order placement, or changes to price artifacts.
- No AI-generated or manually curated concept-stock labels such as market nicknames.
- No claim that a current official classification existed before its first stored snapshot.
- No reconstruction of historical classifications from present-day pages.

## Capabilities

### New Capabilities

- `value-chain-classification`: Fetch, validate, and persist official TPEx value-chain
  classification as latest and append-only dated JSON artifacts.

### Modified Capabilities

- `daily-update-orchestration`: Refresh value-chain classification on a bounded weekly cadence
  without making metadata-source failure block price-cache processing or backup.

## Impact

- New Python fetcher and parser under `scripts/` with focused tests under `tests/`.
- New generated-data contracts:
  `data/value_chain_classification.json` and
  `data/value_chain_classification_history/YYYY-MM-DDTHHMMSSZ.json`.
- `scripts/run_daily.py`, README documentation, and the daily orchestration behavior contract
  gain the scheduled metadata-refresh step.
- Quarterly `data_meta` archives gain the latest and append-only historical classification
  JSON paths so observations that cannot be reconstructed remain protected.
- No existing CSV, SQLite, or Parquet schema changes and no new runtime dependency.
