# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-07-add-data-quality-report/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-07-add-data-quality-report/proposal.md).
No production action or acquisition is authorized by this record.

## Why

Local data completeness currently has no observable signal. A missing `{code}_day.csv`,
a hole of trading days inside an existing file, or a corporate-action ex-date with no
matching price behavior all go unnoticed until a downstream consumer happens to trip over
them. Separately, failures during the daily refresh are under-logged: `run_daily.py` records
only the exception type name for each step, and the Shioaji API usage-limit check in
`download_stock.py` swallows its own failures with a bare `except Exception: pass`. This
change adds a standalone health-check tool that reports data completeness as a versioned
artifact, tightens failure logging, and adds a way to repair a known gap without waiting for
the next incremental run to reach it.

## What Changes

- Add `check_data_integrity.py`: derives a Taiwan trading-day calendar from the union of
  trading dates seen across a small set of high-liquidity reference symbols (e.g. `2330`,
  `0050`), cross-checks references against each other for confidence, then scans every
  `data/{code}_day.csv` for missing files, missing trading days within an existing file, and
  zero-volume anomaly dates. Cross-checks price gaps/jumps against `data/corporate_actions.sqlite`
  ex-dates in both directions (a gap with no matching event row; an event row with no visible
  price gap). Writes `data/data_quality_report.json` with a stable, versioned schema.
- Wire the health check into `run_daily.py` as a fifth step (after the parquet rebuild), so a
  fresh report is produced on every daily run without a separate invocation.
- Extend every step's exception handling in `run_daily.py` to log the full exception message
  (not just `type(e).__name__`).
- Fix the silent `except Exception: pass` around the Shioaji usage-limit check in
  `download_stock.py`: log the caught exception instead of discarding it.
- Add a targeted re-fetch option to `download_stock.py`: an explicit `--start`/`--end` date
  range that bypasses the incremental "resume from last timestamp" shortcut, for repairing a
  hole in the middle of a symbol's history. Re-fetched rows merge into the existing file
  without duplicating overlapping rows.

## Capabilities

### New Capabilities

- `data-quality-report`: the health-check tool and the `data_quality_report.json` artifact
  contract (schema, fields, and update cadence).

### Modified Capabilities

- `daily-update-orchestration`: the one-click refresh gains a fifth step (health check) and
  fuller exception logging for all steps.
- `price-data-pipeline`: adds a targeted re-fetch capability and fixes a silent exception
  swallow in the usage-limit check.

## Non-Goals

- No automatic repair triggered by the health check itself — the targeted re-fetch is a
  separate, explicit action.
- No change to corporate-action classification or price-adjustment math — this only
  cross-checks price gaps against existing `corporate_actions.sqlite` rows for plausibility.
- No new network endpoints or credentials — this reads and re-derives data already fetched
  through existing entry points.
- Does not define how any downstream consumer displays or interprets
  `data_quality_report.json` — this pipeline is decoupled from any consumer, per
  `openspec/config.yaml`.

## Impact

- New file: `check_data_integrity.py`; new artifact: `data/data_quality_report.json`.
- `run_daily.py`: new fifth step; fuller exception logging for all five steps.
- `download_stock.py`: targeted re-fetch option; fixed silent exception swallow.
