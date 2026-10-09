# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/retry-transient-tpex-empty/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/retry-transient-tpex-empty/design.md).
No production action or acquisition is authorized by this record.

## Context

The TPEx history endpoint can return a successful empty response before publishing a trading day. `run_backfill` currently marks an empty gap day as settled, and the planner only reconstructs candidates from price rows. Together these rules can permanently strand a missing day. The existing official-price-source specification already requires ambiguous empty whole-market responses to remain retryable.

## Goals / Non-Goals

**Goals:** retain automatic recovery of new transient empties and unfinished checkpoint-only TPEx dates, with current artifact and request contracts.

**Non-Goals:** change TWSE empty policy, migrate old settled checkpoints, normalize new provider fields, download live history, rebuild artifacts or deploy production code.

## Decisions

1. Complete a new TPEx day only after receiving nonempty normalized rows. Empty responses remain `partial`, including on a later run after a long interruption. Aging an unfinished day into `empty` would reproduce the permanent gap.
2. Select the union of existing gap dates, unsettled bulk-written dates and valid TPEx whole-market `partial`/`error` checkpoint dates, subtracting already settled whole-market dates. Accept canonical ISO dates within the TPEx endpoint range and reject future checkpoint dates. Support both existing `period` and legacy `year_month` columns without migrating the database.
3. Preserve old `ok`/`empty` records as settled. Blanket relabeling would erase established observations and can generate a large unreviewed request backlog. Any later repair of legacy empties needs a separate bounded audit and explicit data-operation scope.
4. Keep gaps-only behavior when no checkpoint table exists. Preserve the TPEx upsert path, single-unit commits, lock, throttling, successful-unit budget and failure-limit behavior.

## Risks / Trade-offs

- A genuinely empty newly attempted day can remain retryable indefinitely. Existing explicit date selection, request/time budgets and throttling bound attempts; permanent completion cannot be inferred from a temporary empty response.
- Previously settled legacy empties may still hide missing data. This code fix prevents new stranded dates and leaves historical repair scope explicit.
- A parser or schema change can affect other markets. Regressions cover TPEx recovery, legacy checkpoint shapes and current TWSE/budget behavior without live endpoint calls.

## Migration Plan

Publish an independently reviewed repair PR from latest main. Deployment follows the existing clean-main and writer-idle procedure after owner merge. There is no data migration; rolling back code preserves every stored row and checkpoint.
