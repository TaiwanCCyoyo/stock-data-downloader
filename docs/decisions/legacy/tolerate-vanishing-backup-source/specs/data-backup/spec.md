# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/tolerate-vanishing-backup-source/specs/data-backup/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/tolerate-vanishing-backup-source/specs/data-backup/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: A source file that vanishes during a backup is not a failure

A file listed in the source that no longer exists by the time it is copied or verified SHALL
be skipped and logged. It SHALL NOT fail the run, and an existing mirrored copy SHALL be kept.

#### Scenario: A lock file is removed mid-backup

- **WHEN** a transient file such as `.official_backfill.lock` is listed and then removed
  before it is copied or before it is verified
- **THEN** the run MUST log the skip and complete without failing for that file
- **AND** every other missing, short or unreadable file MUST still fail the run
