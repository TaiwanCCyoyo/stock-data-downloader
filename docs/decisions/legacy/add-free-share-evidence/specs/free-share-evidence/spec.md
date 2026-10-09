# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-free-share-evidence/specs/free-share-evidence/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-free-share-evidence/specs/free-share-evidence/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: Exact source-specific free-share quantities

The extractor SHALL return JSON-serializable evidence with source, raw body SHA256, row identity, original named literals, quantity state and exact free-shares-per-share numerator and denominator. It MUST separate free shares from paid subscriptions and preserve zero, missing and invalid distinctly.

#### Scenario: Fractional free grant

- **WHEN** a supported source explicitly states 30.00000000 free shares per thousand
- **THEN** the ratio is exactly 3/100, independent of Decimal context, and no delivery time is inferred

#### Scenario: Paid-only rights

- **WHEN** free shares are explicitly zero and paid subscriptions are positive
- **THEN** free-share ratio is 0/1, paid literal fields are preserved, and the entire event is not declared economically empty

#### Scenario: Missing or malformed quantity

- **WHEN** the free-share cell is blank, a known missing marker, or invalid numeric text
- **THEN** its distinct missing or invalid status is returned with null ratio, never zero

### Requirement: Validated evidence identity and limited timing claims

The extractor MUST reject malformed structures, duplicate fields, unsupported sources and conflicting identities. It SHALL retain null delivery, fractional-settlement and historical-availability fields. TWSE effective dates MUST be explicitly labeled caller-bound rather than observed in the response.

#### Scenario: Detail security mismatch

- **WHEN** a TWSE detail security differs from the caller-bound annual event
- **THEN** extraction fails without an accepted record

#### Scenario: Empty annual response

- **WHEN** a validated annual response has no rows
- **THEN** its status is unobserved_empty and it cannot certify zero-event coverage
