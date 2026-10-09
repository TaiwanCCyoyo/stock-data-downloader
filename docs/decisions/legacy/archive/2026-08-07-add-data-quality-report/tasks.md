# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-07-add-data-quality-report/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-07-add-data-quality-report/tasks.md).
No production action or acquisition is authorized by this record.

## 1. Health-check tool

- [x] 1.1 Derive a Taiwan trading-day calendar from the union of trading dates observed
      across a reference set of high-liquidity symbols (e.g. `2330`, `0050`); cross-check
      references against each other for confidence and flag low-confidence calendar days rather
      than asserting them.
- [x] 1.2 Implement `check_data_integrity.py`: scan all `data/{code}_day.csv` files, report
      missing files, missing trading days per existing file, and zero-volume anomaly dates.
- [x] 1.3 Cross-check price gaps/jumps against `data/corporate_actions.sqlite` ex-dates;
      flag mismatches in both directions (gap with no event row; event row with no visible gap).
- [x] 1.4 Write `data/data_quality_report.json` with a stable, versioned schema (symbol →
      gap details; generated-at timestamp).
- [x] 1.5 Add tests using a small fixture data directory with known gaps and a known
      corporate-action mismatch.

## 2. Wire into the daily refresh

- [x] 2.1 Add the health check as `run_daily.py`'s fifth step, after the parquet rebuild.
- [x] 2.2 Confirm a failure in the health-check step is logged and does not prevent the run
      from completing (consistent with the existing four steps).

## 3. Fuller failure logging

- [x] 3.1 Extend each of `run_daily.py`'s five step exception handlers to log the full
      exception message, not just `type(e).__name__`.
- [x] 3.2 Replace the bare `except Exception: pass` around the Shioaji usage-limit check in
      `download_stock.py` with a logged warning.
- [x] 3.3 Add a test asserting the usage-limit check logs rather than silently swallows a
      raised exception.

## 4. Targeted re-fetch

- [x] 4.1 Add an explicit `--start`/`--end` date-range override option to `download_stock.py`
      that bypasses the incremental-resume "last timestamp" shortcut for a specific range.
- [x] 4.2 Ensure re-fetched rows merge into the existing local file without duplicating
      overlapping rows.
- [x] 4.3 Add a test (with a mocked/fake API client) confirming a forced re-fetch fills or
      overwrites the targeted range correctly without duplicating rows.

## 5. Validation

- [x] 5.1 Run `uv run pytest` — all tests green.
- [x] 5.2 Run `uv run ruff check` on changed files.
- [x] 5.3 Manual E2E: run `check_data_integrity.py` against the real local data directory,
      inspect `data/data_quality_report.json` for plausibility.
- [ ] 5.4 Manual E2E: run `run_daily.py` end-to-end and confirm the fifth step produces a
      fresh `data_quality_report.json`.
- [x] 5.5 Note any skipped Docker-backed or live-API verification with a reason.

**5.4 skipped**: `run_daily.py`'s first four steps require live Shioaji API credentials and
network access, which are not available/appropriate in this session. Step 5 itself
(`run_health_check`) was verified directly against the real local data directory in 5.3, and
its wiring into `run_daily.py` (try/except, step ordering) was verified by code review — the
fifth step is a straight call to the same `run_health_check` function, with no additional
untested logic.
