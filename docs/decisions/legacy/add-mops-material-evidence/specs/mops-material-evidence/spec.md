# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-mops-material-evidence/specs/mops-material-evidence/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-mops-material-evidence/specs/mops-material-evidence/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: Bounded explicit acquisition

The CLI SHALL require company, ROC year and month, accept a 1–50 detail budget, and issue no more than one list plus that many details against fixed official MOPS routes. It SHALL stop on the first source error, without retry, filtering announcements by subject, or scheduling further work.

#### Scenario: Partial month capture

- **WHEN** a valid list contains nine announcements and the detail budget is one
- **THEN** the manifest reports partial, nine listed, one captured, eight unprocessed, and retains every list row

#### Scenario: Invalid scope

- **WHEN** month is absent, outside 1–12, or a boolean passed through the Python API
- **THEN** capture fails before creating output or requesting the source

### Requirement: Truthful raw evidence and identity

The collector SHALL write a new `mops-material-evidence.v1` manifest and exclusive raw response files beneath a fresh explicit directory or `config.DATA_DIR/raw/mops/material_announcements/<unique-id>`. It SHALL record exact request URL, parameters, timing, HTTP/application status, response hash, capture mode and source outcome. It SHALL preserve displayed publication date/time, original subject, detail lookup parameters and complete explanation, matching detail identity to its list row. It MUST NOT produce a normalized cash event or claim historical coverage, PIT availability or revision completeness.

#### Scenario: Conditional payment explanation

- **WHEN** a detail contains original and changed payment dates with an unaffected-region exception
- **THEN** all condition text is preserved unchanged, with no universal payment-date inference

#### Scenario: Different lookup and publication dates

- **WHEN** enterDate differs from the displayed date and the detail matches its list row
- **THEN** both source values remain distinct and validation does not replace the displayed date

#### Scenario: Padded subject

- **WHEN** the detail subject matches except for trailing whitespace
- **THEN** identity validation succeeds while both raw values remain preserved

### Requirement: Safe failures and existing compatibility

The collector SHALL reject unrecognized routes, parameter shapes, unsupported market values, identity mismatch, duplicate/missing titles, redirects and oversized responses. Complete error bodies SHALL be archived when available within the cap. Source-empty SHALL mean only a validated empty response, not complete historical absence. Filesystem failure SHALL raise without a false completed manifest. Existing dividend capture API and `mops-dividend-evidence.v1` semantics SHALL remain unchanged.

#### Scenario: Unsupported remote link

- **WHEN** a list detail link has an unrecognized route or marketKind other than the observed otc
- **THEN** no request follows that link, the run records an error and the row is failed rather than captured

#### Scenario: Source or storage failure

- **WHEN** a request fails after one successful detail
- **THEN** the run reports partial with the failure and remaining rows; a storage exception instead raises and does not publish a completed manifest

#### Scenario: Existing dividend consumer

- **WHEN** an existing caller invokes the dividend capture API
- **THEN** its output schema, list parameters, identity checks and CLI exit behavior remain compatible
