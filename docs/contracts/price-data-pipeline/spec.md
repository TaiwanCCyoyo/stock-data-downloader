# Price Data Pipeline

## Purpose

Define the baseline behavior for acquiring, converting, and caching Taiwan (TWSE/TPEx) stock minute and daily price data.

## Requirements

### Requirement: Incremental minute-bar download

The system MUST download 1-minute K-line data per symbol from the Shioaji API and MUST resume from the last locally stored timestamp instead of re-downloading the full history. The system MUST also support an explicit date-range override that bypasses this incremental-resume shortcut for a targeted repair.

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
- **AND** it MUST log any exception encountered while checking usage rather than silently discarding it.

#### Scenario: Force re-fetch a date range

- **WHEN** a caller requests re-fetch for a specific symbol and explicit start/end dates
- **THEN** the fetcher MUST re-request that range from the Shioaji API even if a later timestamp already exists locally for that symbol
- **AND** it MUST merge the re-fetched rows into the existing local file without duplicating overlapping rows.

### Requirement: Daily bar conversion from minute bars

The system MUST convert `{code}_min.csv` minute bars into `{code}_day.csv` daily bars, discarding rows that fail OHLC consistency checks.

#### Scenario: Convert a minute file

- **WHEN** `convert_stock_price.py` processes a `{code}_min.csv` file
- **THEN** it MUST resample to daily Open/High/Low/Close/Volume
- **AND** it MUST exclude rows where High/Low/Volume are inconsistent with Open/Close/Volume bounds
- **AND** it MUST attach 5/10/20/60-period SMA and EMA columns to the output.

### Requirement: Consolidated read-optimized price cache

The system MUST provide a single consolidated `data/price_daily.parquet` file for
downstream consumers, built from the official daily artifact as the source of record and
falling back to the Shioaji-derived `{code}_day.csv` files where official data does not
cover a symbol or date.

The cache MUST remain a derived, rebuildable artifact: both underlying sources stay the
source of truth and the parquet MUST be reproducible from them.

- `Volume` MUST be expressed in lots and MUST be a floating-point value, because the
  official artifact publishes shares and odd-lot-inclusive figures do not divide evenly.
- Each row MUST carry the source it came from, so a consumer can distinguish official rows
  from Shioaji-derived ones without inspecting the upstream artifact.
- Moving averages MUST be computed over the merged per-symbol series, not copied from the
  Shioaji-derived files, and MUST NOT be computed across symbol boundaries.

#### Scenario: Rebuild the parquet cache

- **WHEN** `build_price_parquet.py` runs
- **THEN** it MUST write a single `data/price_daily.parquet` containing a `Code` column
  identifying the symbol for each row
- **AND** every row MUST carry a source indicator
- **AND** it MUST report how many rows came from each source and the earliest date covered.

#### Scenario: A symbol is covered by both sources

- **WHEN** a symbol and date exist in both the official artifact and the Shioaji-derived
  daily file
- **THEN** the official values MUST be used
- **AND** the row MUST be marked as official-sourced.

#### Scenario: A symbol the official artifact does not cover

- **WHEN** a symbol has Shioaji-derived daily bars but no official rows, as is the case for
  the market index
- **THEN** the symbol MUST still appear in the cache with its Shioaji-derived values
- **AND** the row MUST be marked as Shioaji-sourced
- **AND** the symbol MUST NOT be dropped merely because the official artifact lacks it.
