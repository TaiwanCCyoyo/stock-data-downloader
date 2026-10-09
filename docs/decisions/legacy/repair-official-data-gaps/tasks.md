# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/repair-official-data-gaps/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/repair-official-data-gaps/tasks.md).
No production action or acquisition is authorized by this record.

## 1. Observed source evidence

- [x] 1.1 Probe additional MI_INDEX eras and compare real 2010/current rows with stored quantities.
- [x] 1.2 Probe revenue archive coverage and audit independent calendar/corporate-action ranges.

## 2. Official price repair

- [x] 2.1 Implement shared dated MI_INDEX parser and insert-only persistence.
- [x] 2.2 Implement bounded TWSE day planning, settlement, universe restriction and same-run TPEx recomputation.
- [x] 2.3 Add the optional newer TWSE supplement and functional regression cases.
- [x] 2.4 Update day/month progress queries and documented artifact/comparability boundaries.
- [x] 2.5 Add an opt-in consecutive-failure limit with committed-error, reset, default-compatibility, validation and resume regression coverage.
- [x] 2.6 Preserve automatic retry candidates from unfinished TWSE day checkpoints after they age beyond the recent window, including legacy checkpoint schemas.

## 3. Verification and authorized data repair

- [x] 3.1 Run focused uv run pytest; normal commit hooks own formatting/type/hygiene checks including uv run ruff check.
- [x] 3.2 Inspect small scratch database inserted rows/checkpoints and unchanged existing provenance.
- [x] 3.3 Download missing production institutional and supported revenue units; preserve existing observed units.
- [x] 3.4 Run bounded insert-only production TWSE repair, re-audit missing dates/checkpoints, and quantify affected CSV-backed symbols without rebuilding production parquet.
- [x] 3.5 Deliver task-owned code through PR and complete current-head CI/review follow-through.

## 4. Claude review follow-up

- [x] 4.1 Recover missing TWSE bulk from the successful TPEx date, isolate failed writes, and verify failure/empty/success cases.
- [x] 4.2 Separate added-history and existing-bar effects, record owner download authorization, and preserve rollout evidence outside durable source documentation.
- [x] 4.3 Document deployment ordering and preserve scheduler definitions before the owner-authorized release; track live review/deployment status in the PR and task receipt.
