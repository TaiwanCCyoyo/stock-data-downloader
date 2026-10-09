# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-dated-tpex-roster-evidence/specs/dated-tpex-roster-evidence/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-dated-tpex-roster-evidence/specs/dated-tpex-roster-evidence/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: Validate dated category evidence

The parser SHALL verify expected raw SHA256, response and table dates, successful
status, named code/name fields, row width, count and unique codes before emitting rows.
It SHALL preserve the source category, type, names and raw identity and SHALL NOT
declare ordinary-share or historical public-availability qualification.

#### Scenario: Wrong date or modified raw

- **WHEN** a response date differs from the requested date or the digest mismatches
- **THEN** parsing fails rather than filing rows under the requested date.

#### Scenario: Empty category

- **WHEN** a successful matching-date response has zero rows and zero total count
- **THEN** the output records observed_empty with unknown historical absence.

#### Scenario: Duplicate or malformed rows

- **WHEN** a row lacks its named fields, has incorrect width or repeats a code
- **THEN** parsing fails without guessing a schema or deduplicating silently.
