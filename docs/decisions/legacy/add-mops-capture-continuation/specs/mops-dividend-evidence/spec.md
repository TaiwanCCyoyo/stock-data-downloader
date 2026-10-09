# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-mops-capture-continuation/specs/mops-dividend-evidence/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-mops-capture-continuation/specs/mops-dividend-evidence/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: Opt-in continuation past a proven business refusal

The capture SHALL keep its stop-on-first-failure behaviour unless a caller opts in.
When opted in, a detail response whose body matches the observed refusal shape and
names the requested company SHALL mark that row `unavailable` with a reason and
SHALL NOT be retried. An issuer-scoped refusal SHALL mark the remaining selected
rows `not_attempted_issuer_refused` without requesting them. HTTP errors, final-URL
mismatches, unfamiliar messages and schema errors SHALL still end the capture.

#### Scenario: Default behaviour is unchanged

- **WHEN** no hook is supplied and the first detail is refused
- **THEN** the capture ends with status `error`, the row is `failed` and later rows stay `unprocessed`

#### Scenario: Issuer refusal with continuation

- **WHEN** continuation is enabled and the first of three details is refused with `該 <company> 公開發行公司不繼續公開發行！`
- **THEN** one row is `unavailable`, two are `not_attempted_issuer_refused`, no further request is made and the status is `complete_with_unavailable`

#### Scenario: Issuer refusal after the detail budget boundary

- **WHEN** continuation is enabled, three rows are selected, `max_details` is one, and the first detail is refused for the issuer
- **THEN** the refused row is `unavailable` and both remaining selected rows are `not_attempted_issuer_refused`, rather than `unprocessed`

#### Scenario: A refusal naming another company

- **WHEN** a refused body names a different company ID
- **THEN** it is not treated as a business refusal and the capture ends

#### Scenario: Blocked source

- **WHEN** a detail request returns HTTP 403 or 429
- **THEN** the row is `failed` and the capture ends; nothing is classified as a refusal

### Requirement: Row accounting partitions every listed row

A manifest produced with any hook SHALL include `row_accounting`, in which every
listed row is counted exactly once among `captured`, `unavailable`, `failed`,
`unprocessed`, `not_selected`, `not_attempted_issuer_refused` and
`not_attempted_target_found`. `unprocessed_rows` SHALL count only rows that were
never attempted because of an abort or the detail budget.

#### Scenario: Refusals are not unprocessed

- **WHEN** one row is refused and the rest are skipped for the same issuer
- **THEN** `unprocessed_rows` is zero and the refusal and skips are counted in their own buckets

### Requirement: Row selection preserves listed-row identity

When a selector is supplied, every selected row SHALL be one unique original list-row object.
The capture SHALL reject copied, duplicate, or foreign selected rows before requesting a detail.

#### Scenario: Invalid selector output has no detail side effect

- **WHEN** a selector returns a copy, duplicate, or foreign row
- **THEN** the manifest records `invalid_selected_rows` and only the list request has been made
