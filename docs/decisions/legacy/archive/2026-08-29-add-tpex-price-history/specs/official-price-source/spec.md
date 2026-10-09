# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-29-add-tpex-price-history/specs/official-price-source/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-29-add-tpex-price-history/specs/official-price-source/spec.md).
No production action or acquisition is authorized by this record.

## MODIFIED Requirements

### Requirement: Rate-limited resumable historical backfill

The system MUST provide a standalone tool to backfill official daily history for past dates via
the official history endpoints, designed to run within the endpoints' unpublished
soft rate limit without being blocked.

- The tool MUST throttle requests with a configurable minimum delay and MUST default to a rate
  below the observed soft limit.
- The tool MUST checkpoint progress per unit of work so that a re-run skips completed work and
  resumes where it stopped. A unit is per symbol and month for TWSE, whose history endpoint
  answers for one symbol at a time, and per trading day for TPEx, whose history endpoint
  answers for the whole market at once.
- The tool MUST back off (increasing delay, bounded) on overload signals (e.g. HTTP 429,
  connection errors) and MUST stop hammering after repeated failures, recording the failed unit.
- The tool MUST support a bounded run (a maximum request count or wall-clock budget per
  invocation) so history can be crawled in small slices across multiple runs.
- Writes MUST be idempotent: fetching an already-stored period MUST merge and de-duplicate by
  date rather than duplicating rows.
- The tool MUST support a default invocation with no symbol or date arguments, deriving its
  universe of symbols from the official daily artifact and its date range from the endpoint's
  earliest supported date through the last fully-elapsed period, ordered from the most recent
  period backward.
- The tool MUST NOT issue a request for a period earlier than the endpoint's earliest supported
  date, whether that period was selected automatically or given explicitly.
- The tool MUST NOT checkpoint an in-progress period (one that has not yet fully elapsed) as
  complete; only a period that has fully elapsed may be checkpointed as complete.
- The tool MUST apply a default per-run budget when invoked with no explicit request-count or
  wall-clock budget, so the default invocation never runs unbounded.
- The tool MUST stop cleanly when interrupted by an operator, without corrupting the checkpoint,
  and MUST leave the checkpoint at an accurate resume point for the next invocation.
- The tool MUST prevent two instances of itself from running concurrently, and MUST NOT prevent
  the daily official-price fetch (or any other reader/writer of the same artifact) from running
  while it is in progress.

#### Scenario: Interrupted crawl resumes without duplication

- **WHEN** the backfill tool is stopped mid-run and started again
- **THEN** it MUST skip units already completed
- **AND** re-fetched overlapping rows MUST NOT produce duplicate dates in the stored artifact.

#### Scenario: Endpoint signals overload

- **WHEN** a history endpoint returns an overload/error response
- **THEN** the tool MUST apply bounded exponential backoff before retrying
- **AND** after repeated failures it MUST pause and record the failed unit rather than
  continuing to request at full rate.

#### Scenario: Bounded invocation

- **WHEN** the backfill tool is invoked with a per-run request or time budget
- **THEN** it MUST stop once the budget is reached
- **AND** it MUST leave a checkpoint from which the next invocation continues.

#### Scenario: Default no-argument invocation

- **WHEN** the backfill tool is invoked with no symbol or date arguments
- **THEN** it MUST select its universe of symbols from the official daily artifact
- **AND** it MUST select its date range from the endpoint's earliest supported date through the
  last fully-elapsed period, most recent period first
- **AND** it MUST apply a default per-run budget rather than running unbounded.

#### Scenario: Period earlier than the earliest supported date

- **WHEN** a requested or automatically selected period is earlier than the endpoint's earliest
  supported date
- **THEN** the tool MUST NOT issue a request for that period.

#### Scenario: In-progress period is not checkpointed as complete

- **WHEN** the tool fetches a period that has not yet fully elapsed (e.g. the current calendar
  month)
- **THEN** it MUST NOT record that period as complete in the checkpoint
- **AND** a later invocation, once the period has fully elapsed, MUST re-fetch it rather than
  skipping it as already done.

#### Scenario: Operator interrupts the crawl

- **WHEN** an operator interrupts a running backfill
- **THEN** the tool MUST stop without corrupting the checkpoint or the official artifact
- **AND** re-running the same command MUST resume from the last completed unit rather than
  restarting or losing progress.

#### Scenario: Daily refresh runs while a backfill is in progress

- **WHEN** the daily official-price fetch runs while a backfill is in progress
- **THEN** both MUST complete successfully without one causing the other to fail
- **AND** a second, concurrently started backfill MUST NOT be allowed to run at the same time as
  the first.

#### Scenario: Backfill a whole-market trading day

- **WHEN** the backfill tool runs for a market whose history endpoint answers for the whole
  market at once, for a date within that endpoint's available range
- **THEN** it MUST request that market-day in a single query
- **AND** it MUST upsert the returned rows into the same artifact, column shape, and units the
  daily fetch writes
- **AND** it MUST checkpoint that day as completed, distinguishably from a day never attempted.

#### Scenario: A market-day returns no rows

- **WHEN** a whole-market history request returns a successful response containing no rows, and
  the endpoint gives no way to tell a non-trading day apart from a date before its data floor
- **THEN** the tool MUST NOT record that date as permanently empty
- **AND** it MUST leave the date eligible for a later attempt.

## ADDED Requirements

### Requirement: Response schema is selected by field-name signature

The system MUST select its field mapping by comparing a response's field-name signature against
a known table of signatures, wherever an official endpoint has published more than one response
schema over its history, and MUST fail loudly on an unrecognized signature rather than fall back
to a positional or date-derived guess.

Schema transition dates MUST NOT be used to choose a mapping. They are known only to within a
range, and a vendor may change a schema again outside any range recorded here; a signature
mismatch is the only signal that reliably distinguishes "new schema" from "wrong columns".

#### Scenario: A response carries a known schema

- **WHEN** a response's field-name signature matches a known mapping
- **THEN** the system MUST use that mapping regardless of the requested date.

#### Scenario: A response carries an unknown schema

- **WHEN** a response's field-name signature matches no known mapping
- **THEN** the system MUST fail with an error naming the unrecognized signature
- **AND** it MUST NOT store rows parsed under a guessed mapping.
