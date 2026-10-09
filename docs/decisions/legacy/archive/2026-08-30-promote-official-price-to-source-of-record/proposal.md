# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-30-promote-official-price-to-source-of-record/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-30-promote-official-price-to-source-of-record/proposal.md).
No production action or acquisition is authorized by this record.

## Why

`price_daily.parquet` is Shioaji-derived and starts **2018-12-07**. The official artifact
covers **2010-01-04** for both markets, verified complete on 2026-08-29, and the
corporate-action index now reaches 2003 (TWSE) and 2008 (TPEx) — so for the first time the
extra history can actually be adjusted rather than merely stored. 1,618 of 2,069 symbols
would gain more than a year; the median gain is about **8.9 years**.

The gate this change closes was set by `add-official-daily-price-source`: promote official
only once (a) the backfill has the depth needed, and (b) the volume seam has an explicit
handling decision. (a) has held since 2026-08-29. (b) is decided here, from measurement
rather than assumption.

**Close is not the problem.** Across 3.28M overlapping rows the median |close difference|
is **0.0000%**, and closes disagree on only 321 rows in the whole overlap — 274 of them on
2020-03-23/24, the COVID limit-down days, where the official value is the correct one.

**Volume is, and it is not one seam but two.** Measured as official ÷ Shioaji:

| segment                                  | median ratio | exactly equal |
| ---------------------------------------- | ------------ | ------------- |
| TPEx backfilled (2010-01-04..2026-08-06) | **1.0000**   | 92–94%        |
| TPEx nightly (2026-08-07 onward)         | 1.0180       | 4%            |
| TWSE backfilled (2010-01-04..)           | 1.0074       | 4.5%          |

TPEx's backfilled volume is the round-lot session and matches Shioaji share for share.
TWSE's `STOCK_DAY` includes odd-lot trading, and that share **grows monotonically from
+0.24% in 2018 to +1.46% in 2026** as odd-lot trading grows — a drift, not an offset.
There is no round-lot-only TWSE source with history, so a single "round lot" definition
across both markets is not obtainable.

**The TPEx seam is live and permanent unless this change fixes it.** The backfill selects
only dates that have TWSE rows and no TPEx rows, so a day the nightly fetch already wrote
is never a gap and is never re-fetched. Verified: TPEx rows are backfill-written through
2026-08-06 and nightly-written every day from 2026-08-07, with `load_tpex_gap_dates()`
returning zero. Left alone, every TPEx symbol shows a one-day **+1.8% volume step on
2026-08-07** that a volume-surge screen reads as a market-wide event.

## What Changes

- **Build `price_daily.parquet` from the official artifact**, falling back to the
  Shioaji-derived `{code}_day.csv` per (code, date) where official has no row. Column
  names and units are unchanged; `Volume` stays in **lots**, as a float, because official
  publishes shares and the odd-lot-inclusive TWSE figures do not divide evenly by 1000.
- **Add a `Source` column** (`official` / `shioaji`) so a consumer, and a stored result,
  can say where each row came from without reverse-engineering it from `raw_fields_json`.
- **Compute SMA/EMA in `build_price_parquet.py`.** Today it copies columns
  `convert_stock_price.py` already wrote; building from official means computing
  5/10/20/60 SMA and EMA over the merged series.
- **Close the forward TPEx seam**: the backfill must re-fetch a TPEx day the nightly bulk
  wrote, so TPEx volume stays one definition — round-lot — through time. One extra request
  per trading day.
- **Accept the cross-market volume difference and document it.** TWSE stays
  odd-lot-inclusive and drifting; TPEx becomes uniformly round-lot. Internal consistency
  through time is preserved for each market, which is what a momentum or volume-surge rule
  actually depends on; the residual is a slowly growing ~1% level difference between
  markets, which is immaterial against liquidity spreads of two orders of magnitude.

## Capabilities

### Modified Capabilities

- `price-data-pipeline`: the consolidated cache is built from the official artifact with a
  Shioaji fallback, carries a `Source` column, and computes its own moving averages.
- `official-price-source`: the backfill re-fetches whole-market days a bulk fetch wrote,
  so one market-day has one volume definition regardless of which fetcher reached it first.

## Non-Goals

- **No round-lot reconstruction for TWSE.** `TWT53U` is dated and would allow
  `STOCK_DAY - TWT53U`, but only from 2020-10-26 (intraday odd-lot's start), leaving a
  ~0.3% residual from after-hours odd-lot before that, in exchange for a new per-day
  crawler over ~1,450 days. Not worth a ~1% correction; recorded so it is not rediscovered.
- **No change to minute-bar acquisition.** Shioaji remains the only intraday source and
  the only source for the 50 symbols official lacks.
- **No change to any study's default date window.** This change makes deeper history
  available; choosing to use it is a per-study decision in the consuming repository.
- **No change to corporate-action or adjustment math.**

## Impact

- **Stored backtest results are not comparable across this change.** TPEx volume is
  unchanged for dates through 2026-08-06 and _reverts_ to round-lot after it; TWSE volume
  rises by 0.24%–1.46% depending on the year. Closes are effectively unchanged.
- **Bar-count eligibility filters change which symbols they admit.** A whole-history rule
  such as "at least 1750 bars" admits a very different set once most symbols start 2010
  instead of 2018-12. `tasks/20260823-rotation-universe/eligibility.py` in the consuming
  repository implements the per-date form and is the reference for what must be re-checked.
  This is the one effect that changes _which_ stocks a study looks at rather than by how
  much a number moves.
- **Moving averages near the old start date stop being NaN.** Rows around 2018-12-07
  currently have no SMA60 because nothing precedes them. With 2010–2018 behind them they
  get real values, so a filter written as `SMA60.notna()` silently changes behaviour.
- **The 50 symbols official does not cover stay Shioaji-only**, including `001`, the market
  index, for which no official daily series exists. The artifact will therefore have most
  symbols starting 2010 and 50 starting 2018-12; that is intended, not a bug.
- File size grows roughly with the extra history (about 3.5M rows today).
