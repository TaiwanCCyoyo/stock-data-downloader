# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-26-add-monthly-revenue-pipeline/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-26-add-monthly-revenue-pipeline/design.md).
No production action or acquisition is authorized by this record.

## Context

Official monthly-revenue data has two useful but materially different surfaces. The TWSE
OpenAPI exposes the latest listed/OTC batch as JSON, while MOPS exposes month-addressable
historical CSV artifacts. The latest response can be observed repeatedly from now forward;
historical files reconstruct values but do not expose each company's exact submission time.
The shared `official_api` helper currently supports JSON only, and `run_daily.py` already
isolates independent official-data steps from the price-cache backup gate.

The source uses New Taiwan dollars in thousands. Its `資料年月` identifies the revenue period;
`出表日期` identifies a source-produced table date and must not be relabeled as an individual
company announcement timestamp.

## Goals / Non-Goals

**Goals:**

- Acquire listed and OTC monthly revenue from official, credential-free sources.
- Preserve normalized current rows, append-only revisions, and immutable raw evidence.
- Make time provenance explicit enough that downstream users can exclude historical rows whose
  exact availability is unknown.
- Provide a resumable, bounded historical backfill and a cheap latest-batch daily refresh.
- Keep failure isolation and data paths consistent with the existing pipeline.

**Non-Goals:**

- Reconstruct unavailable company filing times or infer them from the revenue period.
- Add strategy logic or a consumer-specific join.
- Fetch emerging/public-company-only rows in the first version.
- Replace or mutate price, corporate-action, or symbol metadata artifacts.

## Decisions

### Use official latest JSON plus official historical CSV

`t187ap05_L` and `t187ap05_O` are the latest-batch sources, but they live on _different_
hosts: the listed batch is `openapi.twse.com.tw/v1/opendata/t187ap05_L` and the OTC batch is
`www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O`. TWSE's OpenAPI host does not serve the OTC
batch, and asking it for one is not an error you can see from the status code -- the path
302s to `openapi.twse.com.tw/404.html`, which answers **HTTP 200 with HTML**, so
`raise_for_status()` passes and the failure only surfaces as a JSON parse error. The same
host confusion is already recorded for institutional data in `docs/institutional-sources.md`,
and the price side already reads TPEx from `www.tpex.org.tw/openapi/v1/`.

Historical backfill uses MOPS' month-addressed `t21sc03` CSV for `sii` and `otc`, which is
served from `mopsov.twse.com.tw` for both markets. All three normalize into the same row
contract.
Alternative: scrape interactive MOPS HTML. Rejected because the official machine-readable
artifacts are simpler, more stable, and do not require browser state.

### Separate value history from availability evidence

`data/monthly_revenue.sqlite` contains a current table, append-only revisions, and raw-snapshot
metadata. Each normalized revision records `source_report_date`, nullable `announcement_at`,
nullable `available_at`, `availability_kind`, `first_observed_at`, and `last_fetched_at`.

Latest-batch rows use their first successful local observation as `available_at` with
`availability_kind=FIRST_OBSERVED`; this is conservative and auditable, though later than the
true publication. Historical CSV rows use `availability_kind=HISTORICAL_NO_EXACT_TIME` and keep
`available_at`/`announcement_at` null. Alternative: convert `出表日期` or the statutory filing
deadline into a timestamp. Rejected because neither is the company's actual filing time and a
precise-looking value would invite look-ahead bias.

### Keep revisions append-only and current rows idempotent

A stable hash of normalized business fields identifies a revision. Re-fetching identical data
updates only `last_fetched_at`; changed values append a revision and update the current row.
This preserves corrections without duplicating identical fetches. Raw payload files are written
atomically and recorded with SHA-256, source URL, row count, status, and fetch time.

### Extend the shared official HTTP helper for bytes

Add a retrying byte-response helper beside the existing JSON helper, using the same verified TLS,
headers, timeout, and backoff behavior. Alternative: put another requests/retry loop in the
revenue module. Rejected to avoid divergent network behavior.

### Daily refresh is independent and non-cache-blocking

The latest fetch runs as its own `run_daily.py` step. Failure makes the overall unattended run
non-zero and is logged, but does not stop later steps or suppress the price-cache backup because
the revenue database is transactionally independent of the expensive price cache.

## Risks / Trade-offs

- [Risk] MOPS changes a field name or file encoding. -> Validate required semantic fields, accept
  UTF-8/Big5-family encodings, preserve raw bytes, and fail the affected snapshot explicitly.
- [Risk] The latest endpoint still serves the previous month. -> Trust `資料年月`, never derive
  the period from today's date, and upsert only the period contained in the payload.
- [Risk] A correction overwrites earlier evidence. -> Hash normalized fields and append a new
  revision before updating the current row.
- [Risk] Historical data is accidentally treated as exact point-in-time. -> Leave exact
  availability null and attach an explicit evidence kind.
- [Risk] Daily polling observes a release later than its true time. -> Document that
  `FIRST_OBSERVED` is conservative and retain `source_report_date` separately.

## Migration Plan

The change is additive. Deploy code and tests, then let the daily refresh create the database and
first raw snapshots. Historical backfill is an explicit operator command and never rewrites other
artifacts. Rollback removes the daily step and stops invoking the CLI; existing SQLite/raw files
remain readable and are not deleted automatically.

## Open Questions

- Whether an official machine-readable source exposes per-company historical submission times.
  Until verified, the schema reserves `announcement_at` but the first version leaves it null.
