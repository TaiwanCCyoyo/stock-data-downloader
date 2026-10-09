# Shioaji Stock Prices Agent Contract

Shared by Codex and Claude Code. This is a standalone Taiwan (TWSE/TPEx)
market-data acquisition and processing pipeline with its own Git remote and uv
environment.

## Working with the owner

- Converse in Traditional Chinese. Keep code, comments, commit messages,
  configuration and docs outside `docs/zh-TW/` in English.
- Follow [Git workflow](docs/en/git-workflow.md): never commit or push main.
  Verify and commit task-owned changes on a named non-main branch. This rebuilt
  candidate is local-only until the owner approves publication; the owner merges.
- Use the reserved agent worktree when available. Before reuse, check its branch,
  uncommitted changes and other writers. Preserve unrelated work.
- Improve project guidance or tooling only when it reduces concrete maintenance
  work. This does not authorize global settings, remote rules, new reviewer roles,
  other projects or extra blocking hooks.
- Keep automated-check inventories and duplicate check instructions out of skills,
  rules and agent prompts. Focus local verification on changed behavior.
- On permission failures, report the exact command and affected paths. Do not
  relocate caches, change environment variables or ACLs, or bypass checks.

## Candidate scheduling gate

- Until the owner has renamed the candidate folder and confirmed its formal
  production location, do not create, replace or resume market-data update tasks.
- Existing legacy task disabling belongs to the separate owner-assigned task.
  This candidate must not repeat that operation or move legacy directories.

## Data boundaries

- Keep the pipeline independent of downstream applications. Describe artifacts
  by their own CSV/SQLite/Parquet/JSON contracts, without importing or naming a
  consuming project.
- Read [operations and data safety](docs/operations.md) before worktree, scheduling,
  backup or data operations. Worktree data is a partial physical snapshot, never
  a symlink/junction or an automatically synchronized production source.
- Preserve data, raw responses, checkpoints, manifests, timestamps, failures,
  local credentials/settings, `.tmp/` evidence, archived workspaces and backups.
  Cleanup does not authorize deleting, moving or overwriting production data,
  schema changes, database rebuilds or resetting progress; obtain separate
  approval with concrete impact before these actions.
- Do not launch downloads or research to validate code cleanup. Check actual
  scheduled writers before approved data operations; never stop another session's
  process. File-by-file copying of live SQLite is not a consistency guarantee.

## Implementation

- Current behavior contracts live in `docs/contracts/`. Record substantive
  behavior changes in ordinary design notes before implementation. Historical
  records under `docs/decisions/legacy/` have unverified implementation status.
- Run Python through uv: `uv sync`, then `uv run scripts/<name>.py`.
  Entry points live in `scripts/`, shared code in `shp_utils/`, defaults in
  `config/`, tests in `tests/`. See [pipeline commands](docs/pipeline.md).
- Every default data path resolves through `config.config.DATA_DIR`, anchored
  to `config.config.REPO_ROOT`. Never use CWD-relative data defaults.
- Generic acquisition steps belong in `scripts/run_daily.py`. The official daily
  fetch must precede the parquet rebuild; see the [orchestration contract](docs/contracts/daily-update-orchestration/spec.md).
- Keep scheduled CMD/VBS launchers and their installer in `cron/`.
  Task-action migration requires matching files in the production checkout. Logs go to
  `logs/<script>_<YYYYMMDD>.log`; see [operations](docs/operations.md).
- Historical re-fetch work comes from checkpoints, not only price gaps. Empty
  responses must not settle a re-fetch of a day that already has rows.
- Read observed nonempty responses before mapping fields. TWSE/TPEx labels and
  volume definitions differ; never infer a schema from field count or empty data.

## Verification and review

- Test changed behavior with relevant cases. Do not add tests that merely freeze
  wording or perform a separate manual pass of automated commit checks.
- Confirm data preservation, scheduler entry compatibility and stored-result
  readability when affected. Report what was tested and any remaining limitation.
- Review actionable behavior defects and data-loss risks. Local code inspection
  is optional unless requested; the PR supplies the additional review step.
