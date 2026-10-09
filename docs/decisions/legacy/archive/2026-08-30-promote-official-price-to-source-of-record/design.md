# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-30-promote-official-price-to-source-of-record/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-30-promote-official-price-to-source-of-record/design.md).
No production action or acquisition is authorized by this record.

# Design — promote-official-price-to-source-of-record

Every number here was measured on 2026-08-29 against the live artifact, not assumed. The
measurement is the whole point: the gate this change closes was left open in
`add-official-daily-price-source` precisely because nobody had quantified the volume seam.

## What the overlap actually looks like

3,282,752 rows where the official artifact and the Shioaji-derived cache cover the same
(code, date), 2018-12-07 onward.

### Close: not a problem

Median |close difference| is **0.0000%**, and the difference exceeds 0.5% on 0.00%–0.20%
of days depending on segment. Over the whole overlap only **321 rows** disagree at all,
**274 of them on 2020-03-23 and 2020-03-24** — the COVID limit-down days, where Shioaji's
minute bars miss the closing call auction and the official close is the correct one.

Any argument for keeping Shioaji as the source of record has to rest on volume. It does
not have a price leg.

### Volume: two seams, not one

`official ÷ shioaji`, where the Shioaji column is in lots and official in shares:

| segment                                       | rows      | median     | exactly equal | official > shioaji |
| --------------------------------------------- | --------- | ---------- | ------------- | ------------------ |
| TPEx backfilled, 15-field era (..2020-04-29)  | 245,337   | **1.0000** | 91.8%         | 1.3%               |
| TPEx backfilled, 17-field eras (..2026-08-06) | 1,218,405 | **1.0000** | 94.1%         | 0.7%               |
| TPEx **nightly** (2026-07-23..)               | 15,630    | **1.0180** | 4.4%          | 95.6%              |
| TWSE backfilled (2018-12-07..2026-08-24)      | 1,800,137 | **1.0074** | 4.5%          | 93.8%              |
| TWSE nightly (2026-08-25..)                   | 3,243     | 1.0146     | 1.4%          | 98.6%              |

**TPEx's history endpoint publishes the round-lot session and agrees with Shioaji share for
share.** TWSE's `STOCK_DAY` includes odd-lot trading and does not.

### The TWSE difference is a drift, not an offset

Median ratio by year:

| year | TPEx   | TWSE   |
| ---- | ------ | ------ |
| 2018 | 1.0000 | 1.0024 |
| 2019 | 1.0000 | 1.0034 |
| 2020 | 1.0000 | 1.0029 |
| 2021 | 1.0000 | 1.0054 |
| 2022 | 1.0000 | 1.0064 |
| 2023 | 1.0000 | 1.0087 |
| 2024 | 1.0000 | 1.0108 |
| 2025 | 1.0000 | 1.0126 |
| 2026 | 1.0000 | 1.0146 |

Six-fold growth, monotonic, tracking the growth of odd-lot trading (intraday odd-lot opened
2020-10-26; after-hours odd-lot predates the window and explains the ~0.24% floor). TPEx is
1.0000 in every single year — which is what makes it credible that the difference is a
definition and not noise.

## Decision: accept official's definition; do not reconstruct round lots for TWSE

A single round-lot definition across both markets is **not obtainable**. `STOCK_DAY` is the
only TWSE source reaching 2010 and it is odd-lot-inclusive.

`TWT53U` (盤中零股交易) _is_ dated — `rwd/zh/afterTrading/TWT53U?date=YYYYMMDD` returned
1,363 whole-market rows for 2026-08-27 — so `STOCK_DAY − TWT53U` could reconstruct round
lots. Rejected: it only works from 2020-10-26, leaves a ~0.3% after-hours-odd-lot residual
before that, and costs a new per-day crawler over ~1,450 days, all to correct about 1%.
Recorded so it is not rediscovered as a fresh idea.

What is kept instead is the property that actually matters: **each market is internally
consistent through time.** A momentum or volume-surge rule compares a symbol to its own
past, so a constant-definition series matters far more than agreement between exchanges.
The residual is a level difference between markets that grows to ~1.5%, against liquidity
spreads of two orders of magnitude.

## The seam that had to be fixed first, and why it is not historical

`official_daily_price` has PK `(market, code, date)`, so the nightly bulk fetch and the
backfill overwrite each other and only `raw_fields_json`'s field signature says which one
won. Six signatures exist:

| #   | market | fields       | span                   | writer                      |
| --- | ------ | ------------ | ---------------------- | --------------------------- |
| 1   | TWSE   | 10 (Chinese) | 2010-01-04..2026-08-24 | backfill `STOCK_DAY`        |
| 2   | TPEx   | 15 (Chinese) | 2010-01-04..2020-04-29 | backfill `afterTrading/otc` |
| 3   | TPEx   | 17, `(千股)` | 2020-04-30..2025-01-09 | backfill                    |
| 4   | TPEx   | 17, `(張數)` | 2025-01-10..2026-08-06 | backfill                    |
| 5   | TPEx   | 18 (English) | 2026-07-23..2026-08-28 | **nightly bulk**            |
| 6   | TWSE   | 11 (English) | 2026-08-25..2026-08-27 | nightly bulk                |

TPEx rows are backfill-written through **2026-08-06** and nightly-written **every day from
2026-08-07**. And `load_tpex_gap_dates()` returns **zero** gaps, because it selects dates
that have TWSE rows and _no_ TPEx rows — a day the nightly fetch already populated is not a
gap and will never be re-fetched.

So without this change TPEx volume changes definition at 2026-08-07 and stays changed:
every TPEx symbol shows a **+1.8% one-day volume step** that a volume-surge screen reads as
a market-wide event, on the live edge of the data where decisions are made.

TWSE has no equivalent problem. Its backfill unit is (code, month) ending at the last
_completed_ month, so the current month is re-fetched once it closes and self-heals.

The fix is one extra request per trading day: make a bulk-written TPEx day eligible for
re-fetch. That also delivers, for TPEx alone, the uniform round-lot series that was the
original hope for both markets.

## Merge shape

Official is the source of record; Shioaji fills what official does not cover.

- **`Volume` stays in lots and becomes a float.** Official publishes shares; TWSE's
  odd-lot-inclusive figures do not divide evenly by 1000. The unit must not change — it is
  documented as lots and has already produced a silently wrong answer once.
- **A `Source` column** records `official` or `shioaji` per row. It makes the merge and the
  seam inspectable from the artifact instead of from `raw_fields_json`, and lets a stored
  result be explained after the fact.
- **50 symbols stay Shioaji-only**, `001` among them — there is no official daily series
  for the market index. The artifact will have most symbols starting 2010 and 50 starting
  2018-12. That is intended; a future reader must not take it for a bug, which is why it is
  a spec scenario rather than a comment.
- **Moving averages are computed here for the first time.** `build_price_parquet.py`
  currently copies columns `convert_stock_price.py` wrote. They must be computed per symbol
  over the merged series, and must not bleed across symbol boundaries.

## Consequences worth stating plainly

**Bar-count eligibility changes which symbols a study admits.** A whole-history rule like
"at least 1750 bars" admits a very different set once most symbols start 2010 rather than
2018-12. This is the only effect here that changes _which_ stocks get looked at rather than
by how much a number moves, and given the objective is picking runaway candidates from a
qualified pool, it is the one to check first. The consuming repository's
`tasks/20260823-rotation-universe/eligibility.py` implements the per-date form that is
robust to this; whole-history filters are not.

**Moving averages near 2018-12-07 stop being NaN.** Rows there currently have no SMA60
because nothing precedes them. With 2010–2018 behind them they get real values, so a filter
written as `SMA60.notna()` changes behaviour silently rather than numerically.

**Stored results are not comparable across this change.** TPEx volume is unchanged through
2026-08-06 and reverts to round-lot after it; TWSE volume rises 0.24%–1.46% by year; closes
are effectively unchanged.
