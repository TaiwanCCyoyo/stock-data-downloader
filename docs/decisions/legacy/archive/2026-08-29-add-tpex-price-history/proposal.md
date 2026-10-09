# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-29-add-tpex-price-history/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-29-add-tpex-price-history/proposal.md).
No production action or acquisition is authorized by this record.

## Why

TPEx daily price history is not backfillable today, so every TPEx trading day the
nightly bulk fetch misses is lost permanently. This is not a hypothetical shortfall:
`official_daily_price` holds TPEx rows for 2026-07-23 and 2026-07-24 and no other day
in July 2026, because those are the only two days a nightly run happened to cover.
TWSE has no such hole — `backfill_official_history.py` fills its gaps after the fact —
and the asymmetry means the official-vs-Shioaji cross-check in the data-quality report
is effectively TWSE-only for anything but the last few weeks.

The reason was believed to be that TPEx publishes no dated history. That conclusion
was resolved against TPEx's OpenAPI swagger catalogue, and correctly noted at the time
that the main site was outside the scope it examined. It is: TPEx's own site serves a
dated whole-market daily-quote endpoint on the same host that this repository already
queries for institutional data. `docs/tpex-history-sources.md` records the probe
evidence.

The endpoint covers the same universe the nightly bulk fetch already stores: for
2026-08-19 its `type=AL` code set and the stored TPEx code set are identical — 10,602
each, empty symmetric difference — with open, high, low and close matching to the cent.
Filling gaps adds days to the existing population rather than mixing in a different one.

Volume is the one place they differ, and checking both against this repository's own
Shioaji-derived bars settles which is which: the history endpoint matches Shioaji
exactly, to the share, while the nightly feed runs ~2% higher because it also counts
odd-lot trading. Backfilled days will therefore agree with the cross-check more closely
than nightly days do. See `design.md` Decision 1.

## What Changes

- Extend the historical backfill to TPEx using
  `https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date=YYYY/MM/DD&type=AL&response=json`,
  a whole-market query for one trading day, with data available back to 2007-07-02.
- Crawl TPEx in `(market, date)` units rather than TWSE's `(code, year_month)` units.
  One request covers a whole day for every OTC instrument, so a missing day costs one
  request instead of one per symbol. A whole-market unit is stored with `code = ''` in the
  checkpoint's `year_month` column, renamed to `period`, which then holds `YYYY-MM` for a
  TWSE symbol-month and `YYYY-MM-DD` for a TPEx market-day. See `design.md` Decision 3.
- Select the response field mapping by comparing the returned field-name signature
  against a known table, never by branching on the request date, and fail loudly on an
  unrecognized signature. Three eras exist, all fully named, so nothing is positional:
  15 fields through 2020-04-29, then 17 fields labelled `(千股)` through 2025-01-09, then
  17 fields labelled `(張數)`. The middle one was found only after the first scheduled
  runs failed on all 1,147 of its trading days -- the research probes had recorded field
  counts, which cannot separate two eras of the same shape. Failing loudly is what kept
  that a delay rather than 1,147 days of silently mis-parsed history.
- Select candidate dates from the artifact itself: dates that already have TWSE rows
  (proof the market was open) and no TPEx rows (proof of a gap). TPEx answers a holiday
  and a pre-floor date identically, so a calendar-driven crawler cannot tell them apart;
  a gap-driven one never asks. See `design.md` Decision 2.
- Accept the scope this implies: the no-argument auto mode reaches back only as far as
  TWSE history exists (2013-09-02 today), not the endpoint's 2007-07-02 floor. Earlier
  dates remain reachable by explicit arguments.

## Capabilities

### Modified Capabilities

- `official-price-source`: the historical backfill gains TPEx, and its stated
  backfillable universe stops being TWSE-only.

## Non-Goals

- **No new credential or authenticated call.** These endpoints are public and
  unauthenticated, like every other official source this repository reads.
- **No change to the nightly fetch.** The bulk OpenAPI fetch keeps serving the latest
  trading day; this change only adds a way to fill in days it missed.
- **No schema change to `official_daily_price`.** TPEx rows already exist in that table
  with the same columns; this change adds rows for past dates, not columns. Volume stays
  in shares, as the artifact contract documents -- but backfilled rows count the regular
  round-lot session, so the column gains a second provenance. That is a change in the
  contract's meaning without one in its shape, and is called out under Impact.
- **No per-symbol TPEx crawl.** `afterTrading/tradingStock` also exists and reaches
  further back, but it reports volume in thousands rather than shares and costs one
  request per symbol-month. It is recorded in the research doc as an option, and is
  deliberately not adopted here.
- **No change to which endpoint the nightly fetch uses.** That the history endpoint
  agrees with Shioaji where the nightly one does not is an argument for revisiting the
  nightly source, but it publishes later in the day, so that trade belongs in its own
  change.
- **No attempt to reconstruct pre-2007 history.** The endpoint's floor is the floor.

## Impact

- Downstream artifact contract: `data/official_daily.sqlite`'s `official_daily_price`
  table gains TPEx rows for historical dates. Column shape, units, and the
  `close_missing` convention are unchanged, so a reader written against the current
  contract needs no change — it will simply find days that were previously absent.
- Changed in meaning, not in shape: the `volume` and `value` columns for backfilled TPEx
  rows count the regular round-lot session, about 2% below what the nightly fetch records
  for the same symbol and day, which additionally counts odd-lot trading. A consumer
  comparing volume across a backfilled and a nightly day should expect that step -- and
  should know the backfilled side is the one that matches this repository's Shioaji bars
  exactly. Prices are unaffected.
- The checkpoint table `official_history_raw_ranges` gains TPEx units, and its
  `year_month` column is renamed to `period`. Nothing outside the backfill tool reads it.
- The work is bounded and small. The artifact holds 3,163 TWSE trading days
  (2013-09-02 onward) and **12** TPEx days, so there are 3,152 gap days to fill: about
  1.8 hours at the existing 2s throttle, inside a single scheduled slice. TPEx official
  history goes from twelve days to thirteen years in one run.
