# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/organize-windows-scheduling/specs/windows-scheduling/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/organize-windows-scheduling/specs/windows-scheduling/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: Scheduling files are grouped under cron

Windows launchers and task installation SHALL live under cron/. Launchers SHALL
run the existing Python entry points from the repository root, independently of
the caller's directory, without changing arguments or hiding failure exit codes.

#### Scenario: Launch from an unrelated directory

- **WHEN** either launcher is invoked from another directory, including a path with spaces
- **THEN** the Python command runs from the checkout root with its existing arguments
- **AND** the launcher's exit code equals the child exit code

### Requirement: Existing actions can be migrated without resetting tasks

The installer SHALL offer an action-only update that preserves existing task
triggers, identity, settings and enabled state. It SHALL NOT start the tasks.

#### Scenario: Migrate paused tasks after pulling the relocation

- **WHEN** the owner runs the action-only update from the production checkout
- **THEN** the two actions point to that checkout's cron/ launchers
- **AND** disabled tasks remain disabled until separately enabled
