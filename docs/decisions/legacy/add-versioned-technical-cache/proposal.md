# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-versioned-technical-cache/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-versioned-technical-cache/proposal.md).
No production action or acquisition is authorized by this record.

## Why

Daily-price consumers repeatedly calculate the same basic technical indicators, while the existing rounded raw-price SMA/EMA columns are not interchangeable with indicators using other price bases or warm-up definitions. Reuse needs an explicit, verifiable identity instead of treating any column named MA as equivalent.

## What Changes

- Add a versioned, content-addressed technical-feature cache with per-symbol reuse, immutable manifests, input/output hashes, runtime and implementation identities, and explicit price-basis and missing-row semantics.
- With an explicit build option, precompute a bounded set of reusable moving averages, returns, ranges, volatility, RSI and MACD components after consolidated raw daily prices are built. Keep existing CSV and price_daily.parquet schemas, calculations and daily default unchanged; do not automatically accumulate full history each day.
- Provide a standalone local-only builder that can consume explicitly labelled, already-normalized alternative price inputs without performing corporate-action adjustment itself.
- Reject corrupt cached artifacts and incomplete publication instead of returning stale values. Preserve older cache generations; no automatic pruning.

## Capabilities

### New Capabilities

- `technical-feature-cache`: Reusable, attributable technical components from an explicit daily-price input, distinct from strategy rules and forward outcomes.

### Modified Capabilities

None. The consolidated price artifact remains compatible; the new capability adds a sibling artifact after its existing build.

## Impact

New modules under shp_utils/, a scripts/build_technical_cache.py entry point, an additive call from scripts/build_price_parquet.py, focused tests and artifact documentation. No new dependencies, downloader changes, credentials, network calls, scheduler changes, universe expansion, corporate-action methodology, portfolio rules or forward labels. Default output is data/technical_features/; no source files are modified by the cache builder. Production generation is deferred until PR merge and coordination with active writers.
