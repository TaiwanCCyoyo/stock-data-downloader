# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-26-add-institutional-data-pipeline/specs/institutional-data-pipeline/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-26-add-institutional-data-pipeline/specs/institutional-data-pipeline/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: Official daily institutional reports are normalized safely

The system MUST fetch TWSE T86 with `selectType=ALLBUT0999` and TPEx dailyTrade with
`sect=EW`, MUST select a parser by the exact returned field signature, and MUST reject an
unknown signature without writing normalized rows or a successful checkpoint.

#### Scenario: Known TWSE or TPEx schema

- **WHEN** a fetched report matches a documented schema signature
- **THEN** the system MUST normalize every source row with its market, code, name, and
  available foreign, trust, dealer, and three-institution share counts
- **AND** source-era fields that do not exist MUST remain null rather than zero.

#### Scenario: Unknown schema

- **WHEN** a report contains an unrecognized field signature
- **THEN** the fetch MUST fail with the market, date, and signature identified
- **AND** no successful `(market, date)` checkpoint MUST be written.

### Requirement: Institutional SQLite artifact preserves point-in-time provenance

The system MUST store normalized rows in `data/institutional.sqlite` table
`institutional_daily`, keyed by `(date, code)`, with source market, name, instrument type,
per-category gross/net share columns, schema signature, nullable source publication time,
and non-null fetch time. It MUST preserve each executed response as a raw JSON snapshot and
record the path relative to the database directory and its SHA-256 in
`institutional_fetch_log`.

#### Scenario: Store a report row

- **WHEN** a known report row is stored
- **THEN** repeated storage of the same `(date, code)` MUST update that row without creating
  a duplicate
- **AND** reported zero values MUST remain distinguishable from unavailable source-era fields.

#### Scenario: Publication time is unavailable

- **WHEN** the official response does not expose an authoritative publication timestamp
- **THEN** `published_at` MUST be null
- **AND** `fetched_at` MUST record when the pipeline observed the report.

### Requirement: Historical and daily collection are bounded and resumable

The system MUST provide daily-incremental and inclusive historical-range entry points for
both markets, with `(market, date)` checkpoints, throttling, and request/time budgets.

The daily entry point MUST revisit a rolling seven-calendar-day window and skip completed
checkpoints so a prior-day report that was unpublished during an earlier run is recovered.

#### Scenario: Resume overlapping range

- **WHEN** a historical command overlaps successfully checkpointed market/date units
- **THEN** those units MUST be skipped unless force refresh is requested
- **AND** uncheckpointed or failed units MUST remain eligible for collection.

#### Scenario: A report in the daily rolling window is not published yet

- **WHEN** a market date in the daily rolling window returns no rows
- **THEN** the checkpoint MUST remain partial rather than complete
- **AND** a later daily run MUST be allowed to retry it.

### Requirement: Instrument classes remain separable

The system MUST retain all rows returned by the stock-focused endpoint filters and MUST
store an `instrument_type` classification from local symbol metadata when available,
falling back to `unknown` without dropping the observation.

#### Scenario: Metadata is unavailable or incomplete

- **WHEN** a fetched code has no matching local symbol metadata
- **THEN** the row MUST be stored with `instrument_type='unknown'`
- **AND** its institutional counts and provenance MUST remain intact.
