# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-29-add-tpex-price-history/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-29-add-tpex-price-history/tasks.md).
No production action or acquisition is authorized by this record.

# Tasks

Endpoint facts: `docs/tpex-history-sources.md`. Decisions: `design.md`.

## 1. Design

- [x] 1.1 Encode a whole-market unit as `code = ''` with the checkpoint's `year_month`
      column renamed to `period`, holding `YYYY-MM` for TWSE and `YYYY-MM-DD` for TPEx.
      Decision 3.
- [x] 1.2 Settle the in-progress-period question: it does not arise for TPEx, because a
      unit is a single past day. The nightly fetch owns the current day; the backfill only
      requests days that already have TWSE rows, which a day cannot have until it ends.
      Decision 2.
- [x] 1.3 Select candidate dates from the artifact itself — dates with TWSE rows and no
      TPEx rows — newest first. Decision 2.
- [x] 1.4 Decide what backfilled volume means, given it differs from the nightly figure.
      Decision 1, revised after measurement: it matches Shioaji exactly and the nightly
      feed is the outlier.

## 2. Implementation

- [x] 2.1 Migrate the checkpoint: rename `year_month` to `period` in
      `official_history_raw_ranges`, in the same init path that creates the table, and
      update every reference.
- [x] 2.2 Generalize `CrawlUnit` to carry its market, so the crawl loop dispatches fetch
      and normalize by market instead of assuming TWSE.
- [x] 2.3 Add the TPEx market-day fetch via `www.tpex.org.tw/www/zh-tw/afterTrading/otc`
      with `type=AL`, reusing `shp_utils.official_api` for retry and backoff.
- [x] 2.4 Add the field-signature table for every era and a normalizer producing the
      existing `official_daily_price` row shape, raising on an unrecognized signature
      rather than parsing positionally. Rows are read by field name, so one normalizer
      serves every era and the signature table exists to recognize a schema. Three eras,
      not the two the research doc named -- see 4.7.
- [x] 2.5 Add gap-driven date selection: TWSE dates with no TPEx rows, newest first,
      excluding dates already checkpointed.
- [x] 2.6 Extend the auto mode to crawl TPEx gaps before TWSE months within the existing
      request/time budget, and add an explicit `--tpex-dates` argument.

## 3. Tests

- [x] 3.1 Normalizer: a 15-field fixture and both 17-field fixtures each produce the
      expected rows, with volume in shares and `close_missing` set for a no-trade row.
- [x] 3.2 An unrecognized field signature raises rather than storing rows.
- [x] 3.3 Gap selection returns only dates with TWSE rows and no TPEx rows, newest first,
      and ignores dates before the endpoint floor.
- [x] 3.4 A TPEx market-day and a TWSE symbol-month checkpoint independently, neither
      shadowing the other.
- [x] 3.5 An empty response on a selected date is recorded as `empty` without being stored
      as data, and is not re-selected automatically.
- [x] 3.6 Re-fetching a stored TPEx date produces no duplicate rows.
- [x] 3.7 The `period` migration runs against a database created with the old column name
      and preserves existing rows.

## 4. Validation

- [x] 4.1 `uv run python -m pytest tests/ -q` — 168 passed.
- [x] 4.2 `uv run pre-commit run --files <changed files>` — clean.
- [x] 4.3 Backfilled two real gap days (2026-07-01, 2026-07-02) against the live endpoint
      into a scratch database: 9,979 and 10,027 rows, checkpointed as
      `('TPEx', '', '2026-07-01', 'ok', 9979)`, every stored volume a multiple of 1,000.
- [x] 4.4 Cross-checked five OTC symbols across both days against this repository's
      Shioaji-derived bars: every close and every volume identical, 0.00% difference —
      comfortably inside the report's 10% volume threshold, and better agreement than the
      nightly feed achieves.
- [x] 4.5 Proved the column migration against a copy of the live 1.4 GB database: 213,765
      rows preserved, rename completed in 0.00s (metadata-only).
- [x] 4.6 Confirmed on the 2026-08-21 and 2026-08-22 scheduled runs. The column
      migration ran cleanly and TPEx gap days were crawled ahead of TWSE months as
      designed. Coverage moved from 12 TPEx days to 436: all of 2026 and 2025 back to
      2025-01-10, plus 2020-02-18..2020-04-29. 2,750 gap days remain.
- [x] 4.7 Third schema era, found by the same runs: 2020-04-30..2025-01-09 labels the
      last bid/ask size `(千股)` rather than `(張數)`. Identical shape, and the parser
      never reads those columns, so the fix is one signature-table entry. Verified before
      adding it, rather than assuming the labels were cosmetic: `成交股數` on 2021-03-10,
      2023-04-06 and 2024-12-30 matched this repository's Shioaji bars on 17 of 18
      symbol-days exactly and the 18th to 0.09%, every close matched to the cent, and
      every volume was a multiple of 1,000. Needs no database surgery -- the second run
      re-selected dates the first had marked `error`, which proves `is_unit_cached`
      ignores that status, so the 1,147 days re-queue on their own.
- [x] 4.8 Cross-checked the 15-field era, which the first run had written on the strength
      of a matching signature alone: closes exact on all three sampled days, volumes
      exact on 2020-03-16 and 2020-04-29. 2020-02-18 differs by up to 4.85% on volume
      with prices still exact, and 8069 on 2024-12-30 differs by 0.09% in the middle era,
      so the residue is per-day and not per-era -- plausibly after-hours fixed-price
      trading counted by the exchange but not by Shioaji's bars. Unexplained, bounded,
      and well inside the report's 10% threshold. Not chased further here.
- [x] 4.9 Explained the apparent 53s/unit throughput, which looked like TPEx
      rate-limiting and is not. `run_backfill` backs off as
      `throttle * 2 ** min(consecutive_failures, MAX_BACKOFF_MULTIPLIER)`, which pins at
      2.0 x 32 = 64s and matches the 65s cadence in the log exactly. The backoff is
      right for a transport failure and useless against a deterministic parse failure:
      1,147 of those held it at maximum and spent a whole night waiting for a condition
      no wait could change. A bounded live run of two middle-era days on the fixed code
      took 6s for 2 units -- 3s each, the throttle plus the request -- so 2,750 days is
      roughly two to three hours, not two to three nights.
- [x] 4.10 Applied 4.7's own lesson to the range the next run will crawl. The larger
      half of the remaining gap (2013-08-01..2020-02-17) was known only as
      `len(fields) == 15` from the discovery probes -- the evidence class that hid the
      middle era. Signature-checked eight dates spread across it (2013-09-02, 2014-05-15,
      2016-06-15, 2017-03-01, 2018-01-05, 2019-10-01, 2020-01-02, 2020-02-17) by name,
      through the production `normalize_tpex_field_name` and against the live signature
      table: one distinct signature, all eight the known 15-field era. No fourth era
      hiding there.
- [x] 4.11 Confirmed 2026-08-29: the backlog is cleared and the crawl is finished.
      `official_history_raw_ranges` holds 4,064 TPEx days at `ok` and **zero at
      `error`**; TPEx rows now run 2010-01-04..2026-08-28, and the 2026-08-28
      scheduled run reported `auto plan: 1380 TWSE codes, 0 TPEx gap day(s)` and
      exited in 27s with `reason=units exhausted, fetched=0`. Independently verified
      against gaps rather than counts: every one of the 50 inter-day gaps longer than
      four calendar days is a recognizable market holiday (Lunar New Year appears
      exactly once in each of the 17 years), the per-year non-trading weekday counts
      resolve to real closures including typhoon days (2023-08-03 Khanun, 2024-07-24/25
      Gaemi, 2024-10-02/03 Krathon, 2024-10-31 Kong-rey), and over the 1,875 days that
      overlap the Shioaji-derived cache no official day is missing. (The two dates the
      cache holds and the official artifact does not -- 2021-10-31 and 2024-12-01 --
      are both Sundays, so they are Shioaji-side artifacts, not official gaps; that
      also explains the standing 2024-12-01 false positive in the data-quality report.)
      The second half -- whether the backoff should distinguish a transport failure
      from a parse failure -- remains out of scope, noted so it is not rediscovered.
