# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/repair-official-data-gaps/specs/official-price-source/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/repair-official-data-gaps/specs/official-price-source/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: TWSE whole-market day repair

The system MUST fetch TWSE MI_INDEX whole-market daily observations from 2010-01-04 onward with a known field-name signature, validate the response date, retain security names and raw fields, and record volume in shares. It MUST insert absent primary keys without modifying an existing official_daily_price row. Whole-market TWSE checkpoints MUST use empty code and ISO day periods, distinguishable from symbol-month units.

#### Scenario: Historical instruments and existing observations

- **WHEN** a TWSE day includes an instrument missing from the current artifact and an already-stored instrument
- **THEN** the missing row MUST be inserted with its observed name and raw fields
- **AND** the existing row and its fetched_at MUST remain unchanged.

#### Scenario: Unknown or mismatched response

- **WHEN** a nonempty response has an unrecognized field-name signature or a date differing from the requested date
- **THEN** the unit MUST fail without inserting guessed or misdated rows and MUST remain eligible to retry.

#### Scenario: No closing trade

- **WHEN** close is missing or volume is zero
- **THEN** close_missing MUST be true and close MUST be NULL, including odd-lot-only rows with missing close.

### Requirement: TWSE day candidate planning and settlement

Automatic planning MUST combine observed dates from either market from 2010-01-04 with recent weekdays missing from both markets, exclude settled TWSE day checkpoints, prioritize dates without TWSE rows and then newer dates, and process day repairs before symbol-month work. Newly recovered TWSE days MUST participate in automatic TPEx planning in the same invocation. Explicit day requests MUST support a bounded day-only run without symbol-month requests.

#### Scenario: Consecutive missed daily runs

- **WHEN** a recent weekday is absent from both markets
- **THEN** it MUST remain a TWSE day candidate even without peer-market rows.

#### Scenario: Explicit date repair keeps its request budget

- **WHEN** only `--tpex-dates` or `--twse-dates` is supplied with a request budget
- **THEN** only the explicitly requested market-days MUST be planned
- **AND** automatic work for the other market and symbol-month work MUST NOT consume that budget.

#### Scenario: Empty response awaiting publication

- **WHEN** a TWSE day request returns empty and is recent or already has TWSE rows
- **THEN** its checkpoint MUST remain partial and retryable
- **AND** an older empty day without TWSE rows MUST settle empty.

#### Scenario: Historical names do not multiply symbol requests

- **WHEN** historical MI_INDEX-only codes appear in official_daily_price
- **THEN** symbol-month planning MUST include only codes with prior TWSE symbol checkpoints or present on the newest TWSE date.

### Requirement: Opportunistic newer TWSE daily supplement

When a successful TPEx bulk observation supplies a date and TWSE bulk is missing or older, the daily fetch MUST attempt MI_INDEX for the TPEx date, insert only missing rows, and log returned and inserted counts. Failed bulk writes MUST be rolled back before a later market or supplement commits. Bulk counts MUST continue to describe bulk results, with inserted supplement rows counted separately. Supplement emptiness, malformed data or transport failure MUST log a warning and MUST NOT undo successful bulk writes or fail their result. Without a successful TPEx date, the daily fetch MUST NOT infer a supplement date. Availability at a particular clock time MUST NOT be assumed.

#### Scenario: Newer dated observations are available

- **WHEN** TWSE bulk lags and MI_INDEX returns the requested date
- **THEN** absent TWSE rows MUST be available before the next derived artifact rebuild.

#### Scenario: Supplement is unavailable

- **WHEN** MI_INDEX is empty or fails
- **THEN** successful bulk results MUST remain committed and the supplement MUST be attempted again on a later eligible run.

#### Scenario: Failed TWSE bulk write does not leak into recovery

- **WHEN** a TWSE bulk write fails after partially changing rows and TPEx bulk succeeds
- **THEN** failed TWSE changes MUST be rolled back before TPEx commits
- **AND** the dated supplement MUST preserve pre-existing TWSE observations and count only newly inserted rows.

### Requirement: Optional consecutive-failure stop

Official backfills MUST accept an optional positive consecutive-failure limit.
On reaching the limit, the run MUST preserve committed successful data and the
latest failed unit's error checkpoint, stop before requesting another unit,
and report its stopping reason. A successful fetch MUST reset the streak.
Omitting the limit MUST preserve existing retry and run-budget behavior.

#### Scenario: Provider maintenance persists

- **WHEN** the configured number of consecutive units fail
- **THEN** the run MUST record the last error before stopping and release its lock
- **AND** later units MUST remain unattempted and eligible for a later run.

#### Scenario: An intervening request succeeds

- **WHEN** a successful unit separates failed units
- **THEN** failures before that success MUST NOT contribute to the new streak.

#### Scenario: Invalid threshold

- **WHEN** a nonpositive limit is supplied
- **THEN** the CLI and callable MUST reject it before creating or modifying data.

## MODIFIED Requirements

### Requirement: Rate-limited resumable historical backfill

The system MUST provide a standalone tool to backfill official daily history for past dates via
the official history endpoints, designed to run within the endpoints' unpublished
soft rate limit without being blocked.

- The tool MUST throttle requests with a configurable minimum delay and MUST default to a rate
  below the observed soft limit.
- The tool MUST checkpoint progress per unit of work so that a re-run skips completed work and
  resumes where it stopped. TWSE supports both symbol-month STOCK_DAY units and
  whole-market MI_INDEX day units; TPEx uses whole-market trading-day units. Day
  checkpoints use an empty code and ISO date, while symbol-month checkpoints use
  a nonempty code and YYYY-MM period.
- The tool MUST back off (increasing delay, bounded) on overload signals (e.g. HTTP 429,
  connection errors) and MUST stop hammering after repeated failures, recording the failed unit.
- The tool MUST support a bounded run (a maximum request count or wall-clock budget per
  invocation) so history can be crawled in small slices across multiple runs.
- Writes MUST be idempotent: fetching an already-stored period MUST merge and de-duplicate by
  date rather than duplicating rows.
- The tool MUST support a default invocation with no symbol or date arguments, deriving its
  symbol-month universe from established symbol checkpoints and the newest TWSE date
  in the official daily artifact, and its month range from the endpoint's
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
- **THEN** it MUST select symbol-month codes from prior TWSE symbol checkpoints
  and the newest TWSE date in the official daily artifact
- **AND** it MUST also plan TWSE whole-market day repair from either market's
  observed dates and recent weekdays, prioritizing day gaps before symbol-months
- **AND** it MUST select its date range from the endpoint's earliest supported date through the
  last fully-elapsed period, most recent period first
- **AND** it MUST apply a default per-run budget rather than running unbounded.

#### Scenario: Unfinished day ages beyond the recent window

- **WHEN** a TWSE whole-market day checkpoint is `partial` or `error` and both
  markets have no price rows for that date after a long interruption
- **THEN** automatic planning MUST retain its valid ISO date as a candidate
  after it leaves the recent weekday window
- **AND** it MUST retain the supported date floor, current-date upper bound,
  settled exclusion and gap-first/newest-first ordering
- **AND** malformed periods, symbol-months and other markets MUST NOT become
  TWSE day requests.

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
- **AND** it MUST persist returned rows in the same artifact, column shape, and units
  the daily fetch writes, using insert-only writes for TWSE MI_INDEX and the existing
  upsert policy for TPEx
- **AND** it MUST checkpoint that day as completed, distinguishably from a day never attempted.

#### Scenario: A market-day returns no rows

- **WHEN** a whole-market history request returns a successful response containing no rows, and
  the endpoint gives no way to tell a non-trading day apart from a date before its data floor
- **THEN** a recent TWSE date or one already holding TWSE rows MUST remain
  retryable; an older TWSE date without rows MUST settle empty
- **AND** TPEx MUST preserve its existing empty-response settlement policy for
  gap dates and keep empty re-fetches of an already-populated day retryable.

#### Scenario: A day the daily bulk fetch already wrote

- **WHEN** a whole-market trading day holds rows written by the daily bulk fetch rather than by
  the history endpoint, and the two report volume under different definitions
- **THEN** the tool MUST treat that day as eligible for re-fetch
- **AND** after re-fetching, that day MUST carry the same volume definition as every other
  backfilled day
- **AND** a day already written by the history endpoint MUST NOT be re-fetched merely because
  it is recent.
