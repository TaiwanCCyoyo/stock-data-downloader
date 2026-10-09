# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-mops-rights-evidence/specs/mops-rights-evidence/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-mops-rights-evidence/specs/mops-rights-evidence/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: Exact separately labeled ordinary-share rights

The projector SHALL retain the validated issuer, announcement and original raw identity and add separately labeled free-share and subscription fields. It MUST derive the exact total free-share ratio only from two explicitly stated share quantities per thousand, never from money or subscription quantities.

#### Scenario: Mixed rights

- **WHEN** earnings grants are 30 per thousand, reserves 20, and paid subscriptions 100 at a stated price
- **THEN** the free-share ratio is 1/20, the paid terms remain separate, and no exercise or delivery is asserted

#### Scenario: Preferred subscription

- **WHEN** ordinary shareholders are offered preferred shares for subscription
- **THEN** the subscription class is preserved and the paid quantity is not added to ordinary free shares

### Requirement: Truthful unresolved states and provenance

The projector MUST distinguish explicit zero from missing, blank, unsupported and ambiguous text, preserve raw text, and inherit hash/issuer/announcement validation. It SHALL keep historical availability and revision completeness unverified, with no version selection or delivery-time inference.

#### Scenario: Preferred notice or duplicate ordinary lines

- **WHEN** there is no ordinary rights line or multiple such lines
- **THEN** the ratio is null with missing or ambiguous state, rather than inferred from a preferred line or selected arbitrarily

#### Scenario: Blank template

- **WHEN** a source line leaves the free-share amount blank
- **THEN** that component and the total ratio are unresolved rather than zero

#### Scenario: Observed empty preferred template suffix

- **WHEN** the ordinary line is followed by the exact observed empty preferred-share template
- **THEN** the ordinary quantities can be projected, full original text is retained, and nonempty or arbitrary trailing templates remain unsupported

#### Scenario: Source identity mismatch

- **WHEN** the issuer, source bytes or announcement binding is invalid
- **THEN** projection fails using the existing validated statement boundary
