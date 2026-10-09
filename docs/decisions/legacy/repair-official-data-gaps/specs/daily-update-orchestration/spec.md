# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/repair-official-data-gaps/specs/daily-update-orchestration/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/repair-official-data-gaps/specs/daily-update-orchestration/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: A newer dated TWSE supplement precedes rebuilding

The official fetch step MUST attempt an opportunistic dated TWSE supplement when a successful TPEx bulk response supplies a trading date and TWSE bulk has no usable dated rows or has an older date. It MUST finish that attempt before the existing price_daily.parquet rebuild. Failure or unpublished data in this optional attempt MUST preserve successful bulk writes and permit later steps to proceed. Failed bulk writes MUST be rolled back before another market or supplement is committed.

#### Scenario: TWSE bulk trails TPEx

- **WHEN** the official fetch observes a newer TPEx trading date and the dated TWSE response has rows
- **THEN** the rebuild MUST see newly inserted TWSE rows from that response.

#### Scenario: Dated TWSE response is unavailable

- **WHEN** the optional dated request fails or contains no rows
- **THEN** the pipeline MUST log the limitation and continue to rebuild from available inputs.

#### Scenario: TWSE bulk is unavailable

- **WHEN** TWSE bulk fails or normalizes to no rows and TPEx supplies a trading date
- **THEN** the official fetch MUST attempt that TPEx date via MI_INDEX and preserve committed TPEx rows
- **AND** the supplement MUST insert only absent TWSE observations.

#### Scenario: No independent date witness

- **WHEN** TPEx bulk fails or has no usable dated rows
- **THEN** the official fetch MUST NOT infer a supplement date from the wall clock.
