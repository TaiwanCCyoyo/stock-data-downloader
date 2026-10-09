# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-24-add-value-chain-classification-snapshots/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-24-add-value-chain-classification-snapshots/tasks.md).
No production action or acquisition is authorized by this record.

## 1. Fetcher and artifact contract

- [x] 1.1 Add a standard-library HTML parser that discovers value chains and extracts staged
      nodes plus domestic security membership from representative local fixtures.
- [x] 1.2 Add the bounded-retry TPEx fetch flow, structural validation, deterministic inverse
      symbol index, and versioned JSON serialization.
- [x] 1.3 Add freshness gating, append-only timestamped history writes, atomic latest writes,
      and the standalone CLI including `--force` and configurable data paths.

## 2. Orchestration and documentation

- [x] 2.1 Integrate the non-price-cache weekly refresh step into `run_daily.py`, preserving
      later steps and backup behavior when classification refresh fails.
- [x] 2.2 Document the CLI, JSON schema, weekly cadence, source provenance, and the rule that a
      current snapshot must not be used as pre-snapshot historical classification.
- [x] 2.3 Include latest and historical classification JSON in quarterly metadata archives,
      preserving history paths and existing price-archive behavior.

## 3. Verification

- [x] 3.1 Add focused parser, validation, history, freshness, atomicity, and daily orchestration
      regression tests.
- [x] 3.2 Run `uv run pytest` and `uv run ruff check .`, then run pre-commit against all changed
      files and inspect any formatter edits.
- [x] 3.3 Force one live fetch into an isolated data directory and manually inspect schema
      version, provenance, latest/history byte identity, and representative memory IC, passive
      components, PCB, glass-fiber cloth, server/AI, and satellite-related coverage.
