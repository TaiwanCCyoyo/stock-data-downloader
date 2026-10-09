# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-26-add-monthly-revenue-pipeline/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-26-add-monthly-revenue-pipeline/tasks.md).
No production action or acquisition is authorized by this record.

## 1. Shared official-source transport

- [x] 1.1 Add a retrying byte-response helper to `shp_utils/official_api.py` without changing
      existing JSON behavior.
- [x] 1.2 Add focused tests for byte payloads, transport retries, and final failure details.

## 2. Monthly-revenue artifact

- [x] 2.1 Implement latest listed/OTC source configuration, ROC date/month parsing, defensive
      normalization, and validation in `scripts/fetch_monthly_revenue.py`.
- [x] 2.2 Implement additive SQLite schema creation, idempotent current-row upserts, append-only
      revisions, and status queries.
- [x] 2.3 Implement atomic raw snapshots and fetch metadata with SHA-256 verification fields.
- [x] 2.4 Implement bounded, resumable historical CSV backfill with encoding detection and
      per-market/month checkpoints.
- [x] 2.5 Add unit/integration tests for both markets, invalid payload rollback, idempotency,
      corrections, point-in-time metadata, snapshots, checkpoints, bounds, and status.

## 3. Daily orchestration and documentation

- [x] 3.1 Add the latest monthly-revenue refresh to `scripts/run_daily.py` as an independent,
      non-cache-blocking step.
- [x] 3.2 Extend orchestration tests to prove the step runs, failures do not stop later steps or
      backup, and failures still produce a non-zero unattended-run result.
- [x] 3.3 Document CLI usage, SQLite/raw contracts, units, source URLs, and the exact-time
      limitation in English and Traditional Chinese READMEs.

## 4. Validation

- [x] 4.1 Run focused tests for the new fetcher, shared transport, data paths, and daily pipeline.
- [x] 4.2 Run `uv run pytest` and `uv run ruff check` for the complete submodule.
- [x] 4.3 Run pre-commit against changed files and inspect any formatter changes.
- [x] 4.4 When network execution is authorized, fetch a bounded live sample and inspect SQLite/raw
      output; otherwise record the skipped live check and reason without fabricating evidence.
    - Skipped on 2026-08-26 because live network execution was not authorized; no live evidence is
      claimed.
