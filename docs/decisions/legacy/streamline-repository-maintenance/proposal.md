# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/streamline-repository-maintenance/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/streamline-repository-maintenance/proposal.md).
No production action or acquisition is authorized by this record.

## Why

The README mixes onboarding, artifact contracts, scheduling and backup operations,
and several descriptions disagree with the current pipeline. Branch-only delivery
is required by the owner but is absent from this repository's checked-in workflow.
The apparent root clutter includes live scheduler entry points and private local
state, so reducing the file count alone would be unsafe.

## What Changes

- Keep README onboarding short; index existing evidence guides and move detailed
  pipeline, scheduling and backup usage into focused documents. Preserve old README
  top-level section anchors as navigation links and keep historical OpenSpec records intact.
- Record this repository's owner-authorized branch/PR workflow. Check the branch
  before committing and deliver through a PR for review; no additional blocking
  hook or remote protection setup is required by the owner.
- Consolidate Ruff configuration into pyproject.toml without changing its effective
  settings, and explicitly ignore local worktrees, scratch space and tool caches.
- Correct read-only progress instructions so a missing SQLite file cannot be
  created accidentally. Keep existing CLIs and all scheduled cmd/vbs paths.
- Add a read-only UTF-8/text-corruption pre-commit check adapted from the owner's
  reference configuration, without language restrictions or logging file contents.
  Align the Ruff hook with the version already resolved in uv.lock (0.15.22).
- Add owner-requested targeted Mypy and Pyright hooks with local dev dependencies
  and import-path configuration for scripts/, config/ and shp_utils/.
- Remove automated-check inventories and duplicate manual runs from agent guidance.
  Limit Mypy diagnostics to explicitly checked files while retaining imported type
  information, addressing the PR review's unmodified-import diagnostic concern.
- Adopt explicit current-head PR follow-through and resolution rules. Compare
  observed market dates across the full covered interval when reporting potential
  historical gaps; do not infer a complete trading calendar from observed rows.

- Repair the 107 existing Pyright diagnostics across production helpers and offline
  tests, as authorized by the owner. Preserve processing contracts; clarify pandas
  selections, optional values and test doubles without diagnostic baselines.

## Capabilities

Review follow-up: lock development-only Requests stubs so direct Requests imports
have explicit type information, and verify valid and invalid Requests usage without
network calls. Runtime dependency versions and artifact contracts stay unchanged.

### New Capabilities

- `repository-maintenance`: Safe development workspace and branch delivery contract.

### Modified Capabilities

None. Existing CSV, SQLite, Parquet and JSON artifact contracts are unchanged.

## Impact

Documentation, developer configuration and a small file-hygiene helper, verified
with focused offline tests. Mypy and Pyright are development-only dependencies;
runtime dependency versions and acquisition behavior remain unchanged.
No production data operations, credentials, downloads, schema changes, database
rebuilds, schedule changes, strategy research or remote repository-setting changes.
The deprecated dividend CLI remains available because it has a documented interface
and path-contract tests; its outputs and yfinance dependency are retained.
