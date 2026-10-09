# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/streamline-repository-maintenance/specs/repository-maintenance/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/streamline-repository-maintenance/specs/repository-maintenance/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: Targeted Python type checks

Normal Python commit checks MUST run both Mypy and Pyright on the submitted files,
using this repository's own uv environment and scripts import path. Mypy batches
MUST run serially to avoid concurrent cache writes. Type errors MUST fail the check.
Mypy MUST retain imported type information without reporting diagnostics inside
modules that were not explicitly submitted to the check.

#### Scenario: An unsubmitted dependency contains an implementation type error

- **WHEN** a correctly typed submitted caller imports that dependency
- **THEN** the caller's check MUST pass without reporting the dependency's internal error
- **AND** incorrect caller arguments MUST still fail
- **AND** explicitly checking the dependency MUST report its internal error

#### Scenario: A submitted Python file assigns an incompatible value

- **WHEN** a Python file assigns a string to an explicitly integer-typed variable
- **THEN** both targeted type checkers MUST report an error and exit non-zero

### Requirement: Text hygiene without content disclosure

The pre-commit text checker MUST reject invalid UTF-8, a leading UTF-8 BOM and
Unicode replacement characters. It MUST accept legitimate multilingual UTF-8,
leave input files unchanged and report failures without printing file contents.

#### Scenario: A text file contains a decode replacement character

- **WHEN** a submitted text file contains U+FFFD
- **THEN** the check MUST fail and identify the file and line without exposing its text

#### Scenario: A file contains valid Traditional Chinese

- **WHEN** a submitted text file contains valid UTF-8 Traditional Chinese
- **THEN** the encoding check MUST accept it without rewriting the file

### Requirement: Branch-only local development

The development workflow MUST require checking the current branch before staging
or committing. Changes MUST be committed to a named non-main branch and delivered
through a PR for review. Merging remains the owner's decision. The workflow MUST
NOT claim server enforcement or automatically enabled review.

#### Scenario: An implementation task is ready to deliver

- **WHEN** scoped checks pass and the task changes are committed to a task branch
- **THEN** that branch MUST be published as a PR for review without merging it

### Requirement: Maintenance preserves operational interfaces and evidence

Repository maintenance MUST preserve scheduler entry paths, existing data formats,
checkpoints and stored evidence. A worktree snapshot MUST NOT be treated as a full
production data source. A documented read-only SQLite query MUST fail on a missing
database without creating it.

#### Scenario: The worktree has no official daily database

- **WHEN** the documented read-only progress query selects that missing database
- **THEN** it MUST fail without creating or rebuilding the database
