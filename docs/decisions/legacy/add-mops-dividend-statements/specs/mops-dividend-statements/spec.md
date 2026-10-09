# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-mops-dividend-statements/specs/mops-dividend-statements/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-mops-dividend-statements/specs/mops-dividend-statements/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: Hash and identity qualified statement

The offline projector SHALL accept raw bytes, expected SHA-256, company and an ordinary dividend list row; it SHALL reject invalid hashes, oversized bodies, structural ambiguity and mismatching detail identity before returning a `mops-dividend-statement.v1` record. It SHALL retain raw source evidence and distinguish announcement identity from response revision identity. It SHALL perform no network or filesystem IO.

#### Scenario: Changed source bytes

- **WHEN** supplied bytes do not match the expected raw hash
- **THEN** projection raises rather than returning trusted fields

#### Scenario: Supplement with identical cash values

- **WHEN** two distinct valid announcements state identical cash-distribution terms
- **THEN** two source statements retain their distinct announcement identities; no summed entitlement or automatic merge is produced

### Requirement: Explicit projection states

The record SHALL project ordinary-share stated cash amount, dividend year/period, record date, stated ex-date and announced payment date, retaining raw evidence with each value. Known blank templates, absent labels, duplicate labels and unsupported nonblank values SHALL remain distinguishable and null. Explicit zero SHALL remain a stated decimal string. Calendar dates SHALL not be labelled actual market execution or investor receipt dates.

#### Scenario: Cash-increase template

- **WHEN** the dividend amount is blank and the payment date is an unfilled ROC template
- **THEN** both values are null/blank and cannot inherit a prior announcement's values

#### Scenario: Prior-quarter distribution

- **WHEN** an announcement in year 113 states dividend year 112 quarter 3
- **THEN** the projected period remains year 112/Q3, regardless of the payment year

#### Scenario: Conditional or unrecognized payment text

- **WHEN** the payment line contains additional nonstandard conditions or duplicate labelled dates
- **THEN** its raw text remains preserved and it does not become one parsed unconditional date

### Requirement: No economic-event or cash authority

Every record SHALL remain unlinked with unresolved payment and unverified historical availability/revision completeness. It SHALL preserve complete economic text and foreign-currency answer without claiming personal net amount, final revision, final payment date or investor cash receipt. Existing capture/data contracts SHALL remain unchanged.

#### Scenario: A fully parsed original announcement

- **WHEN** every projected field is supported
- **THEN** payment resolution still remains unresolved and event linking remains unlinked, since later amendments and investor applicability have not been adjudicated
