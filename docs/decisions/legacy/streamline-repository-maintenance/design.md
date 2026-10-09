# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/streamline-repository-maintenance/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/streamline-repository-maintenance/design.md).
No production action or acquisition is authorized by this record.

## Decisions

- Use the owner's clean `.worktrees/codex-workspace` and a task branch starting at
  refreshed origin/main. The primary checkout remains the production writer.
- Keep root cmd/vbs launchers byte-identical: actual Windows task actions reference
  the root vbs files, which in turn invoke the root cmd files. No pause is required
  to edit an isolated code checkout.
- Keep the existing pre-commit runner as the verification owner. The owner clarified
  that PR review is the goal and extra push-blocking machinery is unnecessary.
  GitHub's ruleset API returned HTTP 403 with a plan restriction on 2026-09-21;
  no protection, hosted reviewer, CI status or merge authority is inferred.
- Preserve all existing evidence guides and active/historical OpenSpec changes.
  Document the actual backup implementation: file-size verification of an additive
  mirror is not a consistent live SQLite snapshot. No backup or restore is executed.

## Verification

The pre-commit follow-up adopts UTF-8 and decode-corruption checking from the
owner's reference configuration. It additionally rejects a UTF-8 BOM, reports
only paths/line numbers, and never rewrites files. Legitimate Chinese is accepted.
At the owner's explicit follow-up, add both targeted Mypy and Pyright hooks and
their standalone development dependencies. Match pytest's scripts import layout;
use Mypy's explicit package bases and untyped-body checking and Pyright's standard
mode. Mypy ignores missing third-party stubs, as the reference does, without
disabling checks on project code. Serialize Mypy hook batches that share its cache.
This is a changed-file gate, not a claim that the whole repository is type-clean.
After review, Mypy uses `follow_imports = "silent"`: it still analyzes imports
for type information but suppresses diagnostics inside unsubmitted dependencies.
A behavioral regression covers imported implementation errors, explicit dependency
checks and incorrect caller arguments. Agent guidance leaves mechanical checks
to the installed hooks, without requesting an extra manual pass.
The Ruff hook follows the already-locked version instead of copying an older
reference pin. Existing secret-scan exclusions remain narrowly limited to lockfiles.

Use temporary files for existing path/orchestration tests. Check README links,
Ruff effective settings, staged paths,
normal commit hooks and byte identity of scheduler entries against the starting
commit. No test needs production data or a network acquisition call.
