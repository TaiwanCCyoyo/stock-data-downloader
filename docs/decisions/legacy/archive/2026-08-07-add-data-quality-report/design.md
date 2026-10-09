# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-07-add-data-quality-report/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-07-add-data-quality-report/design.md).
No production action or acquisition is authorized by this record.

## Context

Two independent gaps exist today. First, invisible incompleteness: nothing in this pipeline
reports whether a symbol's local daily file is missing entirely, missing individual trading
days, or has anomalous rows — a consumer only discovers this by reading unexpectedly sparse
data. Second, silent failure: `run_daily.py` logs `type(e).__name__` for each step instead of
the exception message, and the usage-limit check in `download_stock.py` has a bare
`except Exception: pass`.

## Goals / Non-Goals

**Goals:**

- Make local data completeness inspectable via one script and one stable JSON artifact.
- Run that check automatically as part of the existing one-click daily refresh.
- Turn silent exception swallowing into logged records with enough detail to act on.
- Give an explicit, targeted way to repair a gap once the report identifies one.

**Non-Goals:**

- No automatic backfill triggered by the health check itself — repair stays a manual,
  explicit re-fetch invocation.
- No opinion on how a consumer displays or reacts to the report — this pipeline stops at
  producing the artifact, per the decoupling rule in `openspec/config.yaml`.

## Decisions

**1. Health check is a standalone script producing a versioned JSON artifact, run as the
last step of `run_daily.py`.**
`check_data_integrity.py` scans every `{code}_day.csv`'s date index against a derived Taiwan
trading-day calendar and against `corporate_actions.sqlite` ex-dates for plausibility.
Running it as `run_daily.py`'s fifth step means a fresh report exists after every daily
refresh with no separate invocation required, mirroring how the parquet rebuild was folded
in as the fourth step. Alternative considered: a fully separate, unscheduled script —
rejected because a report that's only as fresh as the last manual run understates its own
value.

**2. Trading-day calendar derived from reference symbols, not an external calendar source.**
No calendar source exists in this repo. Deriving it from the union of trading dates across a
small set of high-liquidity symbols (e.g. `2330`, `0050`) and cross-checking references
against each other keeps the tool self-contained, at the cost of needing at least one
reference symbol's data to be itself complete.

**3. Re-fetch is scoped to a single symbol and explicit date range, reusing existing
incremental-download logic.**
`download_stock.py` already resumes from the last timestamp per symbol; this change adds an
explicit override so a specific range can be re-requested even when a later timestamp
already exists locally — repairing a hole in the middle of history rather than only
extending the end. Merge logic drops duplicate rows on the overlapping range.

## Risks / Trade-offs

- [Risk] A naive trading-calendar derivation (union of dates seen across reference symbols)
  could itself be wrong if a reference symbol shares a miscoded gap → Mitigation:
  cross-check at least two reference symbols against each other and flag low-confidence
  calendar days rather than asserting them as ground truth.
- [Risk] Running the health check as `run_daily.py`'s fifth step adds runtime to every daily
  refresh → Mitigation: the check only reads local files (no network calls), keeping it fast
  relative to the download step.
- [Risk] False positives could train operators to ignore the report → Mitigation: keep the
  report itemized per symbol so specific gaps can be verified quickly against source data.

## Migration Plan

- Purely additive: new script, new artifact, one new `run_daily.py` step. Existing consumers
  of this pipeline are unaffected until they choose to read the new artifact.
- Rollback: remove the fifth step from `run_daily.py` and stop running
  `check_data_integrity.py`; no other pipeline behavior depends on it.
