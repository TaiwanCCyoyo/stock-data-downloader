---
description: Check today's scheduled-task logs (run_daily.py, backfill_official_history.py) and data_quality_report.json for problems. Use when asked to check today's log, check if the daily download ran, or check for errors in the automated pipeline.
---

# Check Daily Log

Report, in Traditional Chinese, whether today's Windows Task Scheduler runs
(`StockProject-DailyUpdate`, `StockProject-OfficialBackfill`) succeeded and
whether anything needs attention. This is a read-only check — do not modify
any file or re-run any script unless the user explicitly asks.

Resolve the primary production checkout before reading its scheduled-run status.
A worktree's logs and data are a partial snapshot; missing files there do not
establish a production failure. See [operations](../../docs/operations.md).

## Steps

1. Resolve today's local date as `YYYYMMDD` (`date +%Y%m%d`).
2. Look for `logs/run_daily_<date>.log` and
   `logs/backfill_official_history_<date>.log` in this repo.
    - If a log is missing, say so plainly — it means that scheduled task has
      not fired yet today (could still be pending, e.g. before 18:00 for the
      daily update) or the machine was off/asleep through every scheduled
      trigger today. Don't guess which; report the absence and let the user
      decide if it's expected.
3. For each log that exists, scan for problems: lines containing `ERROR`,
   `Exception`, `Traceback`, or `失敗`. `run_daily.log` marks each pipeline
   step with a `--- 步驟 N: ... ---` header — use those to say which step(s)
   (if any) logged an exception, not just "something failed somewhere".
    - Also grep for `disagree on the trading day`. That warning means one
      exchange's bulk endpoint was still serving an older day than the other
      (TWSE did this on 2026-08-19), so that market is missing a trading day
      until the per-symbol backfill covers the month. It is a WARNING, not an
      ERROR, so the scan above will not catch it.
4. Check that step 1 actually saw the whole market, not just that it exited
   cleanly. A partial contract list produces a run that logs no error at all:
   on 2026-08-27 and 08-28 step 1 reported success with 3 and 5 symbols, step 2
   skipped conversion, and the parquet was rebuilt with byte-identical totals,
   while two trading days of bars were never downloaded. Three lines settle it,
   and each one is a single grep:

    - `合約清單: status=... TSE=... OTC=...` — status must be
      `FetchStatus.Fetched` and TSE must be in the tens of thousands. Anything
      else means the contract list was read while it was still downloading.
    - `對照表筆數: 既有 N -> 這次讀到 M` — M should be within a few hundred of
      N. A large drop is the failure above; the run now refuses to write it.
    - `Saved ... (latest bar YYYY-MM-DD, carried by N symbols)` — the date must
      be the trading day just closed, and N must be in the ~1,950 band. A stale
      date means the cache is frozen; a fresh date carried by a handful of
      symbols means the contract list collapsed.

    Runs from before 2026-08-29 do not have these lines. For those, the
    equivalent (weaker) signal is `共取得 N 檔標的` in step 1, which should be
    ~1,970 on a trading day.

5. Read `data/data_quality_report.json` if present. It is schema v3 as of
   2026-09-02 and its subject is `price_daily.parquet`, the artifact research
   reads -- not the Shioaji day CSVs it audited before. Summarize:

    - `checks.<name>.symbol_count` / `finding_count` for the three checks:
      `price_gap_without_ca_event`, `ca_event_without_price_gap`,
      `unexplained_fallback`. `unexplained_fallback` is the one most likely to
      catch a real problem: it means official covers a symbol on both sides of
      a day the parquet had to fill from the Shioaji fallback. It was 0 when
      the check was introduced, so any non-zero value is news.
    - `coverage.latest_day_official_share`. Below 1 means the newest bar is
      partly provisional: the official feed lags a trading day and those rows
      are rewritten once the backfill catches up. Roughly 0.44 on the evening
      of a trading day is normal; still low a day later is not.
    - `symbols` lists only symbols with a finding, so its size is the flagged
      count. The scanned total is `coverage.symbol_count`.

    A v2 report (`file_missing`, `zero_volume_anomaly_dates`,
    `official_cross_check_mismatches`, and a `symbols` map holding every
    symbol) predates that change. It flagged 2,593 of 2,603 symbols, almost all
    structurally, so do not read its counts as defect counts.

6. Optionally, for backfill progress context, count completed rows in the
   checkpoint table:

    ```powershell
    uv run python -c "import sqlite3; c = sqlite3.connect('data/official_daily.sqlite'); print(c.execute('SELECT COUNT(*) FROM official_history_raw_ranges').fetchone())"
    ```

    Treat this as a rough progress indicator only — do not compute or claim a
    precise ETA.

7. Summarize in a few sentences: did today's runs happen, did anything error,
   what (if anything) needs the user's attention. If everything is clean, say
   so briefly rather than padding the report.
