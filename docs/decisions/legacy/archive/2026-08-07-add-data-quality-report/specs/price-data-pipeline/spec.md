# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-07-add-data-quality-report/specs/price-data-pipeline/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-07-add-data-quality-report/specs/price-data-pipeline/spec.md).
No production action or acquisition is authorized by this record.

## MODIFIED Requirements

### Requirement: Incremental minute-bar download

The system MUST download 1-minute K-line data per symbol from the Shioaji API and MUST
resume from the last locally stored timestamp instead of re-downloading the full history.
The system MUST also support an explicit date-range override that bypasses this
incremental-resume shortcut for a targeted repair.

#### Scenario: Resume an existing symbol

- **WHEN** `download_stock.py` runs for a symbol with an existing `{code}_min.csv`
- **THEN** it MUST read the last stored timestamp from that file
- **AND** it MUST request only data from the day after that timestamp through today
- **AND** it MUST append the new rows without rewriting existing rows.

#### Scenario: Corrupted local file

- **WHEN** the last-timestamp read from `{code}_min.csv` fails
- **THEN** the system MUST back up the file with a `.bak` suffix
- **AND** it MUST re-download the full configured history for that symbol.

#### Scenario: API usage limit reached

- **WHEN** the Shioaji API reports usage at or above the configured daily byte limit
- **THEN** the system MUST stop downloading further symbols for that run
- **AND** it MUST leave already-downloaded symbols intact
- **AND** it MUST log any exception encountered while checking usage rather than silently
  discarding it.

#### Scenario: Force re-fetch a date range

- **WHEN** a caller requests re-fetch for a specific symbol and explicit start/end dates
- **THEN** the fetcher MUST re-request that range from the Shioaji API even if a later
  timestamp already exists locally for that symbol
- **AND** it MUST merge the re-fetched rows into the existing local file without duplicating
  overlapping rows.
