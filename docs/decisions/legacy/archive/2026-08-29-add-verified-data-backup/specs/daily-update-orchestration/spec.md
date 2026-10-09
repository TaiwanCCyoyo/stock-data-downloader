# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-29-add-verified-data-backup/specs/daily-update-orchestration/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-29-add-verified-data-backup/specs/daily-update-orchestration/spec.md).
No production action or acquisition is authorized by this record.

## MODIFIED Requirements

### Requirement: Single one-click refresh entry point

The system MUST provide a single entry point, `run_daily.py`, that leaves all local data
(minute prices, daily prices, corporate-action index, the consolidated parquet cache, and the
data-quality report) up to date after one invocation, with no follow-up command required, and
that protects the result by backing it up.

#### Scenario: Run the daily update

- **WHEN** `run_daily.py` runs
- **THEN** it MUST download minute price data
- **AND** it MUST convert minute data to daily bars
- **AND** it MUST refresh the current year of the corporate-action index for all TWSE sources
- **AND** it MUST rebuild the consolidated `data/price_daily.parquet` cache
- **AND** it MUST refresh official TWSE/TPEx bulk daily prices
- **AND** it MUST run the local data-quality health check and refresh
  `data/data_quality_report.json`
- **AND** it MUST back up the data cache
- **AND** it MUST perform these steps in that order.

#### Scenario: Machine-local settings are available to every step

- **WHEN** `run_daily.py` starts
- **THEN** it MUST load `.env` before running any step, so that a step's behavior does not
  depend on an earlier step having loaded it

## ADDED Requirements

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
