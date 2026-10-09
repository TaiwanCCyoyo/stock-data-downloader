# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-26-add-institutional-data-pipeline/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-26-add-institutional-data-pipeline/design.md).
No production action or acquisition is authorized by this record.

## Context

TWSE T86 and TPEx dailyTrade are dated whole-market reports, so the natural fetch and
checkpoint unit is `(market, date)`, not `(code, month)`. The source research in
`docs/institutional-sources.md` confirms three TWSE signatures (12/16/19 fields), two TPEx
signatures (16/24 fields), historical floors, non-trading-day behavior, and the TPEx
`tables[]` lookup rule. The existing `shp_utils.official_api` module already owns HTTP
retry/backoff and numeric parsing.

## Goals / Non-Goals

**Goals:**

- Preserve normalized per-category gross/net share counts without flattening away the
  foreign-dealer or dealer self/hedge splits available in newer eras.
- Make every stored observation auditable to a raw response and first-known fetch time.
- Support bounded, throttled, resumable historical collection and a daily incremental path.
- Keep endpoint failures isolated from the remaining `scripts/run_daily.py` steps.

**Non-Goals:**

- No strategy, signal, dashboard, broker order, or downstream-project integration.
- No attempt to infer an official publication time that the source response does not expose.
- No fallback vendor and no destructive rewrite of existing data artifacts.

## Decisions

1. **Detect source eras by exact field signature.** Each known TWSE/TPEx signature maps to a
   named normalizer. Unknown signatures raise before any rows or successful checkpoint are
   written. Date-keyed branching was rejected because transition dates are not authoritative.

2. **Use one additive SQLite artifact.** `institutional_daily` is keyed by `(date, code)` and
   stores `market`, `name`, an optional `instrument_type`, normalized foreign/trust/dealer
   gross and net columns, the three-institution net total, `source`, `schema_signature`,
   nullable `published_at`, and non-null `fetched_at`. Missing source-era splits remain SQL
   `NULL`; zero is reserved for a reported zero. `instrument_type` is enriched from local
   symbol metadata when available and otherwise remains `unknown`.

3. **Treat fetch time as the conservative availability boundary.** The official responses
   expose the report date but not a trustworthy publication timestamp. `published_at` stays
   nullable and `fetched_at` records when this pipeline first observed the response. Raw JSON
   is written before the database transaction and its path relative to the database directory,
   plus SHA-256, is stored in `institutional_fetch_log`.

4. **Checkpoint atomically per market/day.** Rows and the corresponding fetch-log entry are
   committed together. A completed historical no-data response may be cached; an empty
   response inside the daily rolling window is `partial` so an early scheduled run cannot
   permanently suppress a later same-day or prior-day retry. `--force` intentionally refreshes
   a unit.

5. **Keep the crawler bounded and independent.** The fetcher builds inclusive calendar-day
   units for both markets, skips successful checkpoints, enforces a default 2.5-second
   throttle, and accepts request/time budgets. It calls `fetch_json_with_retry` directly and
   does not import the price-history crawler.

6. **Daily orchestration calls one small entry point.** `scripts/run_daily.py` requests a rolling
   seven-calendar-day window for both markets inside its own `try/except`. Completed
   checkpoints are skipped, while a late-published prior-day report remains eligible; any
   failure is logged and later steps continue.

## Risks / Trade-offs

- [Risk] An undocumented schema appears between sampled eras. → Reject the signature loudly,
  retain the raw response, and leave the checkpoint incomplete for a corrected rerun.
- [Risk] A source publishes after the scheduled daily job. → Empty responses inside the daily
  rolling window are partial, not final, and can be retried; `fetched_at` remains the honest
  availability time.
- [Risk] Current symbol metadata cannot classify a historical ETF or delisted security.
  → Preserve the row with `instrument_type='unknown'`; acquisition does not depend on the
  classification.
- [Risk] Raw snapshots grow over a full backfill. → Keep one immutable JSON snapshot per
  executed market/day request; bounded runs and cached checkpoints prevent redundant growth.

## Migration Plan

The change is additive. Deploy the module and orchestration call, run a small range first,
then resume larger backfills with request/time budgets. Rollback removes the orchestration
call; the standalone SQLite and raw snapshots can remain readable and do not affect other
artifacts.

## Open Questions

None blocking. A future source that exposes an authoritative publication timestamp may
populate `published_at` without changing the table contract.
