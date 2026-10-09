# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-30-promote-official-price-to-source-of-record/specs/price-data-pipeline/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-30-promote-official-price-to-source-of-record/specs/price-data-pipeline/spec.md).
No production action or acquisition is authorized by this record.

## MODIFIED Requirements

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
