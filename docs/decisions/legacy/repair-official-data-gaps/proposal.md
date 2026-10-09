# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/repair-official-data-gaps/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/repair-official-data-gaps/proposal.md).
No production action or acquisition is authorized by this record.

## Why

TWSE bulk prices lag one trading day, and month-only repair leaves missed runs unresolved until rollover. Historical TWSE coverage also omits instruments absent from the current bulk universe; institutional and monthly-revenue artifacts have separately measured missing periods.

## What Changes

- Add resumable TWSE whole-market day requests from MI_INDEX, with insert-only price writes and gap-first planning, including recent weekdays missing from both markets.
- Preserve per-symbol month repair without expanding its universe from MI_INDEX-only historical instruments.
- Attempt a dated TWSE supplement when the bulk date trails TPEx; unavailable or unpublished supplements leave the successful bulk fetch intact.
- Distinguish whole-market day and symbol-month checkpoint progress.
- Add an opt-in consecutive-failure limit for large backfills so provider maintenance can stop a run after preserving its error checkpoints; keep the default retry/run behavior unchanged.
- Probe source schemas, audit trading-calendar and corporate-action coverage, and backfill only missing production institutional/revenue units under the owner's explicit download authorization.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `official-price-source`: TWSE whole-market day coverage, insertion precedence, day checkpoints, gap planning, and optional dated supplement.
- `daily-update-orchestration`: the official fetch attempts a newer dated TWSE supplement before the existing parquet rebuild.

## Impact

Changes affect the official fetcher/backfiller, a shared MI_INDEX parser, associated tests and progress documentation. `official_daily.sqlite` keeps its schema and share-based units but gains historical TWSE instruments and missing rows. `official_history_raw_ranges` additionally stores TWSE units with empty code and ISO day periods. CSV and parquet schemas and the parquet symbol universe remain unchanged. Existing CSV-backed instruments may gain official coverage and longer histories; measure that boundary before rebuilding derived production artifacts.

Non-goals: pre-2010 prices, broader parquet eligibility, broker credentials or orders, schedule changes, replacing existing price rows with MI_INDEX, changing research missions/results, and deploying unmerged source into the primary checkout. Production data downloads are additive, retain raw evidence and truthful availability metadata, and use explicit production paths.
