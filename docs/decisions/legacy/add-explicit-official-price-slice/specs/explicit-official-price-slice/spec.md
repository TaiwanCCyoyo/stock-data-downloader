# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-explicit-official-price-slice/specs/explicit-official-price-slice/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-explicit-official-price-slice/specs/explicit-official-price-slice/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: Explicit read-only official selection

The system SHALL export one specified TWSE or TPEx market, an explicit code list
and an inclusive ISO date interval from an existing official database without
joining current metadata or requiring Shioaji CSV files. Reads SHALL be
transactionally consistent and read-only. Invalid selections or an exceeded
positive row budget SHALL fail without creating output.

#### Scenario: A historical code has no current CSV

- **WHEN** the selected code has official rows but no CSV or current metadata
- **THEN** its rows SHALL be exported for the selected market and interval
- **AND** rows for other markets, codes or dates SHALL not be included.

#### Scenario: Bounded read would exceed its budget

- **WHEN** more than the configured maximum row count matches
- **THEN** the export SHALL fail, not return a truncated dataset.

### Requirement: Preserve nullable source values and identity

`prices.parquet` SHALL contain market, code, date, name, open, high, low, close,
volume, value, transactions, close_missing, raw_fields_json and fetched_at in
that order. Volume SHALL remain shares. Nullable numeric fields and raw source
strings SHALL be retained without adjustment, filling or stock classification.
The schema SHALL remain explicit for empty or all-null output. Duplicate source
keys or invalid source dates/identity/close_missing flags SHALL be rejected.

#### Scenario: Missing close with positive or zero shares

- **WHEN** a selected source row has a null close
- **THEN** the row SHALL remain present with its original shares and missing flag
- **AND** no synthetic price, suspension label or fill SHALL be inferred.

#### Scenario: No rows for a requested code

- **WHEN** some or all requested codes have no selected rows
- **THEN** the manifest SHALL list those codes explicitly as missing
- **AND** the exporter SHALL not invent records or certify complete coverage.

### Requirement: Additive export evidence

The exporter SHALL require a destination directory that does not already exist.
It SHALL write prices first and a final manifest containing the exact selection,
observed/missing codes, units, selected-row SHA-256, output SHA-256 and a false
coverage-certification flag. The selected-row digest SHALL identify canonical
column-ordered source rows, not claim to authenticate the complete database or
original response. No existing cache, database, schedule or merged-cache
behavior SHALL change.

#### Scenario: Existing destination

- **WHEN** the chosen output directory already exists
- **THEN** export SHALL fail without overwriting any content.

#### Scenario: Recompute evidence

- **WHEN** the emitted Parquet and the ordered source selection are read again
- **THEN** their bytes and canonical rows SHALL reproduce their manifest hashes
- **AND** a changed input value SHALL change the selected-row digest.
