# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-mops-dividend-evidence/specs/mops-dividend-evidence/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-mops-dividend-evidence/specs/mops-dividend-evidence/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: Bounded explicit capture

The CLI SHALL require one numeric company ID and ROC year and accept a detail
budget between 1 and 50, default 10. It SHALL issue at most one list request plus
the budgeted detail requests, without retries, and stop on the first failure.

#### Scenario: Budget smaller than list

- **WHEN** the list has three announcement rows and the detail budget is one
- **THEN** one detail is fetched and the manifest is partial with two rows unprocessed

### Requirement: Preserve date and revision provenance

The collector SHALL retain list rows, displayed announcement date, subject,
revision-date text, response-supplied detail parameters and raw detail bodies.
It MUST NOT infer payment dates, public availability or completeness of historical
revisions from lookup keys or the current server response timestamp.

#### Scenario: Displayed date differs from key

- **WHEN** a row shows `113/07/18` and supplies `declarationDate=1130717`
- **THEN** both are retained and the supplied key is used unchanged

#### Scenario: No revised date

- **WHEN** the revised-payment column is `無`
- **THEN** it remains raw text and does not become a missing original dividend or zero payment

### Requirement: Constrained source transport

The collector SHALL use only fixed HTTPS MOPS list/detail endpoints, reject
redirects and final-URL mismatches, cap response bytes and request timeouts, and
reject unsupported detail routes, parameter sets and company identities.

#### Scenario: Untrusted detail route

- **WHEN** the source row points to another host or an unsupported route
- **THEN** no request is made to that route and the capture is non-successful

### Requirement: Additive evidence artifact

Each capture SHALL create a new directory under the config.DATA_DIR-derived raw
root or an explicitly supplied fresh output directory. It SHALL never overwrite
an existing target. Numbered raw bodies and a `mops-dividend-evidence.v1` manifest
SHALL record requests, HTTP/application outcomes, SHA-256, UTC fetch times and
unprocessed rows; complete bodies SHALL be retained before schema validation.

#### Scenario: Existing target

- **WHEN** the requested output directory already exists
- **THEN** the run fails before network access without changing its contents

#### Scenario: HTTP success with application error

- **WHEN** HTTP 200 contains application code 500
- **THEN** its raw body is preserved, status is error and CLI exits nonzero

### Requirement: Honest result states

The manifest SHALL distinguish complete bounded list capture, source_empty,
partial and error. Only successful capture of every listed row can be complete;
missing/invalid schema is not source_empty. Missing final manifest SHALL NOT be
treated as a completed run. No existing CSV/SQLite/Parquet contract SHALL change.

#### Scenario: Empty observed response

- **WHEN** a valid empty announcement list is returned
- **THEN** status is source_empty and historical/PIT completeness remains unverified

#### Scenario: Detail failure

- **WHEN** one detail succeeds and the next fails
- **THEN** prior evidence remains, run is partial and remaining rows are unprocessed
