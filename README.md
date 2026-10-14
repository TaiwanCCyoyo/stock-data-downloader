[繁體中文版](docs/zh-TW/README.md)

# stock-data-downloader

The approved outward project and future repository name is `stock-data-downloader`.
This is a local rebuild candidate pending publication review. Before the owner
renames its folder and confirms the formal production location, do not install
or resume market-data update schedules. Keep the legacy folders in place.

A stock-data download pipeline, currently supporting Taiwan (TWSE/TPEx) through Shioaji and official
exchange sources. It produces minute/daily prices, corporate actions, security
metadata, monthly revenue, institutional flows and dated value-chain evidence as
local CSV, SQLite, Parquet and JSON artifacts.

## ⚙️ How to Initialize

Python **3.13+** and [uv](https://astral.sh/) are required. From the checkout root:

```powershell
uv sync
uv run pre-commit install
```

For production acquisition, copy `.env.example` to `.env` only if no `.env`
already exists, then configure credentials privately. Never commit or print them.
`data/stock_category.json5` supplies the download category mapping.

## 🚀 Quick Start

Run the daily update from the primary production checkout:

```powershell
uv run scripts/run_daily.py
```

The flow is download → daily conversion → corporate actions → official daily
prices → parquet rebuild → monthly revenue → institutional reports → value-chain
classification → integrity check → backup. The official fetch must precede the
parquet rebuild.

Development worktrees contain partial, independent data snapshots. Use them for
code changes and offline tests; do not refresh or rebuild production artifacts
from missing worktree inputs.

## 📂 Project Structure

| Location                        | Responsibility                                                         |
| ------------------------------- | ---------------------------------------------------------------------- |
| `scripts/`                      | Pipeline CLIs; invoke as `uv run scripts/<name>.py`                    |
| `shp_utils/`                    | Shared source parsing, evidence and logging helpers                    |
| `config/`                       | Repository-anchored data paths and defaults                            |
| `tests/`                        | Offline tests and small fixtures                                       |
| `docs/`                         | [Operating guides, source evidence and document index](docs/README.md) |
| `docs/contracts/`               | Current behavior contracts                                             |
| `docs/decisions/legacy/`        | Retained historical records; implementation status unverified          |
| `data/`                         | Acquired artifacts and evidence (or `SHIOAJI_DATA_DIR`); preserve      |
| `logs/`, `.tmp/`, `.worktrees/` | Local logs, handoffs/evidence and physical worktree snapshots          |
| `cron/`                         | Windows scheduler launchers and installer                              |
| Root configuration              | Python/dependencies/Ruff in `pyproject.toml`, formatting and Git hooks |

Private credentials, certificates, ignored data and old logs remain local.
Scheduler launchers now live in `cron/`; the old root paths are removed. Existing
installations must migrate their task actions after merging and pulling; see
[Windows scheduling](docs/operations.md#windows-scheduling). Directory cleanup
does not authorize deleting data or archives.

## Development and PRs

Follow [AGENTS.md](AGENTS.md) and the [Git workflow](docs/en/git-workflow.md):
work on a named non-main branch, verify relevant changes, commit only those
changes. Publication of this local candidate requires owner approval.
The owner decides whether to merge after publication.

```powershell
uv run pytest tests/test_data_paths.py tests/test_run_daily.py
```

Choose additional tests for the actual change. Ruff settings live in
`pyproject.toml`; normal commit hooks own formatting, secret checks and UTF-8/text
integrity checks. The text checker accepts legitimate Chinese and never rewrites
files or prints their contents. The Ruff hook matches the version in `uv.lock`.
Python commits also run targeted Mypy and Pyright checks using the project's uv
environment. Their settings and scripts import paths live in `pyproject.toml`;
passing a targeted check does not certify all existing Python modules.
Mypy retains imported type information but reports diagnostics only for explicitly
checked files. Committing runs these checks automatically; no separate manual
pass is needed unless diagnosing a failure or changing the checks themselves.

## Behavior contracts

[The documentation index](docs/README.md#behavior-contracts) links current contracts.
Record substantive changes in ordinary design notes; retained legacy proposals
do not establish implementation status. See [rebuild provenance](docs/migration/README.md).

## 🛠 Automation Scheduling (Windows)

[Scheduling and logs](docs/operations.md#windows-scheduling): daily update at
18:00 Monday–Friday; official history at 18:25 daily. After merging and pulling into
the production checkout, run `.\cron\setup_scheduled_tasks.ps1 -UpdateActionsOnly`
to migrate existing task actions to `cron/`. Verify both action paths before
re-enabling paused tasks; the action-only update preserves their disabled state.

## ⚡ Consolidated Parquet Cache

[Artifact sources and rebuild rules](docs/pipeline.md#consolidated-parquet-cache):
official rows take precedence, and Shioaji daily CSVs fill gaps.

## 🛡️ Data Safety & Backup

[Data protection and backup limitations](docs/operations.md). Preserve data,
checkpoints, raw observations and archives. The additive file mirror is not a
guaranteed consistent backup of a live SQLite database.

## 📝 License

[Apache License 2.0](LICENSE).
