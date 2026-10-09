# Daily Update Orchestration

## Purpose

Define the baseline behavior for the one-click daily update entry point that keeps all local data artifacts current.

## Requirements

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

### Requirement: Step failures do not abort the run

A failure in one step of the daily update MUST NOT prevent the other steps from running, and
MUST be logged with the full exception message rather than only the exception type name. This
applies to the official daily-fetch, monthly-revenue, institutional and value-chain
classification steps as it does to the existing steps.

#### Scenario: Download step fails

- **WHEN** the download step raises an exception
- **THEN** the system MUST log the failure's full exception message
- **AND** it MUST still attempt the conversion, corporate-action, parquet-rebuild,
  official-daily-fetch, monthly-revenue, institutional, value-chain-classification, and
  data-quality-check steps.

#### Scenario: Any step raises an exception

- **WHEN** any step of the daily update raises an exception
- **THEN** the system MUST log the exception's full message, not just its type name
- **AND** it MUST proceed to the remaining steps.

### Requirement: Scheduled unattended execution

The system MUST be suitable for unattended scheduled execution (e.g. Windows Task Scheduler) without requiring interactive input beyond configured environment variables.

#### Scenario: Run with no operator present

- **WHEN** the daily update is started by a scheduler rather than by an operator
- **THEN** it MUST run to completion without prompting for input
- **AND** it MUST take every setting it needs from arguments or configured environment
  variables.

### Requirement: The daily run reports failure

`run_daily.py` MUST exit with a non-zero status when any step failed, so that an unattended
scheduler can distinguish a bad run from a good one. Steps MUST still be isolated from each
other: a failure MUST NOT prevent later steps from running.

#### Scenario: A step fails

- **WHEN** any step raises
- **THEN** the remaining steps MUST still run
- **AND** the process MUST exit non-zero

#### Scenario: Every step succeeds

- **WHEN** no step raises
- **THEN** the process MUST exit zero

#### Scenario: Nothing new to download

- **WHEN** the download step reports zero updated symbols, as on a non-trading day
- **THEN** this MUST NOT count as a failure
- **AND** the process MUST exit zero

### Requirement: A failed cache write blocks the backup

The backup step MUST be skipped when any step that writes the price cache (download,
convert, parquet rebuild) failed, because copying a partially written file over a good
backup copy is the one way an additive backup can lose data. Failures in steps that do not
write the price cache MUST NOT block the backup. The corporate-action refresh is one of
those: it writes its own database one transaction per fetched range, so a failure there
cannot leave a truncated price file, and blocking on it trades a transient exchange outage
for a night with no backup of the data that is expensive to reacquire.

#### Scenario: A cache-writing step failed

- **WHEN** the download, convert, or parquet step failed
- **THEN** the backup MUST be skipped
- **AND** the reason MUST be logged

#### Scenario: Only a non-cache step failed

- **WHEN** the corporate-action refresh, the official-price fetch, or the health check
  failed, and no price-cache-writing step did
- **THEN** the backup MUST still run
