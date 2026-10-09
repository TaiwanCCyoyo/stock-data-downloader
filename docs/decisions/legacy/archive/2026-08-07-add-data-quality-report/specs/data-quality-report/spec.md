# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-07-add-data-quality-report/specs/data-quality-report/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-07-add-data-quality-report/specs/data-quality-report/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: Local price data completeness is checkable on demand

The system MUST provide a command that scans local daily price files and reports missing
symbols, missing trading days within existing symbols, and anomalous rows.

#### Scenario: Run the health check

- **WHEN** `check_data_integrity.py` runs
- **THEN** it MUST scan every `data/{code}_day.csv` under the local data directory
- **AND** it MUST write `data/data_quality_report.json` containing, per symbol: whether the
  file exists at all, missing trading days (if any), and zero-volume anomaly dates (if any).

#### Scenario: Cross-check corporate-action price gaps

- **WHEN** the health check runs
- **THEN** it MUST flag symbols where a price gap or jump coincides with no matching row in
  `data/corporate_actions.sqlite`
- **AND** it MUST flag corporate-action rows whose ex-date shows no corresponding price
  behavior in the local daily file, for manual review.

### Requirement: Data quality report has a stable schema

`data/data_quality_report.json` MUST have a schema stable enough for a caller to parse
without depending on this pipeline's internals.

#### Scenario: Inspect the report structure

- **WHEN** `data/data_quality_report.json` is read
- **THEN** it MUST contain a generated-at timestamp
- **AND** it MUST contain a per-symbol entry keyed by code, with fields for file-missing
  status, a list of missing trading days, a list of zero-volume anomaly dates, and a list of
  corporate-action mismatches.
