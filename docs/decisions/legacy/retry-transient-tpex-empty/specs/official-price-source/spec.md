# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/retry-transient-tpex-empty/specs/official-price-source/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/retry-transient-tpex-empty/specs/official-price-source/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: TPEx unfinished market-day recovery

The backfill tool SHALL keep new ambiguous empty TPEx market-day responses retryable and SHALL discover unfinished whole-market TPEx dates from checkpoints independently of price-row presence, without rewriting previously settled checkpoints.

#### Scenario: Missing TPEx day temporarily returns no rows

- **WHEN** a TPEx history request returns no normalized rows for a date with no existing TPEx prices
- **THEN** its checkpoint MUST remain `partial`
- **AND** an ordinary later run MUST attempt that date again without requiring `--force`
- **AND** a later nonempty response MUST persist the prices and settle the checkpoint as `ok`.

#### Scenario: Repeated empty responses after a long interruption

- **WHEN** an unfinished TPEx date returns no rows again after a long interruption
- **THEN** the checkpoint MUST remain retryable rather than age into a settled empty result.

#### Scenario: Unfinished date exists only in checkpoints

- **WHEN** a valid TPEx whole-market `partial` or `error` checkpoint date is within the endpoint's range and not in the future, but neither market has price rows for that date
- **THEN** automatic planning MUST include that date
- **AND** planning MUST support both the current `period` and legacy `year_month` checkpoint columns without a schema migration.

#### Scenario: Existing settled and unrelated checkpoints

- **WHEN** a date already has a TPEx whole-market `ok` or `empty` checkpoint
- **THEN** automatic planning MUST leave it settled and exclude it from new work
- **AND** planning MUST NOT reinterpret malformed dates, another market's checkpoints or symbol-month units as unfinished TPEx day work.

#### Scenario: No checkpoint table yet

- **WHEN** the price database has no history checkpoint table
- **THEN** automatic TPEx planning MUST retain its existing gaps-only behavior.

#### Scenario: TWSE repair creates a new date before TPEx publishes

- **WHEN** a run inserts a TWSE market day, then its automatically selected TPEx request temporarily returns no rows
- **THEN** a subsequent ordinary automatic run MUST retry the TPEx day and be able to complete it after publication.
