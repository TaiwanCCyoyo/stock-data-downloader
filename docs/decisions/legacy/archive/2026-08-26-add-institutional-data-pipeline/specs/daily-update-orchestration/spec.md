# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-26-add-institutional-data-pipeline/specs/daily-update-orchestration/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-26-add-institutional-data-pipeline/specs/daily-update-orchestration/spec.md).
No production action or acquisition is authorized by this record.

## MODIFIED Requirements

### Requirement: Single one-click refresh entry point

The system MUST provide a single entry point, `scripts/run_daily.py`, that leaves all local data
(minute prices, daily prices, corporate-action index, the consolidated parquet cache,
official daily price artifact, institutional daily artifact, and data-quality report) up to
date after one invocation, with no follow-up command required.

#### Scenario: Run the daily update

- **WHEN** `scripts/run_daily.py` runs
- **THEN** it MUST download minute price data (Shioaji)
- **AND** it MUST convert minute data to daily bars
- **AND** it MUST refresh the current year of the corporate-action index for all TWSE sources
- **AND** it MUST rebuild the consolidated `data/price_daily.parquet` cache
- **AND** it MUST fetch the latest trading day of official daily prices from the TWSE and TPEx
  bulk endpoints and refresh the official daily artifact
- **AND** it MUST refresh a rolling seven-calendar-day window of TWSE and TPEx institutional
  reports, skipping completed checkpoints, in `data/institutional.sqlite`
- **AND** it MUST run the local data-quality health check and refresh
  `data/data_quality_report.json`
- **AND** it MUST perform the download, convert, corporate-action, and parquet-rebuild steps
  in that order; the two official-data fetches MAY run in any order relative to those four
  steps but MUST run before the data-quality health check.

#### Scenario: Official daily fetch fails

- **WHEN** an official daily-price or institutional-fetch step raises an exception or an
  official endpoint is unavailable
- **THEN** the system MUST log the failure with its full exception message
- **AND** it MUST NOT prevent the other steps from completing
- **AND** the run MUST degrade to no refresh for the affected official artifact rather than
  aborting.

#### Scenario: Daily update runs while a historical backfill is in progress

- **WHEN** `scripts/run_daily.py` runs while a standalone historical backfill is separately in
  progress against an official artifact
- **THEN** each official daily step and the data-quality health check MUST complete or fail
  independently
- **AND** no unrelated pipeline step MUST be aborted by a lock on another artifact.

### Requirement: Step failures do not abort the run

A failure in one step of the daily update MUST NOT prevent the other steps from running, and
MUST be logged with the full exception message rather than only the exception type name.

#### Scenario: Download step fails

- **WHEN** the download step raises an exception
- **THEN** the system MUST log the failure's full exception message
- **AND** it MUST still attempt conversion, corporate-action, parquet-rebuild, official
  daily-price, institutional, and data-quality steps.

#### Scenario: Any step raises an exception

- **WHEN** any daily-update step raises an exception
- **THEN** the system MUST log the exception's full message, not just its type name
- **AND** it MUST proceed to the remaining steps.
