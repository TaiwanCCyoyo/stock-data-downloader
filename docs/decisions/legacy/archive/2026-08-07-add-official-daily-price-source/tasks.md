# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-07-add-official-daily-price-source/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-07-add-official-daily-price-source/tasks.md).
No production action or acquisition is authorized by this record.

## 1. Confirm endpoints and artifact contract

- [x] 1.1 Confirmed by fetching TPEx's own OpenAPI swagger catalogue
      (`www.tpex.org.tw/openapi/swagger.json`, 225 documented paths) and searching it for a
      daily/history-shaped path (rather than guessing one, per design.md's stated preference to
      avoid path-guessing). **Finding: no such endpoint exists in the documented catalogue.**
      Every `tpex_daily_*`/`tpex_*_daily_*` path present is a whole-market snapshot (mirroring
      what the bulk fetcher already consumes), not a per-symbol, date-ranged history query.
      TPEx's legacy (pre-OpenAPI, HTML-scraping-style) site may have such a query, but that is
      out of scope for a change built specifically around the official _OpenAPI_ endpoints — see
      design.md's updated open-questions note. `backfill_official_history.py` therefore stays
      TWSE-only by design, not as a pending confirmation.
- [x] 1.2 Decide the official daily artifact storage shape. Implemented as a consolidated
      SQLite database (`data/official_daily.sqlite`, table `official_daily_price`, PK
      `(market, code, date)`) rather than either CSV or parquet option originally listed here:
      the bulk endpoint hands back the whole market for one day, so a single atomic
      `executemany` upsert (mirroring `fetch_corporate_actions.py`'s pattern) avoids ~2000
      non-atomic per-symbol file writes and gives idempotency for free. Columns: `market, code,
date (ISO), name, open, high, low, close, volume (shares), value, transactions,
close_missing, raw_fields_json, fetched_at`. A no-trade day (TWSE reports it as literal
      `"0.00"`, TPEx as `"---"`) is stored as `close = NULL, close_missing = 1` in both markets
      — see 2.3.
- [x] 1.3 Confirmed with a live query: of this pipeline's 2,497-symbol archived universe,
      2,123 (85%) appear in the official bulk fetch; 374 (15%) don't. The 374 are
      overwhelmingly employee-stock-warrant/rights-series and corporate-bond-style codes
      (structural signal: 5-digit codes with a trailing sequence numeral in their names, e.g.
      "台泥一永", "華友聯三") — instrument types the two bulk endpoints don't cover because
      they're scoped to ordinary equity/ETF daily quotes, not corporate bonds or warrant
      series. Conversely, the bulk fetch returns ~9,400 codes outside this pipeline's archived
      universe (ETFs, plain warrants, international bonds, etc. this pipeline never archived
      via Shioaji) — expected, not an anomaly, since the official endpoints cover the whole
      market and this pipeline only ever archived a subset.

## 2. Official daily fetcher (bulk, latest day)

- [x] 2.1 Implemented in `fetch_official_daily_price.py` (`normalize_twse_bulk_payload`,
      `normalize_tpex_bulk_payload`) using shared parsing helpers in
      `shp_utils/official_api.py` (`parse_number`, `parse_int`, `parse_roc_compact_date`).
      Live-verified against both real endpoints: TWSE `Date` is 7-digit ROC with no separators
      (e.g. `"1150722"`); TWSE zero-volume days use `ClosingPrice = "0.00"`, not `"---"` — the
      `---` sentinel only appears on the TPEx side (sometimes with nonzero `TradingShares`,
      i.e. a tiny trade with no usable close).
- [x] 2.2 Both markets route through `run_fetch()` and upsert into the shared
      `official_daily_price` table (market-scoped by the `market` column), kept distinct from
      `{code}_day.csv`.
- [x] 2.3 A no-trade day is recorded as a real zero-volume row (not dropped), with
      `close = NULL, close_missing = 1` regardless of which market's sentinel convention
      produced it — see 1.2's note on why both markets are normalized the same way.
- [x] 2.4 Each market's fetch/normalize/upsert is wrapped and isolated in `run_fetch()`; a
      failure in one market is logged and does not affect the other market's already-fetched
      rows. Live-verified: an initial TPEx TLS failure (see below) left the same-run TWSE data
      intact.
- [x] 2.5 `test_official_api.py` and `test_fetch_official_daily_price.py` cover fixture
      payloads trimmed from real captured responses (comma-formatted history-endpoint numbers,
      the TPEx `---` sentinel with and without a nonzero trade, TWSE's `"0.00"` no-trade
      convention) — no real network calls; 23 tests, all passing.

**Live-verification note (not in the original task list):** the TPEx endpoint failed TLS
certificate verification in the dev environment (`OpenSSL 3.0`, "Missing Subject Key
Identifier" — TPEx's chain lacks an extension OpenSSL 3.x's stricter RFC 5280 validation
requires; browsers and the OS trust store tolerate it). Fixed by adding the `truststore`
dependency and calling `truststore.inject_into_ssl()` in `shp_utils/official_api.py`, which
routes verification through the OS trust store instead of the bundled OpenSSL verifier —
verification stays fully enabled, no `verify=False`. Confirmed working end-to-end against both
live endpoints after the fix (TWSE 1,371 rows, TPEx 10,175 rows).

## 3. Historical backfill crawler (single-symbol history)

Implemented in `backfill_official_history.py`, **TWSE only** — the TPEx single-symbol history
endpoint path is still unconfirmed (see task 1.1 and design.md's open questions); the TWSE
history endpoint (`rwd/zh/afterTrading/STOCK_DAY`) was live-verified as documented in
design.md. Writes into the same `official_daily_price` table as the bulk fetcher (identical row
shape), so bulk and history data merge transparently.

- [x] 3.1 `run_backfill()` defaults `throttle_seconds=2.5` (below the ≈1 request/≥2s soft
      limit), configurable per call/CLI.
- [x] 3.2 Checkpointed per `(market, code, year_month)` in a new `official_history_raw_ranges`
      table (mirroring `fetch_corporate_actions.py`'s raw-range pattern); `is_unit_cached()`
      only treats `status = 'ok'` rows as cached, so a re-run resumes from the first
      not-yet-succeeded unit. Live-verified: a two-month TWSE 2330 backfill followed by an
      identical second invocation fetched 0 (both months skipped as cached).
- [x] 3.3 On failure, the unit is recorded with `status = 'error'` (so it is retried, not
      skipped, next run) and the inter-unit sleep backs off as
      `throttle_seconds * 2 ** consecutive_failures` (capped at `2**5`); a success resets the
      consecutive-failure counter, so an isolated blip doesn't permanently slow the crawl.
- [x] 3.4 `max_requests` and `max_seconds` are both supported (checked once per unit); either
      bounds a single invocation to a small slice.
- [x] 3.5 Idempotency comes from the same `(market, code, date)` upsert the bulk fetcher uses —
      no separate merge step needed; live-verified no duplicate rows across two full runs of the
      same range.
- [x] 3.6 `test_backfill_official_history.py`: 12 tests covering month-range/crawl-unit
      construction, real-fixture TWSE history parsing, checkpoint/resume, idempotent re-run,
      budget stop, failed-unit non-caching, and consecutive-failure backoff — fake fetch
      function, no real network calls in the suite itself.

## 4. Wire into the daily refresh

- [x] 4.1 Added as **step 5** in `run_daily.py` (moved from an initial step 6 placement — see
      4.3's ordering-bug note below), alongside the existing steps.
- [x] 4.2 Wrapped in the same `try/except Exception: logger.exception(...)` pattern as the other
      steps.
- [x] 4.3 `test_run_daily.py` (new, 4 tests): monkeypatches every step to a no-op recording
      call order, asserting all steps run, a failing step doesn't block the others, and —
      critically — that the official fetch runs _before_ the health check.
      **Live-caught ordering bug**: the official fetch was first wired as step 6 (after the
      Phase-5 health check at step 5), matching this task's original text ("MAY run in any
      order"), which was true in isolation but became false once Phase 5 added a health-check
      read of `official_daily.sqlite`. Under that order, every run's cross-check compared
      against the _previous_ run's official data (or, on a first-ever run, no official data at
      all, since the file wouldn't exist yet) — silently defeating the cross-check's purpose.
      Manual live verification during Phase 2/5 didn't catch this because it invoked fetch and
      health-check manually in fetch-then-check order, the reverse of the buggy pipeline order.
      Fixed by moving the official fetch to step 5 and the health check to step 6; the
      `daily-update-orchestration` spec's ordering scenario is updated accordingly (the
      official fetch is independent of steps 1-4 but must precede the health check).

## 5. Official-vs-Shioaji cross-check in the data-quality health check

- [x] 5.1 `_cross_check_official()` in `check_data_integrity.py` compares official rows
      against `{code}_day.csv` rows per symbol/date: close (`OFFICIAL_CLOSE_THRESHOLD = 0.01`,
      set from the 99.9%-within-0.01 reconciliation in design.md) and volume
      (`OFFICIAL_VOLUME_THRESHOLD = 0.10`, set above the known ~1.5% systematic Shioaji
      undercount so it doesn't fire on that expected bias), plus symbol/date pairs present in
      one source but missing in the other — bounded to the overlap of each source's own
      covered date range, so a date official's gradual backfill hasn't reached yet is not
      misreported as a gap (this bound was a deliberate design decision beyond the original
      task wording; see design.md's cross-check note). **Live-verified critical fix:**
      `{code}_day.csv`'s `Volume` is in board lots, official volume is in shares; comparing
      raw values without converting flagged ~1,079 of ~1,980 symbols as false "mismatches" on
      a live run. Added `SHARES_PER_LOT = 1000` conversion (applied to both the threshold math
      and the reported `shioaji_value`, so a reviewer sees like-for-like units) — re-running
      live afterward dropped flagged symbols to 32, all genuine volume-field outliers.
- [x] 5.2 Findings land in `official_cross_check_mismatches` per symbol, each entry
      `{date, field, official_value, shioaji_value}` (`field` is `close`, `volume`, or
      `presence`); `REPORT_SCHEMA_VERSION` bumped 1 -> 2 since every symbol entry's key set
      changed. Reporting only — `run_health_check()`'s flagged-count now also considers this
      field, but nothing gates on it.
- [x] 5.3 `test_check_data_integrity.py` gained a dedicated `TEST3` fixture symbol (matching
      pair within threshold, a close mismatch, a volume mismatch, a gap inside official's
      covered window, and a date outside it) plus a `TEST4` case for the close-missing/no-trade
      skip — 10 new tests, no real network calls; all existing tests updated for the schema
      bump.

## 6. Validation

- [x] 6.1 `uv run pytest` — 64 tests pass (23 fetcher + 12 crawler + 18 health-check + 11
      pre-existing).
- [x] 6.2 `uv run ruff check` and `uv run ruff format --check` pass on all files.
- [x] 6.3 Manual inspection: live-fetched both bulk endpoints (TWSE 1,371 rows, TPEx 10,175
      rows); spot-checked 2330 and 006201 directly. Zero-volume/no-close convention differs by
      market (TWSE `"0.00"`, TPEx `"---"`, sometimes with nonzero shares) — both normalized to
      `close_missing = 1` in the stored artifact (see task 1.2/2.3).
- [x] 6.4 Manual inspection: ran the TWSE backfill crawler for 2330 over two months, then
      re-ran the identical range — second run fetched 0 (both months correctly skipped as
      cached), no duplicate rows in `official_daily_price`.
- [x] 6.5 Manual inspection: ran the extended health check against the full live dataset.
      Initial run flagged 1,079 symbols (the unit-conversion bug caught here — see 5.1); after
      the fix, 32 symbols flagged, all `field: "volume"` with plausible real discrepancies
      (e.g. code 2327: official 75,283,059 vs Shioaji-converted 66,712,000 shares).

## 7. Automatic full-universe backfill mode

Phases 1–6 above were complete and committed before this phase was opened; unchecked boxes
below are new work on top of a finished change, not a regression of earlier phases. Triggered by
a request for a single, permanent, no-argument command that crawls the whole TWSE backfillable
universe gradually — newest year first, then progressively older — while `run_daily.py` keeps
running normally and Ctrl+C can stop the crawl cleanly.

- [x] 7.1 Confirmed live: the TWSE history endpoint refuses any query before ROC 99/1/4
      (2010-01-04) — `date=20091201` returns `stat: 查詢日期小於99年1月4日` with 0 rows;
      `date=20100101` returns `stat: OK`. `EARLIEST_SUPPORTED_YEAR_MONTH = "2010-01"` added and
      enforced in `build_crawl_units()` for every invocation, explicit or automatic — see
      `design.md` decision #6.
- [x] 7.2 Fixed a checkpoint bug that predates this phase: `mark_unit_fetched()` wrote
      `status='ok'` unconditionally, so fetching the current, unfinished month permanently
      hid the rest of that month from future runs. `mark_unit_fetched()` now takes a `complete`
      flag (`is_month_complete()` / `last_completed_year_month()`) and writes `'partial'` for an
      in-progress month, which `is_unit_cached()` does not treat as done.
- [x] 7.3 `load_twse_backfill_universe()` reads distinct `market='TWSE'` codes from the official
      artifact itself (1,371 codes live), not the Shioaji symbol mapping — measured and
      rejected unioning the mapping in, since 1,226 + 897 + 374 already accounts for every
      mapping code and the union would only add wasted requests. `resolve_auto_crawl_plan()`
      pairs that universe with the floor-to-last-completed-month range.
- [x] 7.4 `build_crawl_units(..., newest_first=True)` orders month-major, newest first, so the
      no-argument command finishes 2026 before touching 2025 without any parameter changing.
      `resolve_auto_budget()` applies `DEFAULT_AUTO_MAX_SECONDS = 3600` when neither
      `--max-requests` nor `--max-seconds` is given, so the bare command is always a bounded
      slice. `main()` now accepts `--codes`/`--start-year-month`/`--end-year-month` as optional
      (all three together, or all three omitted for auto mode).
- [x] 7.5 Concurrency: `fetch_official_daily_price.init_db()` sets `PRAGMA journal_mode=WAL`
      (persisted in the database file); a new `connect()` helper sets
      `PRAGMA busy_timeout=30000` on every connection (not persisted, so every connect site —
      the bulk fetcher, the crawler, and `check_data_integrity._load_official_daily_index` —
      was switched to it). Live-verified: a 40-request bounded backfill run concurrently with a
      direct call to both the daily fetch and the health check completed all three with no
      "database is locked" error, and the cross-check's flagged-symbol count held at 32 (no
      regression). A separate advisory lock file (`data/.official_backfill.lock`) prevents two
      backfills from running at once (request-rate/IP-block concern, not data integrity);
      `run_daily.py`'s own fetch deliberately does not check it.
- [x] 7.6 `run_backfill()` catches `KeyboardInterrupt` around the crawl loop, logs a clean
      fetched/skipped/failed summary instead of a traceback, and releases the lock file in
      `finally`. Since each unit commits before the next starts, the checkpoint is always an
      accurate resume point; unit-tested by raising `KeyboardInterrupt` mid-fetch and asserting
      the prior unit's checkpoint survives and the lock is released.
- [x] 7.7 Tests: `test_backfill_official_history.py` gained checkpoint-partial/ok coverage,
      interrupt-and-resume coverage, lock acquire/release/reclaim-stale-lock coverage,
      newest-first/floor-clamped `build_crawl_units()` coverage, universe/auto-plan/auto-budget
      coverage, and `main()`-level auto-vs-explicit-mode coverage (34 tests total in this file);
      `test_fetch_official_daily_price.py` gained WAL/busy_timeout coverage. Full suite: 92
      tests pass; `ruff check` and `ruff format --check` clean.
- [x] 7.8 Live verification: a bounded no-argument run (`--max-requests 5`) started at the
      newest completed month (2026-06) and wrote rows for real ETF codes absent from the
      Shioaji mapping (`00400A`–`00404A`); a second bounded run advanced to the next codes
      instead of repeating (`skipped_cached: 5`). Interactive Ctrl+C was not exercised live
      (not reliably scriptable in this environment) but is covered by a direct unit test that
      raises `KeyboardInterrupt` mid-fetch and asserts the exact resume/lock-release behavior.
