# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-07-add-official-daily-price-source/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-07-add-official-daily-price-source/design.md).
No production action or acquisition is authorized by this record.

# Design

## Context

Daily bars today are derived from Shioaji minute kbars (`download_stock.py` →
`convert_stock_price.py` → `{code}_day.csv` → `build_price_parquet.py`). A reconciliation
against official exchange figures for 2026-07-22 (1,953 comparable symbols) found close prices
reliable (99.9% within 0.01) but volume systematically undercounted (median local/official
0.985; local < official for 85%, local > official for 0%; a ~10-symbol tail off by >30%).
Shioaji is also a single dependency with a broker-account requirement, a 500 MB/day quota, and
a ≤30-day query window.

This change introduces the exchanges' official daily data as an independent, authoritative
second source.

## Source verification (why these endpoints are trusted)

- **Official issuer**: data is published by TWSE and TPEx (the exchanges themselves) and
  catalogued on the government open-data platform (data.gov.tw) under the FSC Securities and
  Futures Bureau, licensed "政府資料開放授權條款第1版" (Government Open Data License v1). The
  Swagger `info` blocks self-identify as "臺灣證券交易所 OpenAPI" (v1.0) and
  "證券櫃買中心 OpenAPI" (v1.0.0). These are first-party endpoints, not a third-party scraping
  library.
- **Availability history**: individual daily trading data has been listed on data.gov.tw since
  2017-05-23 (updated daily); the dedicated `openapi.twse.com.tw` Swagger portal is v1.0
  (community-reported launch circa 2021; exact press-release date not independently confirmed).
- **Rate limits**: no rate-limit headers are returned and no hard quota is published. Observed
  behavior: the whole-market **bulk** endpoints require only one request per market per day and
  are effectively unconstrained at that cadence; the **single-symbol history** endpoint has a
  community-reported soft limit around a few requests per 5 seconds, with temporary IP blocks
  when exceeded. The crawler design treats the exact limit as unknown and stays conservative.

## Endpoints

| Purpose               | Market      | URL                                                                                                                           | Shape                                                      |
| --------------------- | ----------- | ----------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| Bulk latest day       | TWSE listed | `https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL`                                                                 | JSON array, all listed stocks, **latest trading day only** |
| Bulk latest day       | TPEx OTC    | `https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes`                                                        | JSON array, all OTC instruments, latest trading day only   |
| Single-symbol history | TWSE listed | `https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date=YYYYMMDD&stockNo=CODE&response=json`                              | One month of daily bars for one stock                      |
| Single-symbol history | TPEx OTC    | **Does not exist** — confirmed against TPEx's own OpenAPI swagger catalogue (225 paths); see "Open questions" and decision #6 | n/a — TPEx history is not backfillable                     |

**TWSE history floor**: the single-symbol history endpoint rejects any query before ROC 99/1/4
(2010-01-04). Verified live: `date=20091201` returns `stat: 查詢日期小於99年1月4日，請重新查詢!`
with 0 rows; `date=20100101` returns `stat: OK` with 20 rows. No earlier official history is
obtainable from this endpoint, from any tool, regardless of how the request is framed.

### Field mapping and conventions

- **TWSE bulk**: `Code, Name, Date, OpeningPrice, HighestPrice, LowestPrice, ClosingPrice,
Change, TradeVolume (shares), TradeValue, Transaction (count)`.
- **TPEx bulk**: `SecuritiesCompanyCode, CompanyName, Date, Open, High, Low, Close, Change,
TradingShares (shares), TransactionAmount, TransactionNumber, NextReferencePrice, …`.
- **Volume unit**: official volume is in **shares**; divide by 1000 for 張 (lots) when
  comparing to `{code}_day.csv`.
- **Date format**: TPEx (and the TWSE history endpoint) use ROC year, e.g. `1150722` =
  2026-07-22. Normalize to ISO on read.
- **No-trade day**: a zero-volume day is returned with `TradingShares = "0"` and
  `Close = "---"` (plus a `NextReferencePrice`). This is a legitimate row (e.g. an illiquid
  symbol that did not trade), not missing data — record it as a zero-volume day, do not drop it.
- **Numeric parsing**: values may contain commas and the `---` sentinel; parse defensively.

## Key decisions

### 1. Store official data separately; do not replace Shioaji volume yet

The official artifact is written to its own files/columns, distinct from `{code}_day.csv` and
`price_daily.parquet`. Rationale: switching the consolidated cache's volume from Shioaji to
official would create a **seam** in the historical volume series (older rows ~1.5% low, newer
rows correct), which shifts any volume-based backtest. That switch is a separate, deliberate
decision; this change makes the official data available and cross-checkable first.

### 2. Two acquisition paths: bulk (daily) vs history (backfill)

- **Daily**: `run_daily.py` calls the two bulk endpoints once each → one trading day for the
  whole market in two requests. Cheap, no throttling needed.
- **Historical backfill**: a standalone crawler uses the single-symbol history endpoint,
  because the bulk endpoints only return the latest day. This is inherently many requests
  (symbols × months) and is where rate limiting matters.

### 3. Crawler: conservative, resumable, backing off

- **Checkpointing**: persist per-symbol (and per-month) completion so a re-run skips finished
  work and resumes exactly where it stopped. A stop/kill mid-run must never corrupt or
  duplicate stored rows.
- **Throttle**: a configurable minimum delay between requests, defaulting well under the
  observed soft limit (target ≈ 1 request / ≥2 s, i.e. below "a few per 5 s").
- **Backoff**: on HTTP 429 / empty-but-expected / connection errors, exponential backoff with
  a cap; after repeated failures, pause and record the failed unit rather than hammering.
- **Idempotent writes**: merging a fetched month must de-duplicate by date (same contract as
  the existing forced-refetch merge in `download_stock.py`).
- **Bounded runs**: support a max-requests or max-duration budget per invocation so it can be
  run in small daily slices ("slowly crawl it down") without tripping limits.

### 4. Failure isolation in `run_daily.py`

The official-fetch step follows the existing pattern: wrapped in try/except, logs the full
exception message, and never aborts the other steps. An outage of the public endpoints must
degrade to "no official data refreshed this run", not a failed daily run.

## Risks / trade-offs

- **Public-endpoint dependency**: no SLA; format or path changes are possible. Mitigation:
  defensive parsing, isolated failure, and Shioaji remaining the primary daily source for now.
- **Backfill cost**: full history via the single-symbol endpoint is tens of thousands of
  requests. Mitigation: existing Shioaji history is retained; the crawler is for filling
  official history incrementally, not a one-shot rebuild.
- **Two sources of daily truth** until a later change picks one for the parquet cache. Managed
  by keeping them in distinct artifacts and comparing them via the existing data-quality path.
- **TPEx TLS chain quirk**: TPEx's certificate chain is missing the Subject Key Identifier
  extension, which OpenSSL 3.x's stricter RFC 5280 validation rejects even though browsers and
  the OS trust store accept the chain. Mitigation: the `truststore` dependency routes
  certificate verification through the OS trust store instead of the bundled OpenSSL verifier
  (`shp_utils/official_api.py` calls `truststore.inject_into_ssl()`), which resolves this
  without weakening verification (no `verify=False`).

### 5. Cross-check lives in the existing `data-quality-report` capability, not a new one

The official-vs-Shioaji daily comparison is added as a MODIFIED requirement on
`data-quality-report` (see `specs/data-quality-report/spec.md`) rather than as a new
requirement under `official-price-source`. Rationale: `check_data_integrity.py` already owns
exactly this shape of check (its corporate-action price-gap cross-check), and
`data/data_quality_report.json` is already the stable, documented artifact a caller reads for
"is the local data trustworthy" — reusing it avoids a second, competing report file. The
check is diagnostic only (flags discrepancies for review); it does not decide which source is
correct or block any pipeline step.

The comparison is bounded to the overlap of each source's own covered date range (not the
full symbol history): official data is backfilled gradually, so a date the backfill hasn't
reached yet is expected to be absent, not a discrepancy. Comparing without this bound would
flood the report with false gaps for every not-yet-backfilled date.

**Volume unit seam**: `{code}_day.csv`'s `Volume` column is in board lots; the official
artifact's volume is in shares (see "Endpoints" above). A first implementation compared the
two raw values directly and, on a live run against the full local dataset, flagged 1,079 of
~1,980 symbols as a "volume mismatch" — a ~1000x unit-scale artifact, not a real discrepancy.
Fixed with an explicit `SHARES_PER_LOT = 1000` conversion applied before both the threshold
comparison and the reported value (so a manual reviewer sees like-for-like units, not a fake
1000x gap). Re-running live after the fix dropped flagged symbols to 32, all genuine
volume-field outliers.

### 6. No-argument auto-crawl mode: universe, floor, ordering, budget, concurrency, interruption

The default invocation (`uv run python backfill_official_history.py`, no arguments) needed a
concrete answer for five things:

- **Universe**: `SELECT DISTINCT code FROM official_daily_price WHERE market='TWSE'` — the
  official artifact's own TWSE codes, not the Shioaji symbol mapping. Measured before choosing:
  the mapping's 2,497 codes split as 1,226 in TWSE bulk, 897 in TPEx bulk, and 374 warrant/bond
  codes outside both — every one already accounted for, so unioning the mapping in would add
  zero backfillable equities while costing ~374 wasted requests per month-pass. The official
  universe also includes 145 ETF codes (e.g. `006203`, `00400A`) the Shioaji mapping lacks.
- **Range and ordering**: from `EARLIEST_SUPPORTED_YEAR_MONTH = "2010-01"` (the endpoint's own
  floor, see "Endpoints" above) through the last fully-elapsed calendar month, newest month
  first. Combined with skip-cached checkpointing, one unchanging command naturally finishes 2026
  before touching 2025, and so on backward — no parameter ever needs to change.
- **Default budget**: with neither `--max-requests` nor `--max-seconds` given, the auto mode
  applies `DEFAULT_AUTO_MAX_SECONDS = 3600`. A bare no-argument invocation must mean "today's
  bounded slice," never "run the ~271,000-request backlog to completion in one sitting."
- **Concurrency**: `data/official_daily.sqlite` is also written by `run_daily.py`'s own daily
  fetch and read by the health check. `init_db()` now sets `PRAGMA journal_mode=WAL` (persisted
  in the file) and every connection sets `PRAGMA busy_timeout=30000`. This is sufficient — not a
  mutual-exclusion lock — because the crawler commits after each symbol-month and holds the
  write lock for milliseconds out of every ~2.5s cycle, the daily fetch's two bulk upserts need
  the lock for about a second, and the `(market, code, date)` upsert is idempotent regardless of
  interleaving. Live-verified: a 40-request bounded backfill run concurrently with a direct call
  to both the daily fetch and the health check completed all three with no lock errors, and the
  cross-check's flagged-symbol count stayed at 32 (no regression).

    A separate **advisory lock file** (`data/.official_backfill.lock`, pid + start time, released
    in `finally`) exists for a different reason: preventing **two backfills** from running at
    once, which would double the request rate and risk a TWSE IP block — not a data-integrity
    concern, a politeness-to-the-endpoint concern. `run_daily.py`'s own fetch deliberately does
    not check this lock, so the daily refresh is never blocked by an in-progress backfill.

- **Interruption**: a month still in progress (the current calendar month) must never be
  checkpointed as complete — `mark_unit_fetched()` now writes `status='partial'` for it and
  `'ok'` only once the month has fully elapsed, so a re-run always revisits an in-progress month
  instead of silently losing the rest of it. `KeyboardInterrupt` is caught around the crawl loop
  (each unit already commits before the next starts, so the checkpoint table is always an
  accurate resume point) and logged as a clean summary rather than a traceback; the lock file is
  released either way.

## Migration / long-term direction

- **Target architecture**: official OpenAPI data becomes the authoritative daily source —
  price now, with other officially published daily series (e.g. institutional 三大法人
  buy/sell) a natural later extension of the same fetch foundation — and Shioaji is reduced
  to a minute-bar-only auxiliary source. The system does not jump there in one step.
- **Now**: dual acquisition; official data stored and cross-checked against Shioaji-derived
  data via the data-quality health check (backup + gap detection while both sources coexist).
  Shioaji stays the source of record for `price_daily.parquet` throughout this stage.
- **Next (this change, ongoing)**: the rate-limited resumable crawler backfills official
  history gradually ("slowly crawl it down") rather than a one-shot rebuild.
- **TPEx history ceiling — official-as-primary can only hold historically for TWSE.** TPEx has
  no per-symbol history endpoint (decision #6, task 1.1), so TPEx official history can only ever
  start from the date this pipeline began daily bulk-fetching it — it cannot be backfilled
  further back, no matter how the crawler evolves. Any future "official is now the source of
  record" change must account for TPEx daily history having a hard start date rather than
  reaching back to the same depth as TWSE.
- **Survivorship-bias limitation**: the auto-crawl universe is _currently listed_ TWSE codes
  (from the official artifact); a symbol that delisted before this pipeline began fetching it is
  not in that universe and will not be backfilled. Nothing available to this pipeline enumerates
  historical delisted TWSE codes.
- **Explicit gate for promoting official to source of record**: switching `price_daily.parquet`
  to the official close/volume is deliberately deferred until both (a) the TWSE backfill has
  reached the depth needed for the intended use, and (b) the volume seam (older Shioaji-derived
  rows ~1.5% low, newer official rows correct) has an explicit handling decision. Until both
  hold, Shioaji remains primary and official stays a backup/cross-check source per decision #1.
- **Shared foundation for future officially-sourced data**: the official-OpenAPI HTTP fetch
  helper (retry/backoff, ROC-date and comma-number parsing) and the rate-limited resumable
  history crawler built here are designed to be reusable by other officially-sourced daily
  series — the main-repo `add-institutional-data-pipeline` change (TWSE T86 + TPEx
  institutional buy/sell) is the next candidate consumer of this same foundation, since its
  bulk endpoint also returns only the latest day and needs an equivalent history crawler for
  backfill. That change is lower priority than this one and is not implemented here.
- **Later (separate change)**: promote official daily close/volume to the source of record for
  `price_daily.parquet`, handling the volume seam explicitly; minute bars continue to come from
  Shioaji (SinoPac) for any intraday needs.

## Backfill cost estimate

The backfillable universe is TWSE-only (TPEx has no history endpoint): 1,371 codes as of this
writing. At the crawler's default 2.5s per request:

| Scope                                           | Requests  | Wall-clock (continuous)       |
| ----------------------------------------------- | --------- | ----------------------------- |
| Current year to date (e.g. 2026-01…2026-06)     | ≈ 8,200   | ≈ 5.7 h                       |
| Full floor-to-now (2010-01…2026-06, 198 months) | ≈ 271,000 | ≈ 188 h ≈ 7.8 days continuous |

At roughly one hour per day, the current-year slice completes within about a week; the full
floor-to-now backfill takes on the order of six months of daily one-hour slices.

## Open questions

- ~~Exact TPEx single-symbol daily-history endpoint path and parameters~~ — resolved: no such
  endpoint exists in TPEx's documented OpenAPI swagger catalogue (225 paths checked; every
  `tpex_daily_*` path is a whole-market snapshot, not a per-symbol history query). TPEx's
  legacy site may have one, but that's outside the "official OpenAPI" scope this change is
  built on. `backfill_official_history.py` is TWSE-only by design, not pending confirmation.
  See `tasks.md` task 1.1.
- ~~Coverage of ETFs/warrants/special instruments across the two bulk endpoints vs the symbol
  universe this pipeline archives~~ — resolved: 85% of the archived universe (2,123/2,497) is
  covered by the official bulk fetch; the uncovered 15% is overwhelmingly employee-warrant and
  corporate-bond-style instruments outside the bulk endpoints' equity/ETF scope. See `tasks.md`
  task 1.3.
- ~~Official artifact storage shape~~ — resolved: a consolidated SQLite database
  (`data/official_daily.sqlite`), not either CSV/parquet option originally listed here. See
  `tasks.md` task 1.2 for the rationale.
