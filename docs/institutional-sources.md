# Institutional (三大法人) Daily Buy/Sell Data — Source Endpoint Research

Findings for `add-institutional-data-pipeline` tasks 1.1/1.2, gathered by directly
probing the endpoints with `shp_utils.official_api.fetch_json_with_retry`. Every
response referenced below is saved verbatim under
`data/raw/institutional_probe/` on branch `research/institutional-endpoints`
(gitignored, not part of any commit's tracked content — see that directory for
raw JSON if a claim here needs re-checking).

Mirrors the sourcing-note convention used by `fetch_corporate_actions.py`.

This is round 2 of this research. Round 1 confirmed the endpoints themselves
and the `(market, date)` backfill design (still accurate, see below). Round 2
replaces round 1's TPEx column-grouping guess — flagged at the time as
_inferred from position, not verified_ — with an officially-labeled,
value-cross-checked mapping, and discovers that TWSE's schema history has
**three** eras, not two.

## Core design principle: detect schema era by field-name signature, not by date

Every schema era on both markets turns out to be either fully self-describing
(TWSE, always) or resolvable to a fully-labeled mapping via a one-time lookup
(TPEx's current era, see below). **A historical backfill parser should
identify which era a response belongs to by comparing its returned
`fields`/column-name signature against a known table of signatures, not by
branching on the request date.** This is strictly more robust than a
date-keyed branch: era transition dates below are confirmed only to
day-level windows (narrowed by whatever probe dates happened to be sampled,
never exhaustively bisected to the exact day), so a date-keyed parser risks
being off by hours-to-months at each boundary. A signature-keyed parser has
no such risk and, as a bonus, fails loudly (unrecognized signature -> raise,
don't guess) instead of silently mis-mapping columns if the vendor ever
changes the schema again outside any window documented here.

## TWSE (listed / 上市)

**Endpoint:** `https://www.twse.com.tw/rwd/zh/fund/T86`
**Query params:** `date=YYYYMMDD&selectType=<code>&response=json`

`https://openapi.twse.com.tw/v1/fund/T86` (the OpenAPI-catalogue host the
original proposal named) returns **404** — that host does not serve this
report. The `www.twse.com.tw/rwd/...` host above is the correct source.

### `selectType` matters — do not use `ALL`

| `selectType` | rows (2026-08-07) | code lengths                            | contents                                                            |
| ------------ | ----------------- | --------------------------------------- | ------------------------------------------------------------------- |
| `ALL`        | 14,193            | 12,999×6-char, 1,076×4-char, 118×5-char | every instrument incl. ~13k warrants (`069152` 上銀凱基5B購03 etc.) |
| `ALLBUT0999` | 1,326             | 1,076×4-char, 132×6-char, 118×5-char    | stocks + ETFs + non-warrant instruments, warrants excluded          |

For a stock-focused institutional fetcher, **use `selectType=ALLBUT0999`**, not
`ALL`. `ALL` inflates row count ~10x with warrant noise that has nothing to do
with the target symbol universe (`codes: "2330,2454"` style, per
`experiment.json`/proposal scope).

### Response shape

```
{"stat": "OK", "date": "20260807", "title": "...",
 "fields": [...N names...], "hints": "單位：股", "data": [[...], ...]}
```

`fields` is **always fully named and self-describing** — no positional
ambiguity has been observed at any TWSE schema era. `stat` distinguishes
success (`"OK"`) from before-floor and holiday responses (see below).

### Three confirmed schema eras — all fully named

A year-by-year signature scan (one probe near Jan 5 of each year,
2013–2025, `data/raw/institutional_probe/twse_yearly_*.json`) combined with
round 1's exact-floor probe (`twse_bisect_20120502.json`) found **three**
distinct field-name signatures, not the two round 1 assumed:

**Era A — 12 fields (confirmed at the exact floor, 2012-05-02):**

```
證券代號, 證券名稱,
外資買進股數, 外資賣出股數, 外資買賣超股數,
投信買進股數, 投信賣出股數, 投信買賣超股數,
自營商買賣超股數, 自營商買進股數, 自營商賣出股數,
三大法人買賣超股數
```

No foreign-investor split, no dealer self/hedge split; dealer reports a
single net figure ahead of its own gross buy/sell.

**Era B — 16 fields (confirmed 2015-01-05, 2016-01-05, 2017-01-05):**

```
證券代號, 證券名稱,
外資買進股數, 外資賣出股數, 外資買賣超股數,
投信買進股數, 投信賣出股數, 投信買賣超股數,
自營商買賣超股數,
自營商買進股數(自行買賣), 自營商賣出股數(自行買賣), 自營商買賣超股數(自行買賣),
自營商買進股數(避險), 自營商賣出股數(避險), 自營商買賣超股數(避險),
三大法人買賣超股數
```

The dealer self-trading/hedging split (`自行買賣`/`避險`) was added; foreign
investor is still a single undifferentiated column group.

**Era C — 19 fields (confirmed 2018-01-05 through 2024-01-05, current):**

```
證券代號, 證券名稱,
外陸資買進股數(不含外資自營商), 外陸資賣出股數(不含外資自營商), 外陸資買賣超股數(不含外資自營商),
外資自營商買進股數, 外資自營商賣出股數, 外資自營商買賣超股數,
投信買進股數, 投信賣出股數, 投信買賣超股數,
自營商買賣超股數,
自營商買進股數(自行買賣), 自營商賣出股數(自行買賣), 自營商買賣超股數(自行買賣),
自營商買進股數(避險), 自營商賣出股數(避險), 自營商買賣超股數(避險),
三大法人買賣超股數
```

The foreign-investor split (`外陸資` excl. foreign dealers vs.
`外資自營商`) was added on top of era B. A naive "foreign/trust/dealer net +
gross" schema does not losslessly represent era C without picking which
sub-split to keep or flattening both — same caveat as round 1, now confirmed
to also apply to era B's dealer split.

**Transition windows** (not bisected further — signature detection makes the
exact day unnecessary, see design principle above):

- Era A -> B: between 2012-05-02 and 2015-01-05 (wide, not narrowed this
  round).
- Era B -> C: between 2017-01-05 and 2018-01-05 (narrowed to about a year by
  the yearly scan).

### Historical range and non-trading days

- **Exact floor: 2012-05-02.** A `date` before this returns
  `stat: "查詢日期小於101年05月02日，請重新查詢!"` (an explicit, distinguishable
  error string) with `data: []`. `date=20120502` itself succeeds (803 rows,
  `selectType=ALLBUT0999`).
- **Non-trading day** (tested on multiple dates, e.g. 2026-08-09 Sunday,
  2013/2014/2019/2020/2025's Jan-5 probes landing on holidays):
  `stat: "很抱歉，沒有符合條件的資料!"`, `data: []`. This string is
  distinguishable from both `"OK"` and the before-floor error, so a fetcher
  can tell "holiday" apart from "before data existed" apart from "success"
  purely from `stat`.

## TPEx (OTC / 上櫃)

**Endpoint:** `https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade`
**Query params:** `type=Daily&sect=<code>&date=YYYY/MM/DD&id=&response=json`
(note: slash-separated date, not TWSE's compact `YYYYMMDD`)

This is a **different, dated, whole-market endpoint** — it directly
contradicts the proposal's inherited assumption (from the price-side
`add-official-daily-price-source` finding) that TPEx has no dated history
endpoint. That finding was specific to _price_ data; it does not hold for
_institutional_ data. TPEx institutional daily history **can** be backfilled.

The `openapi.tpex.org.tw`-style catalogue endpoints (`/v1/tpex_3insti_daily_trading`
and four siblings) were also probed and are **not suitable as a data
source**: no date parameter (latest-day only), and `tpex_3insti_daily_trading`'s
JSON keys are malformed with accidental duplicates/near-duplicates
(`"Foreign Dealers-TotalSell"` vs `"ForeignDealers-Difference"` vs a second,
differently-spaced `"Dealers -TotalSell"` key). Round 1 planned to use this
family as a value-anchored "labeling oracle" for the current TPEx schema if
needed; round 2 did not need it — see below, `response=csv` answered the
question directly and more reliably.

### `sect` matters — do not use `AL`

| `sect` | rows (2026-08-07) | notes                                                                                              |
| ------ | ----------------- | -------------------------------------------------------------------------------------------------- |
| `EW`   | 898               | subset of `AL`; excludes ~3,825 6-digit codes (e.g. `711274`, `706524` — warrant/bond-style codes) |
| `AL`   | 4,723             | superset; includes the warrant/bond-style codes `EW` excludes                                      |

**Use `sect=EW`** for a stock-focused fetcher, matching TWSE's
`ALLBUT0999` choice. (`EW` is confirmed a strict subset of `AL` — every `EW`
code appears in `AL`.)

### Response shape — parsing gotchas

```json
{"columnNum": 25, "tables": [ {...} or {}, {...} or {} ], "template": ..., "date": ..., "stat": "ok"}
```

**`tables` is a list whose length (1 or 2) and the index holding the real
table varies by request** — sometimes the populated table is `tables[0]`,
sometimes `tables[1]`, with the other slot an empty `{}`. **Do not assume a
fixed index.** A correct parser must scan `tables` for the entry that has a
non-empty `"fields"` key:

```python
def real_table(tables):
    for t in tables:
        if t.get("fields"):
            return t
    return {}
```

**`columnNum` is off by one from the actual field/column count — never use
it as a field count.** Verified `columnNum == len(fields) + 1` across all 18
saved TPEx responses that have a populated table (both schema eras, `sect`
values `EW` and `AL`, `rows` ranging from 0 to 923). Also confirmed (round 1)
that `columnNum` on a genuinely empty (`rows == 0`) response is not
meaningful at all and must be ignored regardless of this off-by-one rule.

### Current schema (24 fields): group semantics — now officially confirmed

Round 1 inferred the current era's column grouping from a single day's
position/magnitude pattern and flagged it explicitly as unverified. That
inference turned out to be **wrong on one point**: it had the "含外資自營商"
(combined total) and "不含外資自營商" (excl. foreign-dealer) groups swapped.

**Why the arithmetic-only approach could not have caught this**: the
"外資自營商" (foreign-dealer) sub-group's buy/sell/net columns are
_identically zero in every row of both a 2026-08-07 sample (898 rows) and a
2020-01-02 sample (561 rows)_ — TPEx-listed OTC stocks apparently see no
recorded foreign-dealer proprietary trading at all in these samples. With
that sub-group always zero, the "combined total" group and the "excluding
foreign-dealer" group are numerically identical in every row, so no amount
of arithmetic cross-checking (even across independent days) can tell them
apart — round 2 confirmed this by finding 19 different three-column sum
identities that are all "consistent" for exactly this reason. Purely
arithmetic identity-scanning is **not sufficient** to resolve grouping in a
schema like this; a labeled source is required.

**Resolution**: `response=csv` on the same `dailyTrade` endpoint (same host,
same params, one extra query param, zero discovery cost) returns a CSV
export whose header row carries full compound group labels, e.g.
`外資及陸資(不含外資自營商)-買進股數`. Response body is `cp950`-encoded
(reported by the server as `MS950`), not UTF-8 — must decode explicitly.
Saved as `data/raw/institutional_probe/tpex_recent_EW.csv.raw` (bytes) and
`..._decoded.csv` (UTF-8 text).

**Cross-validated, not just trusted**: every one of the 898 `sect=EW`
2026-08-07 JSON rows was matched by security code against the CSV export and
compared field-by-field (indices 2 onward) — **0 mismatches across all 898
rows**. This is a full, exhaustive, value-anchored confirmation, not a spot
check.

Confirmed current-era (24-field) column groups, in true order:

```
代號, 名稱,
[買進, 賣出, 買賣超]  # 外資及陸資(不含外資自營商)   <- round 1 had this at the WRONG position
[買進, 賣出, 買賣超]  # 外資自營商                    (always 0 in every sampled row)
[買進, 賣出, 買賣超]  # 外資及陸資(合計，含外資自營商)  <- round 1 had this at the WRONG position
[買進, 賣出, 買賣超]  # 投信
[買進, 賣出, 買賣超]  # 自營商(自行買賣)
[買進, 賣出, 買賣超]  # 自營商(避險)
[買進, 賣出, 買賣超]  # 自營商(合計)
三大法人買賣超股數合計
```

The dealer self-trading/hedging/combined ordering round 1 guessed (自行買賣,
避險, then 合計) was correct; only the two foreign-investor groups were
swapped. `fetch_institutional.py` **must** use this order, not round 1's.

The current era's `data` rows remain **positional, not name-keyed** — the
same Chinese header string (`買進股數`/`賣出股數`/`買賣超股數`) repeats
across every group in the JSON `fields` array, so category identity comes
only from column position (or, as established above, from a one-time lookup
against the CSV export / this table — never guess from magnitude alone).

### Older schema (16 fields): fully named, no positional ambiguity

Unlike the current era, TPEx's older schema (see transition table below) is
**fully self-describing** — every field has a unique name, e.g.:

```
代號, 名稱,
外資及陸資買股數, 外資及陸資賣股數, 外資及陸資淨買股數,
投信買進股數, 投信賣股數, 投信淨買股數,
自營淨買股數,
自營商(自行買賣)買股數, 自營商(自行買賣)賣股數, 自營商(自行買賣)淨買股數,
自營商(避險)買股數, 自營商(避險)賣股數, 自營商(避險)淨買股數,
三大法人買賣超股數
```

No foreign/dealer split (single `外資及陸資` group); the dealer combined
figure is net-only (`自營淨買股數`, no gross buy/sell), unlike the current
era's fully-gross-and-net combined dealer group. "Positional, not
name-keyed" is a **current-era-only** caveat — a parser targeting the older
era can and should map by field name directly, the same as TWSE at every
era.

### Historical range and schema versions

Field counts corrected from round 1 (**16/24, not 17/25** — round 1
mistakenly reported `columnNum` as if it were the field count; see the
`columnNum` gotcha above):

| Date       | rows | `len(fields)` | `columnNum`  |
| ---------- | ---- | ------------- | ------------ |
| 2014-07-01 | 0    | n/a (empty)   | (unreliable) |
| 2015-01-05 | 372  | 16            | 17           |
| 2015-07-01 | 400  | 16            | 17           |
| 2016-01-04 | 459  | 16            | 17           |
| 2017-01-05 | 444  | 16            | 17           |
| 2018-01-02 | 475  | 16            | 17           |
| 2018-01-05 | 490  | 16            | 17           |
| 2019-01-05 | 0    | n/a (empty)   | (unreliable) |
| 2020-01-02 | 561  | 24            | 25           |
| 2021-01-05 | 649  | 24            | 25           |
| 2022-01-05 | 723  | 24            | 25           |
| 2023-01-05 | 708  | 24            | 25           |
| 2024-01-05 | 787  | 24            | 25           |
| 2026-08-07 | 923  | 24            | 25           |

- **Data floor is narrowed to between 2014-07-01 (no data) and 2015-01-05
  (data present)** — i.e. somewhere in the second half of 2014. Not pinned
  to an exact day; signature detection makes this unnecessary (see design
  principle above).
- **Schema transition (16->24 fields) is narrowed to between 2018-01-05 and
  2020-01-02.** Also not pinned to an exact day, for the same reason.
- Only two TPEx schema eras were found (unlike TWSE's three) across all
  sampled dates 2014–2026. A single year-per-sample scan can in principle
  miss a short-lived intermediate era; this is an accepted residual gap
  (see the "unknown-signature -> fail loudly" rule below), not a claim that
  exactly two eras is exhaustive.

### Non-trading day / before-floor ambiguity

Unlike TWSE, **TPEx gives no distinguishing `stat` message.** Every tested
case — real holidays within the valid range, and dates before the ~2014 data
floor — returns `stat: "ok"` with the real table (found via the `tables[]`
scan above) present but `data: []` / `totalCount: 0`. A TPEx backfill loop
**cannot tell "holiday" apart from "before data existed" from the response
alone**; it must rely on an external trading-calendar or accept that some
`(market, date)` cells will legitimately have zero rows without knowing why.

## Recommended normalized column mapping (for task 2.1)

Not a DB schema decision (`design.md`'s Decisions section is untouched by
this research) — a reference mapping table an implementer can use directly.
Confidence noted per era: TWSE is name-keyed at every era, so mapping is
mechanical; TPEx's current era is now CSV-confirmed; TPEx's older era is
name-keyed.

| Normalized column       | TWSE era A (12f)   | TWSE era B (16f)           | TWSE era C (19f, current)        | TPEx older (16f)         | TPEx current (24f)                       |
| ----------------------- | ------------------ | -------------------------- | -------------------------------- | ------------------------ | ---------------------------------------- |
| foreign_total_net       | 外資買賣超股數     | 外資買賣超股數             | 三大法人買賣超股數 minus others* | 外資及陸資淨買股數       | 外資及陸資(合計)買賣超股數               |
| foreign_excl_dealer_net | n/a (no split)     | n/a (no split)             | 外陸資買賣超股數(不含外資自營商) | n/a (no split)           | 外資及陸資(不含外資自營商)買賣超股數     |
| foreign_dealer_net      | n/a (no split)     | n/a (no split)             | 外資自營商買賣超股數             | n/a (no split)           | 外資自營商買賣超股數 (observed always 0) |
| trust_net               | 投信買賣超股數     | 投信買賣超股數             | 投信買賣超股數                   | 投信淨買股數             | 投信買賣超股數                           |
| dealer_total_net        | 自營商買賣超股數   | 自營商買賣超股數           | 自營商買賣超股數                 | 自營淨買股數             | 自營商(合計)買賣超股數                   |
| dealer_self_net         | n/a (no split)     | 自營商買賣超股數(自行買賣) | 自營商買賣超股數(自行買賣)       | 自營商(自行買賣)淨買股數 | 自營商(自行買賣)買賣超股數               |
| dealer_hedge_net        | n/a (no split)     | 自營商買賣超股數(避險)     | 自營商買賣超股數(避險)           | 自營商(避險)淨買股數     | 自營商(避險)買賣超股數                   |
| three_institutions_net  | 三大法人買賣超股數 | 三大法人買賣超股數         | 三大法人買賣超股數               | 三大法人買賣超股數       | 三大法人買賣超股數合計                   |

\* TWSE era A/B have no combined "foreign total" _field_, but the single
`外資` group there already IS the combined total (no split exists yet), so
`foreign_total_net` = that group's net directly, and
`foreign_excl_dealer_net`/`foreign_dealer_net` are simply not representable
in those eras.

Gross buy/sell columns (not just net) exist for every group named above at
every era except: TWSE era A/B's `自營商` combined group (net-only) and
TPEx's older-era `自營` combined group (net-only) — both mirror the same
"combined figure is net-only, sub-splits have full gross+net" shape.

## Parsing prerequisites

- **TWSE name field has trailing whitespace** — observed on multiple rows,
  e.g. `'聯電            '`. Strip before use.
- **No dash/blank sentinel tokens observed** in any of the ~44 saved
  responses across both markets and roughly 2012–2026 — unlike price data
  (where `parse_number`'s docstring notes a `---` no-trade sentinel),
  institutional share-count fields in every sample use plain `"0"` for "no
  activity," never a placeholder dash. This is not a guarantee the vendor
  will never emit one, but `shp_utils.official_api.parse_int`/`parse_number`
  already handle a `---`-style sentinel defensively if one ever appears, so
  no additional handling is required either way.
- Numeric fields use comma thousands separators (`"1,252,930"`) —
  `parse_int`/`parse_number` already strip commas.

## Recommended `(market, date)` backfill design — confirmed feasible

Both TWSE and TPEx now have confirmed dated, whole-market institutional
endpoints. The proposal/design's `(market, date)` checkpoint unit (as opposed
to price-side's per-symbol `(code, year_month)` unit) is **correct and
sufficient for both markets** — this was an open question in `design.md`'s
Context section and is now resolved: **yes, TPEx supports a dated history
query**, so 2.3/2.5 do not need a TPEx-only "daily increment only" fallback
path. Within each market, the parser should select its field-mapping table
by comparing the response's field-name signature against the tables above
(see the design principle at the top of this document) — never by branching
on the request date, and it should raise loudly on an unrecognized
signature rather than guess.

## Observed request behaviour and a safe rate (measured 2026-08-31)

A 2019-2023 historical backfill stalled repeatedly against TPEx with HTTP 520
and read timeouts, so the endpoint's behaviour was measured directly rather
than guessed at. Everything below is a live observation, not a policy read off
a documentation page.

**The failures are not caused by our request rate.** Measured from a single
host starting at 23:00 local time, `insti/dailyTrade` answered **463
consecutive daily requests with zero failures** on a fully logged stream -- 3
header variants, then 60 at 2.0s spacing, then 400 more at 2.0s -- while a
second identical 400-request stream ran alongside it, so roughly 860 requests
were issued at an aggregate of about one per second and none of them failed.
Median response time was about 0.5s. The front end reports `Server: nginx` with no
`CF-RAY` header, so the 520 is emitted by an intermediate layer, not by
Cloudflare.

**They are strongly time-of-day dependent.** The stalled run's own
`institutional_fetch_log.fetched_at` timestamps show, in local time on a single
day, 251 successful TPEx units in the 05:00 hour, 22 in 15:00, 141 in 16:00,
and then 2 in 17:00 before it stopped — at a _slower_ nominal spacing (10s)
than the burst that later succeeded cleanly. A cumulative per-IP daily budget
is ruled out by the same evidence: that run reached 416 TPEx requests before
stalling, and 763 more from the same address on the same calendar day all
succeeded once the evening had passed.

The operational conclusions:

- **Crawl history outside roughly 14:00-18:00 local.** The 05:00 window is
  demonstrated good and the machine is otherwise idle then.
- **2.0-2.5s spacing is safe** and is what `DEFAULT_THROTTLE_SECONDS` already
  uses; going slower buys nothing during a bad window and costs hours during a
  good one.
- **Retry in minutes, not seconds.** The previous linear `retry_sleep * attempt`
  ladder gave up after about 30 seconds. `fetch_institutional backfill` now
  defaults to five retries starting at 15s with jittered exponential backoff
  capped at 300s, and `run_collection` cools down for 10 minutes after two
  consecutive unit failures and stops cleanly after six, so a bad window ends a
  run instead of grinding through it.
- **Never work around the limit.** No proxying, address rotation, forged
  sessions, or parallel bombardment. Waiting for a better window is both
  cheaper and the only acceptable answer.

## Rejected lower-request-count TPEx sources (probed 2026-08-31)

Per-day querying is a hard requirement: there is no official TPEx artifact that
serves per-stock institutional flows for many days in one response.

- `insti/dailyTrade?type=Weekly` and `type=Monthly` — real endpoints that
  answer, but they aggregate the whole period into one row per stock (736 rows
  for a week, 804 for a month, same 24 fields). They cannot reconstruct the
  daily series a rolling flow measure needs.
- `web/stock/3insti/daily_trade/3itrade_hedge_result.php` — the legacy path is
  still served and returns a byte-identical payload to `insti/dailyTrade` for
  the same date (620 rows for 2020-09-21). Same backend, same request count, no
  advantage.
- `openapi/v1/tpex_3itrade_hedge` — does not exist; redirects to the site index.

## Calling the collector for a historical range

`run_collection(start_date=..., end_date=..., trading_dates=...)` is the whole
API. Call it once for the entire range rather than once per date: the
consecutive-failure streaks, the cooldown and the bounded stop all live inside
one call, so a per-date loop resets them on every date and none of them can ever
fire.

`trading_dates` does two jobs, and they are the same fact seen twice:

- **Which units exist.** A date outside the calendar produces no unit, so it is
  never requested. Without this the daily run spent four requests every day
  re-asking about the previous weekend, for ever -- see below.
- **What an empty response means.** A day the calendar contains that answers
  with no rows is `partial` and stays owed; a day outside it is `empty` and is
  settled.

Passing `None` keeps the every-calendar-day behaviour on purpose. A caller whose
calendar failed to load is better off making redundant requests than silently
fetching nothing and reporting success, which is why `load_trading_dates()`
returns `None` on any read failure instead of raising, and why
`fetch_institutional daily` keeps any date newer than the calendar's newest
entry: `price_daily.parquet` is rebuilt earlier in the same pipeline run and can
lag, and a stale calendar must not be able to suppress today.

Failure streaks are counted **per market**. Units interleave TWSE then TPEx
within each date, so a single global counter let a healthy TWSE reset TPEx's
streak on every date -- TPEx could fail on hundreds of consecutive dates and
never reach the stop threshold. That is the precise shape the 2019-2023 backfill
hit. Reaching the threshold on either market stops the whole collection: a
market that has stopped answering usually means the window is bad for both, and
the other market's settled units are checkpointed and skipped on the next run.

### The weekend that never settled

Between 2026-08-20 and 2026-09-01 the daily run left four checkpoints
permanently at `partial`: 08-22, 08-23, 08-29 and 08-30, two Saturdays and two
Sundays. Both endpoints answer a non-trading day with a _successful_ empty body,
so nothing failed and nothing logged an error -- the rolling seven-day window
simply re-asked all four every single day and got the same empty answer back.
Reading the calendar out of the price cache removes the requests; the rows
already written are left alone, since they fall out of the rolling window on
their own and rewriting checkpoints in place is not something acquisition code
should do.

## Proving where a response came from

The checkpoint records two URLs. `source_url` is built before the request and
proves only what the program meant to ask for. `final_url` comes from the
response itself, so it is the only one that proves which host actually answered.

The transport rejects a response whose URL is not exactly the expected host over
`https`, with no userinfo, no non-default port and no fragment. Hostname
comparison is exact equality after case folding, never a suffix or substring
test: `www.twse.com.tw.example.net` ends with the expected name and is a
different party. A rejected response raises `UnexpectedResponseHostError`, which
is deliberately outside the retry ladder -- being answered by the wrong host is
not transient -- and lands as an `error` checkpoint carrying both URLs.

Redirect following is left enabled. Disabling it is the stricter choice and the
validation above would still hold, but no observed TWSE or TPEx request has
needed a redirect, and turning it off blind risks breaking the 18:00 run for a
path that has never been exercised. The validation is what makes a cross-host
redirect harmless either way.

## TWSE publishes some rows after the same-day fetch (measured 2026-09-06)

`T86` is not final when the 18:00 daily run reads it. Re-fetching four dates that
had already been fetched and settled added rows to every one of them, and removed
none:

| date       | rows at 18:0x | rows on re-fetch | added                               |
| ---------- | ------------- | ---------------- | ----------------------------------- |
| 2026-08-27 | 1,329         | 1,333            | `00636K` `00643K` `00657K` `00668K` |
| 2026-09-02 | 1,334         | 1,338            | `00636K` `00643K` `00657K` `00668K` |
| 2026-09-03 | 1,320         | 1,323            | `00636K` `00657K` `00668K`          |
| 2026-09-04 | 1,336         | 1,340            | `00636K` `00643K` `00657K` `00668K` |

Every added code is a `K`-suffixed ETF share class. Absence from `T86` normally
just means a security had no institutional trades that day, so absence alone is
not evidence of a gap -- but three of these four appear on **1,202 to 1,204 of
the 1,228 TWSE dates in the artifact (~98%)**, which makes a simultaneous
three-day disappearance an anomaly rather than a quiet week. `00643K` is the
exception at 55 dates, genuinely sporadic, and its absence proves nothing.

**The rolling window does not catch this.** `run_daily_fetch` re-requests a
seven-day window, but `is_unit_cached` skips any unit already settled, so a date
fetched successfully at 18:0x is never asked again. A row the exchange publishes
later is therefore permanent absent a deliberate re-fetch: the four dates above
were only recovered because they were re-fetched by hand.

Two consequences worth keeping in mind:

- A same-day institutional row count is a floor, not a total. Do not treat the
  count stored on the day itself as the report's final size.
- Any study whose window ends within a few days of its acquisition date may be
  missing a handful of ETF rows. The effect is small in row terms -- 3 to 4 rows
  out of ~1,330 -- but it is systematic in _which_ securities it drops.

A daily run that force-refreshed the last few trading days would close this at a
cost of roughly six extra requests per day. That is a deliberate change to the
request profile and to what "settled" means, so it is recorded here rather than
implemented on the strength of one measurement.

## An institutional row does not imply a price bar (measured 2026-09-08)

Joining `institutional_daily` to `price_daily.parquet` on `(code, date)` silently
drops rows. Over all of 2026 — 31,338 institutional rows — 5,382 have no matching
price bar. Almost all of that, 5,363, is a universe difference rather than a gap:
the code is not in the price cache at any date (bond ETFs such as `00937B`,
`00687B`, `00411A`). The remaining **19 are genuine per-day gaps for codes the
price cache does carry**, and they split cleanly by market into two unrelated
mechanisms.

### TPEx, 16 rows across 8 dates: the day had only odd-lot trades

TPEx publishes a row for these days, but with prices `----`, `成交股數` 0 and
`close_missing=1`, so the parquet build correctly produces no bar. The
institutional feed still lists the security, because a dealer or foreign desk
traded it in an odd lot.

| market | code | date       | gross shares | official row     |
| ------ | ---- | ---------- | ------------ | ---------------- |
| TPEx   | 4154 | 2026-08-27 | 9            | `----`, volume 0 |
| TPEx   | 3118 | 2026-09-02 | 2            | `----`, volume 0 |
| TPEx   | 6210 | 2026-09-01 | 204          | `----`, volume 0 |
| TPEx   | 1788 | 2026-08-25 | 473          | `----`, volume 0 |

All 16 are between **2 and 594 shares — not one reaches a 1,000-share round lot**.

**Verify the unit before believing that sentence**, because the whole reading
turns on it: if the institutional feed reported 張 rather than 股, `4154`'s 9
would be nine round lots on a day the price feed says zero volume, which would be
a defect rather than a scope difference. Checked against
`official_daily_price.volume` on days both feeds cover, institutional gross
(buy + sell) over official volume lands at 0.74–2.34 for `2330` (TWSE), `6488`
and `5347` (TPEx) — order 1, so both are in shares. A ratio near 1/1000 would
have meant lots.

This is normal for thinly traded TPEx names and is not confined to single days.
`6228` had `----` and zero volume on every session from 2026-08-28 through
2026-09-08 — eight in a row. It is listed and quoted the whole time; it simply
had no round-lot trades. A per-symbol Shioaji download failing for exactly that
span is the same fact seen from the other side, not a download problem.

The two markets are not comparable here: in 2026 TPEx wrote 884,442 rows with
`close_missing=1` against TWSE's 1,584.

### TWSE, 3 rows, all on the fetch date: the price feed lags a trading day

`2945`, `4581` and `5906` on 2026-09-08 look like the same thing and are not.
`official_daily_price` has **no TWSE row at all** for that date, because the TWSE
bulk daily quote was still one trading day behind when the 18:00 run read it —
`fetch_official_daily_price` says so itself:

```text
18:11:12 WARNING - official daily feeds disagree on the trading day
                   (TPEx=2026-09-08, TWSE=2026-09-07);
                   the lagging market is missing a day until it is backfilled
```

The next day's run fills it, which is why no earlier date in 2026 shows a TWSE
case. These three surfaced only because Shioaji's per-symbol download also failed
for them that day, leaving both feeds empty at once.

Note the direction is the opposite of the section above: `T86` gains rows _after_
the same-day fetch, while the official price feed has not published the day _yet_.
Two feeds, two different lateness profiles; do not generalize one to the other.

### What to do about it

- Treat a missing price bar as "no round-lot trades", not "missing data", and
  never infer a trading suspension from it — an official row with `----` proves
  the security was still quoted.
- A study joining institutional activity to price loses on the order of 16 rows a
  year to this. That is negligible in aggregate and concentrated entirely in names
  too illiquid to trade, but an inner join will drop them without saying so.
- When checking the newest day, expect TWSE official to be one session behind
  until the following run.

## Rejected sources

- `https://openapi.twse.com.tw/v1/fund/T86` — 404, does not exist.
- `https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading` (and
  `tpex_3insti_dealer_trading`, `tpex_3insti_summary`, `tpex_3insti_trading`,
  `tpex_3insti_qfii_trading`) — no date parameter, latest-day only, and the
  `daily_trading` variant's JSON keys have collision/typo defects
  (`"Dealers -TotalSell"` with a stray space vs `"ForeignDealers-Difference"`
  inconsistent casing) that make programmatic column mapping fragile even for
  the one day it does cover. Considered as a value-anchored labeling oracle
  for the current TPEx schema in round 2's plan, but not needed in practice
  — `dailyTrade?response=csv` on the already-adopted source endpoint answered
  the same question directly, with a full 898-row cross-check besides.
