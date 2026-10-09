# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-30-promote-official-price-to-source-of-record/specs/daily-update-orchestration/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-30-promote-official-price-to-source-of-record/specs/daily-update-orchestration/spec.md).
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
- **AND** it MUST refresh the current year of the corporate-action index for all sources in
  both markets
- **AND** it MUST also request the previous year as one whole-year range, so a year is
  closed out completely rather than ending wherever the last run of December reached;
  this request MUST be cacheable, so it costs nothing once it has succeeded
- **AND** it MUST fetch the latest trading day of official daily prices from the TWSE and TPEx
  bulk endpoints and refresh the official daily artifact
- **AND** it MUST rebuild the consolidated `data/price_daily.parquet` cache
- **AND** it MUST run the local data-quality health check and refresh
  `data/data_quality_report.json`
- **AND** it MUST perform the download, convert and corporate-action steps in that order.

#### Scenario: The parquet rebuild follows the official fetch

- **WHEN** `run_daily.py` runs both the official daily fetch and the parquet rebuild
- **THEN** the official daily fetch MUST run first
- **AND** the rebuild MUST therefore see the current trading day's official rows

The cache takes its values from the official artifact, so rebuilding first would leave the
newest bar on the fallback source's values and move it to the official ones on the next
run. The two sources do not publish volume under the same definition, so that is a
definition change on the freshest bar of every affected symbol, once per day.

#### Scenario: The health check runs last among the data steps

- **WHEN** `run_daily.py` runs the official daily fetch and the data-quality health check
- **THEN** the official fetch MUST run before the health check, because the health check's
  official-vs-Shioaji cross-check reads this run's official data — running it first would
  compare against stale or entirely absent official data.

#### Scenario: One market's corporate-action sources fail

- **WHEN** the corporate-action refresh fails for one market's sources
- **THEN** the system MUST log the failure with its full exception message
- **AND** it MUST still attempt the other market's sources, so an outage at one exchange does
  not cost the other exchange's refresh.
