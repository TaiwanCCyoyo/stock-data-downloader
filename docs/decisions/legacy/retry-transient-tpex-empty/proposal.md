# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/retry-transient-tpex-empty/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/retry-transient-tpex-empty/proposal.md).
No production action or acquisition is authorized by this record.

## Why

A successful empty TPEx history response currently settles a date that has no TPEx rows, even when the provider has only temporarily withheld data. The date is then skipped permanently; unfinished dates represented only by checkpoints are also omitted by automatic planning.

## What Changes

- Leave every newly attempted empty TPEx day retryable until a nonempty response succeeds.
- Include valid unfinished TPEx whole-market checkpoints in automatic candidates, including dates absent from both price markets after long interruptions.
- Retain existing settled checkpoints and gaps-only planning when the checkpoint table is absent.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `official-price-source`: make transient TPEx empty responses and checkpoint-only unfinished days recoverable through ordinary subsequent runs.

## Impact

Changes the TPEx branch and automatic planner in `scripts/backfill_official_history.py`, focused regressions, and pipeline documentation. SQLite, CSV, Parquet, raw-response formats, HTTP endpoints, field normalization, upsert precedence, unit commits, locks and request budgets remain compatible.

Non-goals: no credential changes, live downloads, canonical data mutation, bulk relabeling of old `ok`/`empty` checkpoints, parquet rebuild, scheduler changes or production deployment. Existing settled empties require separate evidence and an authorized bounded repair if a later audit identifies genuine missing data.
