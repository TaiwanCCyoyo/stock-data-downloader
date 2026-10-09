# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-par-value-evidence/specs/par-value-evidence/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-par-value-evidence/specs/par-value-evidence/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: Source-bound resume evidence

The library SHALL parse held TWSE TWTB8U and TPEx pvChgRslt bytes into versioned,
JSON-compatible records containing raw SHA256, source row index, literal fields,
market/code and stop/resume dates, with resume within caller-provided inclusive
ISO bounds and stop strictly earlier (possibly before the request window).
It MUST reject invalid source status/schema, duplicate keys, wrong counts,
out-of-window dates and malformed identities without silently dropping rows.

#### Scenario: Dated source response

- **WHEN** a valid response contains multiple ordinary-stock par-value events
- **THEN** all events retain their raw row identity and validated dates
- **AND** publication/availability time remains null

#### Scenario: Empty or wrong-window response

- **WHEN** an empty valid response or an out-of-window row is supplied
- **THEN** empty is unobserved rather than complete coverage, and an out-of-window
  row raises an evidence error

### Requirement: Separate exact shares and prices

The library SHALL distinguish an explicit exchange ratio, old/new face values,
previous closing price, resume reference price and opening-auction base.
It MUST NOT derive shares by inverting rounded prices. Missing values SHALL remain
null/unresolved; explicit ratios SHALL be exact and positive and checked against
face values when present.

#### Scenario: Rounded reference price

- **WHEN** explicit ratio is 4, previous price is 109.50, reference price is 27.38
  and auction base is 27.40
- **THEN** the record retains all three prices separately and exact share ratio 4

#### Scenario: Contradictory exchange ratio

- **WHEN** explicit ratio differs from the available old-face/new-face quotient
- **THEN** parsing fails with an evidence error

### Requirement: Bound detail without invented timing

TWSE detail extraction SHALL bind its code and stop date to an independently
parsed supplied resume row and retain both raw hashes. TPEx embedded details SHALL
match their outer code/resume date and be treated as inert text, not executable HTML.

#### Scenario: Empty TWSE detail

- **WHEN** the matching detail response has no rows
- **THEN** its resume event is retained with unresolved ratio and the detail hash

#### Scenario: Conflicting detail

- **WHEN** a detail has a different code/date or duplicate required labels
- **THEN** parsing fails rather than binding it to the requested event
