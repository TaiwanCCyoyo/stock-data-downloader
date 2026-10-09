# Official Price Source

## Purpose

Define the baseline behavior for fetching and persisting official (exchange-sourced) daily
price data as a second authoritative source alongside Shioaji-derived data.

## Requirements

### Requirement: Official daily price fetch from exchange OpenAPI

The system MUST provide a way to fetch one trading day of whole-market daily price data from
the exchanges' official OpenAPI endpoints and persist it as a local artifact, without any API
key or credential.

- TWSE listed stocks MUST be fetched from the official TWSE bulk endpoint
  (`exchangeReport/STOCK_DAY_ALL`).
- TPEx OTC instruments MUST be fetched from the official TPEx bulk endpoint
  (`tpex_mainboard_daily_close_quotes`).
- Official volume is published in shares; the stored artifact MUST record volume in a unit
  documented by its schema (shares or 張), stated explicitly so a consumer never has to guess.
- Dates published in ROC-year form MUST be normalized to ISO `YYYY-MM-DD` in the stored
  artifact.

#### Scenario: Fetch the latest trading day for both markets

- **WHEN** the official daily fetch runs
- **THEN** it MUST request the TWSE and TPEx bulk endpoints
- **AND** it MUST write per-symbol official daily rows (open, high, low, close, volume,
  transaction count) for that trading day
- **AND** it MUST keep the official artifact distinct from the Shioaji-derived `{code}_day.csv`
  files.

#### Scenario: A symbol did not trade that day

- **WHEN** an official row reports zero volume and no close (e.g. `TradingShares = "0"`,
  `Close = "---"`) but carries a reference price
- **THEN** the system MUST record it as a zero-volume trading day
- **AND** it MUST NOT treat the row as missing data or drop it.

#### Scenario: An official endpoint is unavailable

- **WHEN** an official endpoint returns an error, times out, or returns unparseable content
- **THEN** the fetch MUST log the failure with its full message
- **AND** it MUST NOT corrupt or partially overwrite a previously stored day's artifact.

#### Scenario: A transport failure is retried like any other failure

- **WHEN** a request to an official endpoint fails at the transport level (read timeout,
  connection reset) rather than returning an error response
- **THEN** the configured retries MUST still apply to it
- **AND** the fetch MUST only give up after those retries are exhausted.

#### Scenario: One market's feed lags behind the other

- **WHEN** the two bulk endpoints report different trading days in the same run
- **THEN** the system MUST log a warning naming each market and the day it reported,
  because a lagging feed is otherwise indistinguishable from a healthy fetch and silently
  leaves that trading day missing.

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
- Where a market's daily bulk fetch and its history endpoint report different quantities for
  the same day, the tool MUST treat a day written by the bulk fetch as eligible for re-fetch,
  so one market-day carries one definition regardless of which fetcher reached it first.

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

#### Scenario: A day the daily bulk fetch already wrote

- **WHEN** a whole-market trading day holds rows written by the daily bulk fetch rather than by
  the history endpoint, and the two report volume under different definitions
- **THEN** the tool MUST treat that day as eligible for re-fetch
- **AND** after re-fetching, that day MUST carry the same volume definition as every other
  backfilled day
- **AND** a day already written by the history endpoint MUST NOT be re-fetched merely because
  it is recent.

### Requirement: Official daily artifact contract

The official daily data MUST be persisted with a documented, stable contract (file layout,
column names, units, and date/no-trade conventions) so a downstream consumer can read it as
files without depending on this pipeline's internals.

#### Scenario: Consumer reads official daily data

- **WHEN** a consumer reads the official daily artifact
- **THEN** the column names, volume unit, ISO date convention, and zero-volume/no-close
  representation MUST match the documented contract
- **AND** the artifact MUST be identifiable as official (exchange-sourced) and separable from
  the Shioaji-derived daily files.

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
