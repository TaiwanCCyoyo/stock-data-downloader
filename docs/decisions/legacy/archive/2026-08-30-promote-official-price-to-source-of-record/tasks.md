# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-30-promote-official-price-to-source-of-record/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-30-promote-official-price-to-source-of-record/tasks.md).
No production action or acquisition is authorized by this record.

# Tasks

Measurement evidence for every claim below: `design.md`, taken from live probes on
2026-08-29.

## 1. Close the forward TPEx seam first

- [x] 1.1 A **settled checkpoint** is the signal: a TPEx day with rows but no `ok`/`empty`
      row in `official_history_raw_ranges` was written by the nightly bulk, not by this
      crawler. Chosen over the stored response's field signature because it needs no JSON
      parsing, no schema change, and does not break when the endpoint changes shape again
      -- and that table already exists to record exactly "this crawler covered this day".
      Verified against the live artifact: 4,082 TPEx dates with rows, 4,064 with an `ok`
      checkpoint, 18 without, 0 checkpoints without rows.
- [x] 1.2 `load_tpex_gap_dates` is now `load_tpex_dates_to_fetch` and returns gaps plus
      bulk-written days. It falls back to gaps only when the checkpoint table does not
      exist yet, so a fresh database is not queued for a full re-crawl off a bare
      existence check. **Also fixed a trap the re-fetch would otherwise have walked into**:
      the TPEx branch hardcoded `complete = True`, so an empty answer settled as `empty`
      and was never retried. That is right for a gap day but wrong for a re-fetch, which
      can legitimately run before the history page has published -- the daily backfill
      fires at 18:25 -- and would have stranded today's date on the bulk feed's figures
      permanently, every day. `complete` is now `bool(rows) or not already_had_rows`.
- [x] 1.3 Re-fetched all 18 days (2026-07-23, 07-24, and 08-07..08-28). Verified:
      `load_tpex_dates_to_fetch` returns 0, all 18 carry an `ok` checkpoint, and
      2026-08-28's stored response is the 17-field Chinese history shape rather than the
      18-field English bulk one. **The proof is the volume**: against the Shioaji-derived
      cache those days went from median ratio 1.0180 with 4.4% exactly equal, to
      **median 1.0000 with 99.7% exactly equal** across 15,630 rows. The rows about to be
      overwritten were exported to `.tmp/tpex_bulk_rows_before_refetch_20260830.json.gz`
      first (188,275 rows), since the bulk feed serves only the latest day and could not
      have reproduced them.
- [x] 1.4 Four tests: a bulk-written day is selected; a day this crawler settled as `ok`
      or `empty` is not; an empty answer on a day we already hold rows for stays `partial`
      and remains eligible; an empty answer on a genuine gap day still settles as `empty`.
      The last pair is the important one -- a guard written as "empty never settles" would
      pass the first three and cost a request a day forever.

## 2. Build the cache from official

- [x] 2.1 Read the official artifact into the parquet builder, keyed (market, code, date),
      converting volume from shares to lots as a float.
- [x] 2.2 Merge per (code, date) with the Shioaji `{code}_day.csv` rows, official winning,
      and add the `Source` column.
- [x] 2.3 Compute SMA/EMA 5/10/20/60 over the merged per-symbol series.
- [x] 2.4 Keep the existing column names, order and dtypes; `Volume` in lots.
- [x] 2.5 Extend the summary log line with per-source row counts and the earliest date, so
      a run says how much history it actually produced.
- [x] 2.6 **Not in the original plan.** `run_daily.py` rebuilt the parquet at step 4 and
      fetched official prices at step 5. Harmless while the cache was Shioaji-derived and
      wrong the moment it is not: the newest bar would be built before this run's official
      row existed, so it would land Shioaji-sourced and flip to official on the next run --
      a volume-definition change on the freshest bar of every affected symbol, once a day,
      exactly where a volume-surge rule looks. The same failure as the TPEx seam, one layer
      up. The two steps are swapped and the ordering has its own spec scenario and test.

## 3. Tests

- [x] 3.1 A symbol present in both sources takes official values and `Source == "official"`.
- [x] 3.2 A symbol official does not cover (e.g. `001`) is still present, Shioaji-sourced.
- [x] 3.3 Volume is converted shares -> lots and stays a float.
- [x] 3.4 Moving averages are computed per symbol and do not bleed across symbols.
- [x] 3.5 A day present in Shioaji but not official (and vice versa) resolves as specified.

## 4. Validation

- [x] 4.1 `uv run python -m pytest tests/ -q`
- [x] 4.2 `uv run pre-commit run --files <changed files>`
- [x] 4.3 Rebuild and compare against the current parquet: row count, symbol count, date
      range, and the per-year volume ratio, which must reproduce the measured
      1.0024..1.0146 TWSE drift and 1.0000 for TPEx through 2026-08-06.
- [x] 4.4 Confirm `001` and the other 49 official-less symbols survive with Shioaji rows.
- [x] 4.5 Quantify the eligibility change: how many symbols pass a 1750-bar whole-history
      filter before and after, so the consuming repository knows what moved.
- [x] 4.6 Record the comparability boundary in the change's completion notes.

## Validation notes

**4.1** 279 passed (was 271). **4.2** clean.

**4.3 shape.** 3,465,179 rows -> **6,529,072**; 1,876 dates -> **4,082**; symbols unchanged at
2,070; range 2018-12-07..2026-08-28 -> **2010-01-04..2026-08-28**. One column added
(`Source`), none lost. By source: 6,346,645 official / 182,427 shioaji.

The volume ratio reproduces the design probe, which is what says the merge took the values
it was supposed to: TPEx **1.0000 in every year**, TWSE 1.0021 (2018) rising to 1.0143
(2026) against a predicted 1.0024..1.0146. Close agreement holds at a median |difference|
of 0.0000%, above 0.5% on 0.018% of rows.

**4.4** Exactly **50** symbols are shioaji-only, `001` among them with its 1,844 bars.
**Zero symbols present before are missing now.**

**4.5 eligibility -- the consequence that changes which stocks a study sees.**

| whole-history filter | before | after     |
| -------------------- | ------ | --------- |
| >= 750 bars          | 1,910  | 1,929     |
| >= 1,750 bars        | 1,600  | **1,722** |
| >= 2,500 bars        | **0**  | **1,483** |

Median bars per symbol went 1,869 -> 3,974. The 2,500-bar row is the one to notice: the old
cache's longest symbol had 1,872 bars, so _any_ threshold above that admitted nothing at
all. A filter written against the old cache may have been silently empty rather than
selective.

**4.5 the NaN boundary.** Over 2018-12-07..2019-03-31, SMA60 was NaN on **88.2%** of rows
and is now NaN on **5.9%**. A filter written as `SMA60.notna()` therefore admits roughly
eight times as many rows over that window as it used to.

**4.6 comparability.** Recorded in `proposal.md` under Impact and carried into the
`market-data-cache` skill in the consuming repository. TPEx volume is unchanged through
2026-08-06 and round-lot after it; TWSE volume rises 0.24%-1.46% by year; closes are
effectively unchanged; and every bar-count filter admits a different set.

## What the 182,427 shioaji rows turned out to be

32,589 belong to the 50 symbols official does not cover. The other **149,838, spread over
307 symbols, are the emerging-board (興櫃) period before each of those stocks listed** --
`symbol_meta.listed_date` matches the date official coverage begins **exactly** (2072
2026-03-26, 4169 2026-04-08, 3485 2026-05-06, and so on). Official TWSE/TPEx daily quotes
cannot cover a stock that has not listed yet, so this is a market boundary rather than a
coverage gap, and preserving it is correct: dropping those rows would delete real trading
history. `Source` is what lets a study exclude the pre-listing period if it wants to.

Chasing that down also turned up a harmless inconsistency worth knowing about:
`official_history_raw_ranges` holds rows with `status='ok'` and `row_count=0`, written
before the `empty` status existed. Both are treated as settled, so behaviour is correct;
only the label is misleading. Left alone rather than rewritten, since rewriting settled
checkpoints to relabel them is exactly the kind of in-place edit the standing data rule
exists to prevent.
