# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-29-add-tpex-corporate-actions/specs/daily-update-orchestration/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-29-add-tpex-corporate-actions/specs/daily-update-orchestration/spec.md).
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

#### Scenario: One market's corporate-action sources fail

- **WHEN** the corporate-action refresh fails for one market's sources
- **THEN** the system MUST log the failure with its full exception message
- **AND** it MUST still attempt the other market's sources, so an outage at one exchange does
  not cost the other exchange's refresh.
