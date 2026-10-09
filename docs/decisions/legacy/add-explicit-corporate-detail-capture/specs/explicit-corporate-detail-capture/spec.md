# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-explicit-corporate-detail-capture/specs/explicit-corporate-detail-capture/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-explicit-corporate-detail-capture/specs/explicit-corporate-detail-capture/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: Explicit source-bound event plans

The CLI SHALL validate a nonempty `corporate-detail-plan-v1` event list with annual raw
path/hash/source/row index before requesting data or creating an output directory.
It MUST reject duplicate keys, hash drift, malformed fields, code/date/key mismatches
and arbitrary source endpoints.

#### Scenario: Mismatched raw identity

- **WHEN** any plan entry's hash or event key disagrees with the held raw row
- **THEN** the command fails without network requests or output creation.

### Requirement: Additive bounded raw capture

The system SHALL capture only the two supported fixed-origin TWSE detail endpoints into
an exclusive new output directory, preserving raw reply bytes, request URL/status/time,
source row provenance, plan/code/raw hashes and literal named fields. It MUST NOT overwrite
existing outputs, alter a database, follow redirects or retry failures.

#### Scenario: Budget or request failure

- **WHEN** a budget expires or a request returns an error, redirect, empty, malformed or
  security-denial response
- **THEN** the command stops, retains available raw bytes and records an incomplete status
  with attempted/completed counts, without attempting subsequent events.

### Requirement: Literal fields are not execution or PIT certification

The system SHALL reject unexpected/duplicate/missing field names and response-code mismatch,
and retain literal economic values with original units. It MUST leave historical knowledge
and delivery timestamps unknown, and MUST NOT infer quantities from prices or promote the
request key to an independently observed response date.

#### Scenario: Valid historical detail response

- **WHEN** a verified event receives a supported response with matching stock code
- **THEN** the captured event contains source-row effective date and request identity plus
  original response fields, with null historical availability and delivery time.
- **AND** changed company name alone does not overwrite original identity or imply code mismatch.
