# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-26-add-monthly-revenue-pipeline/specs/monthly-revenue-pipeline/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-26-add-monthly-revenue-pipeline/specs/monthly-revenue-pipeline/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: Official listed and OTC monthly revenue is normalized

The system MUST fetch official monthly-revenue data for listed and OTC companies and normalize it
into `data/monthly_revenue.sqlite` without changing any existing artifact.

#### Scenario: Latest official batches are fetched

- **WHEN** the latest refresh runs successfully
- **THEN** it MUST fetch both listed and OTC official batches
- **AND** it MUST use the source `資料年月` as `revenue_month`
- **AND** it MUST store revenue amounts as integer thousands of New Taiwan dollars
- **AND** it MUST retain the company code, name, market, industry, source percentages, and note.

#### Scenario: Required source fields are missing

- **WHEN** a payload lacks a required semantic field or has an invalid revenue month
- **THEN** the affected source snapshot MUST fail explicitly
- **AND** previously committed normalized rows MUST remain unchanged.

### Requirement: The artifact preserves revisions and raw evidence

The system MUST keep a current normalized row keyed by `(market, code, revenue_month)`, an
append-only revision history, and immutable raw snapshot metadata including source URL, fetch
time, SHA-256, row count, status, and raw path.

#### Scenario: Identical data is fetched repeatedly

- **WHEN** a normalized business row has the same content hash as the current revision
- **THEN** the system MUST NOT append a duplicate revision
- **AND** it MUST update the current row's last fetched timestamp.

#### Scenario: A company corrects a reported value

- **WHEN** a normalized business row differs from the current revision for the same key
- **THEN** the system MUST append a new revision before updating the current row
- **AND** both revisions MUST remain queryable.

#### Scenario: A fetch completes

- **WHEN** an official payload is received
- **THEN** its raw bytes MUST be written atomically under `data/raw/monthly_revenue/`
- **AND** the database MUST record metadata sufficient to locate and verify that payload.

### Requirement: Availability metadata does not fabricate announcement times

The system MUST distinguish company announcement time, source table date, first local
observation, and fetch time, and MUST NOT present one as another.

#### Scenario: Latest data is observed for the first time

- **WHEN** a row is first obtained from a latest-batch endpoint
- **THEN** `available_at` MUST equal its first successful local observation time
- **AND** `availability_kind` MUST be `FIRST_OBSERVED`
- **AND** a source table date MUST be stored separately when provided.

#### Scenario: Historical data lacks an exact announcement timestamp

- **WHEN** a row is backfilled from a historical source that does not expose the company's exact
  filing timestamp
- **THEN** `announcement_at` and `available_at` MUST be null
- **AND** `availability_kind` MUST be `HISTORICAL_NO_EXACT_TIME`.

### Requirement: Historical backfill is bounded and resumable

The system MUST provide an explicit month-range backfill for listed and OTC markets with a
bounded maximum number of source-month requests per invocation.

#### Scenario: Successful historical month is requested again

- **WHEN** a non-forced backfill encounters a market/month checkpoint already marked successful
- **THEN** it MUST skip the network request.

#### Scenario: Backfill stops at its configured bound

- **WHEN** pending market/month checkpoints exceed the invocation's maximum
- **THEN** it MUST process no more than the configured maximum
- **AND** a later invocation MUST be able to continue from remaining checkpoints.

### Requirement: Status reports local coverage

The CLI MUST report normalized coverage and revision/raw-snapshot counts without making a network
request.

#### Scenario: Operator checks status

- **WHEN** the status command runs against an existing database
- **THEN** it MUST report row count, symbol count, earliest/latest revenue month, revision count,
  raw snapshot count, and counts by availability kind.
