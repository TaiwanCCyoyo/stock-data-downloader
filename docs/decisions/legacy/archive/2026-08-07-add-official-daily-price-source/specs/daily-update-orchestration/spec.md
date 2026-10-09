# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-07-add-official-daily-price-source/specs/daily-update-orchestration/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-07-add-official-daily-price-source/specs/daily-update-orchestration/spec.md).
No production action or acquisition is authorized by this record.

## MODIFIED Requirements

### Requirement: Single one-click refresh entry point

The system MUST provide a single entry point, `run_daily.py`, that leaves all local data
(minute prices, daily prices, corporate-action index, the consolidated parquet cache, the
data-quality report, and the official daily price artifact) up to date after one invocation,
with no follow-up command required.

#### Scenario: Run the daily update

- **WHEN** `run_daily.py` runs
- **THEN** it MUST download minute price data (Shioaji)
- **AND** it MUST convert minute data to daily bars
- **AND** it MUST refresh the current year of the corporate-action index for all TWSE sources
- **AND** it MUST rebuild the consolidated `data/price_daily.parquet` cache
- **AND** it MUST fetch the latest trading day of official daily prices from the TWSE and TPEx
  bulk endpoints and refresh the official daily artifact
- **AND** it MUST run the local data-quality health check and refresh
  `data/data_quality_report.json`
- **AND** it MUST perform the download, convert, corporate-action, and parquet-rebuild steps
  in that order; the official daily fetch MAY run in any order relative to those four steps
  (it depends on none of them and none of them depend on it) but MUST run before the
  data-quality health check, because the health check's official-vs-Shioaji cross-check
  reads this run's official data — running it first would compare against stale or entirely
  absent official data.

#### Scenario: Official daily fetch fails

- **WHEN** the official daily-fetch step raises an exception or an official endpoint is
  unavailable
- **THEN** the system MUST log the failure with its full exception message
- **AND** it MUST NOT prevent the other steps from completing
- **AND** the run MUST degrade to "no official data refreshed this run" rather than aborting.

#### Scenario: Daily update runs while a historical backfill is in progress

- **WHEN** `run_daily.py` runs while the standalone historical backfill tool (see
  `official-price-source`) is separately in progress against the same official daily artifact
- **THEN** the official daily-fetch step and the data-quality health check MUST both complete
  successfully
- **AND** neither process MUST be blocked or fail due to the other holding a write lock on the
  shared artifact.

### Requirement: Step failures do not abort the run

A failure in one step of the daily update MUST NOT prevent the other steps from running, and
MUST be logged with the full exception message rather than only the exception type name. This
applies to the official daily-fetch step as it does to the existing steps.

#### Scenario: Download step fails

- **WHEN** the download step raises an exception
- **THEN** the system MUST log the failure's full exception message
- **AND** it MUST still attempt the conversion, corporate-action, parquet-rebuild,
  official-daily-fetch, and data-quality-check steps.

#### Scenario: Any step raises an exception

- **WHEN** any of the six steps of the daily update raises an exception
- **THEN** the system MUST log the exception's full message, not just its type name
- **AND** it MUST proceed to the remaining steps.
