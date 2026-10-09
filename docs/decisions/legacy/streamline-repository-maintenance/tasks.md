# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/streamline-repository-maintenance/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/streamline-repository-maintenance/tasks.md).
No production action or acquisition is authorized by this record.

## 1. Inventory and proposal

- [x] Read owner contracts, archive handoff and current OpenSpec baseline.
- [x] Confirm clean worktree, refreshed base, physical data snapshot and scheduled paths.
- [x] Audit references before choosing retained CLIs and scheduler entry points.
- [x] Record the proposal before implementation.

## 2. Implementation

- [x] Simplify README and index operating/evidence documents in both language entry points.
- [x] Add repository-owned Git workflow for branch/PR delivery and owner-controlled merging.
- [x] Consolidate Ruff settings and clarify ignored local state without removing files.
- [x] Repair read-only progress instructions and stale orchestration/backup descriptions.

## 3. Verification and delivery

- [x] Run focused offline data-path and orchestration tests plus Ruff checks.
- [x] Check local documentation links, unchanged scheduler entries and protected paths.
- [x] Verify existing hook installation and run normal hooks; commit only this task on its branch.
- [x] Push the task branch and create a PR; leave merging to the owner.

Validation on 2026-09-21: 29 focused tests passed (one third-party Shioaji/Pydantic
deprecation warning); Ruff check and format check passed. Parsed Ruff settings are
identical before/after consolidation. Five scheduling files match the starting
commit. Local documentation links resolved, and four scratch-only SQLite query
cases covered current/legacy checkpoints, absent checkpoints and an absent
database without mutating input files. Strict OpenSpec validation passed.

Delivered on `codex/repository-cleanup-20260921` in
[PR #2](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/pull/2).
Normal commit hooks passed. Production data and registered tasks were untouched;
review and merging remain with the owner.

## 4. Pre-commit follow-up

- [x] Compare the owner's reference hooks with this standalone environment.
- [x] Add a read-only UTF-8/corruption check with multilingual and nondisclosure tests.
- [x] Align the Ruff hook with uv.lock and verify affected-file hooks and existing text.
- [x] Add owner-requested Mypy/Pyright dependencies and targeted hooks; verify positive and negative type-check cases.
- [x] Prepare the verified follow-up for the existing PR #2; merging remains with the owner.

Follow-up validation: all tracked text passed the UTF-8 hook, including existing
Chinese content; 17 hygiene/data-path tests passed. Mypy and Pyright both accepted
valid cross-module types and rejected incompatible assignments and imported
function arguments through the actual pre-commit hooks. Targeted hooks passed on
the new helper and tests. Existing locked dependency versions were unchanged;
only development tools and their dependencies were added.

## 5. Review and guidance follow-up

- [x] Read Codex review on 7c543c6 and starter-kit PR #9 at 77c9326.
- [x] Remove duplicated automated checks from agent guidance and delivery instructions.
- [x] Verify that imported type errors do not block an unchanged caller, while argument errors and explicitly submitted dependency errors still fail.
- [x] Prepare the review follow-up for normal commit and PR #2 delivery.

The review's specific 39-error count did not reproduce in the locked Windows
environment, including a nonincremental run. An isolated regression reproduced
the imported-diagnostic behavior and passed all four cases after scoping it:
normal imports report the dependency error, targeted callers pass, explicitly
submitted dependencies fail, and incorrect imported arguments fail. The change
retains both type checkers. No manual duplicate hook pass was run for this
follow-up; the normal commit is the automatic-check boundary.

## 6. Current-head follow-through

- [x] Read starter-kit PR #10 / 4e10cf7 and adopt implementation PR follow-through.
- [x] Verify full-interval gap queries on temporary databases, including gaps older than ten sessions.
- [x] Address the current Pyright baseline finding within owner-approved scope.
- [x] Push verified fixes and resolve both addressed discussions before re-review, following the owner's clarified order.
- [x] Read the completed review of 64e17f3 and re-read thread state; handle the new Requests-stubs finding below.

The owner authorized fixing the existing diagnostics rather than suppressing them.
Pyright now reports zero errors and warnings across the configured repository scope
(previously 107 errors in 19 files). Relevant offline regressions passed: 291 existing
tests and five full-interval/read-only documentation query cases. The installed
Shioaji dependency emits one existing Pydantic deprecation warning. No production
data, scheduler registrations or scheduler entry paths were changed.

## 7. Requests typing follow-up

- [x] Check the review's direct-import command in the locked environment.
- [x] Add development-only types-requests; preserve every existing locked version.
- [x] Verify valid Requests calls and rejected return-type mismatches without making requests.

The reported missing-stubs failure did not reproduce before the change. Explicit
stubs nevertheless add Requests type information: the direct official_api.py Mypy
command passes, configured Pyright reports zero errors/warnings, and both scope
regressions pass. Delivery and automatic re-review status are tracked on PR #2.
