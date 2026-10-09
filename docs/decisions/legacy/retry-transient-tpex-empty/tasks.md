# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/retry-transient-tpex-empty/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/retry-transient-tpex-empty/tasks.md).
No production action or acquisition is authorized by this record.

## 1. TPEx recovery

- [x] 1.1 Keep newly attempted empty TPEx days partial until nonempty history arrives.
- [x] 1.2 Include valid unfinished whole-market checkpoint dates in automatic TPEx planning, support both checkpoint column names and preserve settled/gaps-only behavior.

## 2. Behavioral verification and handoff

- [x] 2.1 Reproduce the old empty-then-skip defect with offline regression tests, then verify repeated empty/successful recovery and checkpoint-only recovery after a long interruption.
- [x] 2.2 Verify legacy settled checkpoints, invalid/other-market units, existing TWSE behavior and bounded execution with uv run python -m pytest; use normal commit hooks for scoped Ruff and other configured checks.
- [x] 2.3 Document retry semantics and legacy-data boundary in the pipeline guide and review receipt, validate this OpenSpec change and prepare the dedicated PR handoff.
