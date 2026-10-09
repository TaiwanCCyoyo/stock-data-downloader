> Historical source report retained for review; measurements are not current coverage guarantees. Publication attribution/terms classification is pending, not a finding of infringement.

# TPEx (上櫃) Dated History Endpoints — Source Endpoint Research

Findings for the two open TPEx gaps in this repository: official daily price
history (`backfill_official_history.py` is TWSE-only) and corporate actions
(`fetch_corporate_actions.py` is TWSE-only). Gathered by directly probing the
endpoints; every response referenced below is saved verbatim under
`data/raw/tpex_probe/` (gitignored, not part of any commit's tracked content —
see that directory for raw JSON if a claim here needs re-checking).

Mirrors the sourcing-note convention used by `docs/institutional-sources.md`.

## What this revises

`docs/decisions/legacy/archive/2026-08-07-add-official-daily-price-source/design.md`
records "TPEx single-symbol daily-history endpoint — no such endpoint exists",
resolved against **TPEx's OpenAPI swagger catalogue (225 paths)**. That
conclusion was correct for what it examined, and it said so at the time: _"TPEx's
legacy site may have one, but that's outside the 'official OpenAPI' scope this
change is built on."_

The scope was the limit, not the finding. TPEx's main site serves its own JSON
API on the same host — the one its public tables are built on, and the one
`docs/institutional-sources.md` already adopted for institutional flow. Every
gap listed above has a dated endpoint there.

## How these endpoints were found (repeatable, no guessing)

Each public TPEx table page is a shell whose inline script names its own API
action:

```javascript
tables.init({ pattern: API_PATTERN, action: "afterTrading/otc", ... });
```

and `/rsrc/js/main.js` defines `API_PATTERN = "/www/{LANG}/{ACTION}"`. So the
endpoint for any table on the site is `https://www.tpex.org.tw/www/zh-tw/` plus
that page's `action`, and the query parameters are the `name=` attributes of the
form with `id="tables-form"` on the same page. `/data/menu/zh-tw/menu.json`
lists all 485 pages, so the full surface can be enumerated rather than guessed.

Request parameters are the form field names plus `response=json`. Date inputs
carry `data-start` (the picker's floor, a useful first estimate of data
availability) and `data-format` (`D` = a single day, `M` = a month).

## Endpoint summary

| Need                         | Action                      | Date parameters         | Picker floor | Confirmed           |
| ---------------------------- | --------------------------- | ----------------------- | ------------ | ------------------- |
| Whole-market daily quotes    | `afterTrading/otc`          | `date` (one day)        | 2007-07-01   | yes                 |
| Per-symbol daily history     | `afterTrading/tradingStock` | `code` + `date` (month) | 1994-01-01   | yes                 |
| Ex-rights / ex-dividend calc | `bulletin/exDailyQ`         | `startDate` + `endDate` | 2008-01-02   | yes                 |
| Capital reduction, resumed   | `bulletin/revivt`           | `startDate` + `endDate` | 2013-01-01   | yes                 |
| ETF split, resumed           | `bulletin/etfSplitRslt`     | `startDate` + `endDate` | 2021-06-01   | responds, no events |
| ETF reverse split, resumed   | `bulletin/etfRvsRslt`       | `startDate` + `endDate` | 2021-06-01   | responds, no events |

Dates are passed as `YYYY/MM/DD` (Gregorian). Dates _inside_ responses are ROC
(`115/08/19`, `1140113`) — the same split the institutional endpoints show.

## The test that matters: does `date` change the payload?

A path that returns 200 with plausible rows can still be a latest-day snapshot
ignoring the parameter — that is exactly what disqualified TPEx's
`tpex_3insti_*` OpenAPI paths in the institutional research. Every endpoint
below was probed at multiple dates and checked for _different_ content, not
merely a successful response.

## `afterTrading/otc` — whole-market daily quotes

**A `type` filter is required.** Without it the response is `stat: "ok"` with
zero rows — a silent empty, not an error. The values match the institutional
endpoint's `sect` convention:

| `type` | Meaning                    | Rows on 2026-08-19 |
| ------ | -------------------------- | ------------------ |
| `AL`   | 所有證券                   | 10,602             |
| `EW`   | 所有證券(不含權證、牛熊證) | 1,012              |

`AL`'s 10,602 is exactly the row count the nightly OpenAPI bulk fetch stored for
TPEx that day, so **`type=AL` reproduces the same universe already in
`official_daily_price`** — a backfill would fill gaps, not introduce a different
population. 40 further `type` values select single industries.

**Date is honoured, verified across nine trading days plus two negatives:**

| `date`     | rows  | `len(fields)` | note                           |
| ---------- | ----- | ------------- | ------------------------------ |
| 2007-07-02 | 826   | 15            | first trading day at the floor |
| 2010-01-05 | 1,257 | 15            |                                |
| 2012-01-05 | 605   | 15            |                                |
| 2015-03-10 | 684   | 15            |                                |
| 2018-01-05 | 752   | 15            |                                |
| 2021-01-05 | 892   | 17            |                                |
| 2023-01-05 | 907   | 17            |                                |
| 2026-08-06 | 1,012 | 17            | different prices from 08-19    |
| 2026-08-19 | 1,012 | 17            |                                |
| 2026-08-09 | 0     | 17            | Sunday                         |
| 2007-06-01 | 0     | 17            | before the floor               |

`len(fields)` is recorded here as it was probed, and it is not enough to tell
the eras apart — see the correction below.

Both 2026 dates return 1,012 rows, so row count alone would not have told them
apart. Their payloads were compared by content hash and differ, with a spot check
on 00679B confirming real price movement (26.66 on 08-06 vs 25.98 on 08-19).

**Volume and value cover the round-lot session only.** Every one of the 10,602
`type=AL` volumes for 2026-08-19 is an exact multiple of 1,000 — zero exceptions —
so the underlying figure is 張, presented in shares. Compared against the same day
already stored from the nightly OpenAPI bulk fetch:

| code   | site vol / bulk vol | site value / bulk value |
| ------ | ------------------- | ----------------------- |
| 3105   | 0.9796              | 0.9796                  |
| 6488   | 0.9337              | 0.9338                  |
| 4966   | 0.9335              | 0.9333                  |
| 8069   | 0.9845              | 0.9843                  |
| 00679B | 0.9972              | 0.9972                  |

Volume and value are scaled by the _same_ factor to four decimal places, which
rules out rounding (that would move volume alone) and points at a consistently
excluded component: odd-lot (零股) trading, which is under 1,000 shares per trade
by definition and would break the multiple-of-1,000 property. The OpenAPI bulk
includes it; this endpoint does not.

Open, high, low and close are identical between the two sources to the cent, and
the code sets match exactly — 10,602 codes each, with an empty symmetric
difference — so this is a difference in what is counted, not in which
instruments are covered or at what prices they traded.

**This endpoint is the one that agrees with Shioaji.** Checked against
Shioaji-derived daily bars for five OTC symbols across 2026-07-01 and 2026-07-02,
every close matches to the cent and every volume to the share — 0.00% difference.
For 3105 on 2026-08-19 this endpoint and Shioaji both report 17,582,000 while the
OpenAPI bulk reports 17,947,311. So the round-lot figure is not a lesser measure
to be tolerated: it is the one consistent with the rest of the pipeline, and the
nightly feed is the outlier.

**Three schema eras, all fully named.** The table above records `len(fields)`,
and that is precisely what hid the middle one: the two 17-field eras have the
same shape and differ only in how they label a column. Corrected 2026-08-23,
after a crawl failed on every trading day in the middle range and on none
outside it, which pins both boundaries to the day:

| era      | span                     | distinguishing labels               |
| -------- | ------------------------ | ----------------------------------- |
| 15-field | 2007-07-02 .. 2020-04-29 | no last bid/ask size columns at all |
| 17-field | 2020-04-30 .. 2025-01-09 | `最後買量(千股)`, `最後賣量(千股)`  |
| 17-field | 2025-01-10 .. current    | `最後買量(張數)`, `最後賣量(張數)`  |

The 15-field era is
`代號, 名稱, 收盤, 漲跌, 開盤, 最高, 最低, 成交股數, 成交金額(元), 成交筆數, 最後買價, 最後賣價, 發行股數, 次日漲停價, 次日跌停價`;
the 17-field eras insert a size column after each of `最後買價` and `最後賣價`.

千股 and 張數 name the same unit, so the change is cosmetic — and the columns it
touches are ones a price backfill never reads. `成交股數` stays in shares across
all three eras: sampled against this repository's Shioaji bars on 2021-03-10,
2023-04-06 and 2024-12-30, seventeen of eighteen symbol-days matched to the
share and the eighteenth to 0.09%, with every stored volume a multiple of 1,000.

Two lessons worth carrying to the institutional endpoints, which have their own
era structure:

- **Record signatures, not field counts.** A probe that stores `len(fields)`
  cannot distinguish eras that differ by a label, and will report full coverage
  while leaving a hole. Applied here before trusting the pre-2020 range: eight
  dates spread from 2013-09-02 to 2020-02-17 were re-probed by field-name
  signature and all eight are the 15-field era, so nothing else is hiding in the
  span the table above covers only by count.
- **Failing loudly is what made this cheap.** The crawler raised on the
  unrecognized signature rather than mapping positionally, so 1,147 trading days
  were recorded as `error` and re-queued instead of being written wrong. The
  cost was two wasted nights; the alternative was silently corrupt history.

Neither boundary should be branched on by date in code. A parser selects its
field mapping by comparing the response's field-name signature against a known
table and fails loudly on an unrecognized one; the spans above exist to document
what has been observed, not to be tested against. That principle and its
rationale are stated once in `docs/institutional-sources.md`; it applies
unchanged here.

Field names carry stray whitespace and embedded markup (`最後買量<br>(張數)`),
so signature comparison must normalize before matching.

## `afterTrading/tradingStock` — per-symbol month history

`code=3105&date=2019/03/01` returns 20 rows, one per trading day of that month:

```
fields: 日 期, 成交仟股, 成交仟元, 開盤, 最高, 最低, 收盤, 漲跌, 筆數
row:    108/03/04, 13,313, 2,372,399, 177.00, 180.50, 174.00, 180.00, 5.00, 8,046
```

Volume is in **thousands of shares (仟股) and thousands of NTD (仟元)**, unlike
`afterTrading/otc`, which reports 成交股數 in shares. Any consumer writing into
`official_daily_price` must convert, or it will silently store volumes 1000x too
small — that artifact's documented unit is shares.

This is the per-symbol shape the price-side backfill already uses for TWSE
(`(code, year_month)` crawl units), so it is a structural drop-in. But for
filling gaps in an existing whole-market artifact, `afterTrading/otc` is the
better unit: one request per missing _day_ rather than one per symbol-month.

## `bulletin/exDailyQ` — ex-rights / ex-dividend calculation results

The TPEx counterpart of TWSE's `twt49u`, the source `fetch_corporate_actions.py`
already normalizes.

| Range                    | rows                 |
| ------------------------ | -------------------- |
| 2025-01-01 .. 2025-12-31 | 1,250                |
| 2020-01-01 .. 2020-12-31 | 940                  |
| 2021-06-01 .. 2026-08-20 | 6,081                |
| 2005-01-01 .. 2005-12-31 | 0 (before the floor) |

Multi-year ranges are accepted — the 5-year probe returned 6,081 rows — so the
range is not silently capped.

**Fields (21):** `除權息日期, 代號, 名稱, 除權息前收盤價, 除權息參考價, 權值, 息值, 權值+息值, 權/息, 漲停價, 跌停價, 開始交易基準價, 減除股利參考價, 現金股利, 每仟股無償配股, 現金增資股數, 現金增資認購價, 公開承銷股數, 員工認購股數, 原股東認購股數, 按持股比例仟股認購`

This maps onto the existing TWSE `twt49u` normalizer almost one-to-one —
`除權息前收盤價` → `previous_close`, `除權息參考價` → `reference_price`,
`權/息` → `action_type`, `權值+息值` → `value_plus_dividend`, `現金股利` →
`cash_dividend_estimate` — so `price_factor = reference_price / previous_close`
and the existing `TWSE_REFERENCE_PRICE_RATIO` factor method carry over unchanged.

**Gotcha: an empty response returns a _different_, older field list** (22 fields,
including `員工紅利轉增資`, which the populated 2020 and 2025 responses do not
have). Schema era must never be inferred from a response with no rows.

## `bulletin/revivt` — capital reduction, resumed-trading reference price

The TPEx counterpart of TWSE's `twtauu`. 2025 returned 10 rows.

**Fields (11):** `恢復買賣日期, 股票代號, 名稱, 最後交易日之收盤價格, 減資恢復買賣開始日參考價格, 漲停價格, 跌停價格, 開始交易基準價, 除權參考價, 減資原因, 詳細資料`

`減資原因` carries the event subtype (`彌補虧損`, i.e. loss-offset) that the
existing classifier uses to split `CASH_CAPITAL_REDUCTION` from
`LOSS_OFFSET_CAPITAL_REDUCTION`. `詳細資料` is an embedded HTML table, not a
value — parse the named columns, not that field.

The reduction factor is `減資恢復買賣開始日參考價格 / 最後交易日之收盤價格`, the
same ratio shape as TWSE's, and it is `> 1` for a reduction (39.18 / 21.55 in the
sampled row), matching what the engine already expects.

## `bulletin/etfSplitRslt` and `bulletin/etfRvsRslt` — ETF (reverse) split

Both respond `stat: "ok"` with the correct 9-field header
(`恢復買賣日期, 證券代號, 證券名稱, 最後交易日之收盤價格, 恢復買賣開始參考價, 漲停價格, 跌停價格, 開始交易基準價, 詳細資料`)
and **zero rows across the whole available window** (2021-06-01 to 2026-08-20).

The `exDailyQ` control above rules out a silent range cap, so the most likely
reading is that no TPEx-listed ETF has split or reverse-split since these pages'
data floor. That cannot be distinguished from "these events are reported
somewhere else" without a known TPEx ETF split date to test against. Treat the
endpoints as wired-but-unproven, and do not treat an empty result here as a
confirmed zero.

## Holiday vs before-floor: still indistinguishable

As with the institutional endpoints, TPEx returns `stat: "ok"` with an empty
`data` for a holiday (2026-08-09, a Sunday) _and_ for a date before the data
floor (2007-06-01). There is no distinguishing `stat` string of the kind TWSE
provides. A TPEx backfill loop cannot tell those apart from the response alone;
it needs an external trading calendar, or must accept that some dates
legitimately have zero rows without knowing why.

## Rejected / not useful for history

- `bulletin/prePost` (除權除息預告表) and `bulletin/decap` (減資預告表) — no date
  parameters on their forms; they are forward-looking announcement boards, not
  history queries. Useful for knowing about an upcoming event, useless for
  backfill.
- TPEx's OpenAPI paths (`openapi/v1/tpex_*`) — no date parameter, latest day
  only. Already rejected for institutional data in
  `docs/institutional-sources.md`; the same limitation is what led the price-side
  backfill to conclude TPEx history was unavailable.

## Probe budget

About 40 requests total across discovery and probing (17 page/asset fetches, 23
endpoint probes), with a 2.5s throttle on every probe. No rate limiting was
observed, but no ceiling was measured either — treat 2.5s as a safe starting
point, not a verified limit, exactly as the institutional research did.
