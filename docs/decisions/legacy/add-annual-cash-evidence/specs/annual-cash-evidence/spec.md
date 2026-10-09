# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-annual-cash-evidence/specs/annual-cash-evidence/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-annual-cash-evidence/specs/annual-cash-evidence/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: Source-specific annual cash projection

The API SHALL accept held bytes for `twse/twt49u` or `tpex/exdailyq` and emit an
`annual-cash-evidence.v1` JSON-compatible object with source SHA256 and per-row
code, ISO effective date, source row index, original event kind, original cash
field text, nullable decimal cash text and amount status/basis.

#### Scenario: TWSE cash-only versus mixed rights

- **WHEN** TWSE rows are explicitly 息, 權息 or 權
- **THEN** only 息 projects `權值+息值` into cash evidence; the others retain null
  cash with `requires_detail` or `cash_not_indicated` rather than fabricating zero

#### Scenario: TPEx explicit cash column

- **WHEN** a TPEx row includes a valid `現金股利` value
- **THEN** the amount is projected independently of its 權/息 type and explicit zero
  remains distinguishable from missing or malformed text

### Requirement: Provenance and strict structural validation

The API SHALL bind by field names, retain original precision and raw SHA256, reject
malformed structure, unknown event types, invalid dates and duplicate code/date
keys, and SHALL NOT replace missing/invalid numeric values with zero.

#### Scenario: Reordered valid fields

- **WHEN** source fields and matching cells are reordered
- **THEN** projected economic values and identities remain the same

#### Scenario: Missing or malformed numeric field

- **WHEN** a cash cell is missing text or is not a finite nonnegative decimal
- **THEN** the output contains null cash and a distinct missing/invalid status

#### Scenario: Empty response

- **WHEN** the source has no rows
- **THEN** the result is `unobserved_empty`, not proof of a complete event-free interval

### Requirement: No implicit execution or acquisition

The helper SHALL perform no network or filesystem writes and SHALL leave historical
availability and payment time null. It SHALL NOT infer net credited cash, tradability,
stock eligibility or use unrelated current financial-report fields.

#### Scenario: Historical response retrieved later

- **WHEN** held bytes contain an old effective date and newer metadata
- **THEN** only source-bound event evidence is returned; no historical publication
  time or payment timestamp is manufactured
