# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-07-add-data-quality-report/specs/daily-update-orchestration/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-07-add-data-quality-report/specs/daily-update-orchestration/spec.md).
No production action or acquisition is authorized by this record.

## MODIFIED Requirements

### Requirement: Single one-click refresh entry point

The system MUST provide a single entry point, `run_daily.py`, that leaves all local data
(minute prices, daily prices, corporate-action index, the consolidated parquet cache, and the
data-quality report) up to date after one invocation, with no follow-up command required.

#### Scenario: Run the daily update

- **WHEN** `run_daily.py` runs
- **THEN** it MUST download minute price data
- **AND** it MUST convert minute data to daily bars
- **AND** it MUST refresh the current year of the corporate-action index for all TWSE sources
- **AND** it MUST rebuild the consolidated `data/price_daily.parquet` cache
- **AND** it MUST run the local data-quality health check and refresh
  `data/data_quality_report.json`
- **AND** it MUST perform these five steps in that order.

### Requirement: Step failures do not abort the run

A failure in one step of the daily update MUST NOT prevent later steps from running, and MUST
be logged with the full exception message rather than only the exception type name.

#### Scenario: Download step fails

- **WHEN** the download step raises an exception
- **THEN** the system MUST log the failure's full exception message
- **AND** it MUST still attempt the conversion, corporate-action, parquet-rebuild, and
  data-quality-check steps.

#### Scenario: Any step raises an exception

- **WHEN** any of the five steps raises an exception
- **THEN** the system MUST log the exception's message, not just its type name
- **AND** it MUST proceed to the remaining steps.
