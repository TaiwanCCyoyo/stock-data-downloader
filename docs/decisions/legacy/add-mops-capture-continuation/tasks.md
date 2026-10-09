# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-mops-capture-continuation/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-mops-capture-continuation/tasks.md).
No production action or acquisition is authorized by this record.

## 1. Implementation

- [x] 1.1 Preserve the pre-change failure evidence (11 manifests, 34 stranded rows) before editing.
- [x] 1.2 Add the opt-in hooks and observed-shape refusal classifier to the shared engine.
- [x] 1.3 Pass the hooks through `mops_dividend_evidence.capture` as keyword-only options defaulting to off.
- [x] 1.4 Reject selector copies, duplicates, and foreign rows before any detail request; issuer refusals cover every remaining selected row, including rows beyond the detail budget.

## 2. Verification

- [x] 2.1 Offline tests: default unchanged, issuer and row-scoped refusals, unrecognised errors, HTTP 403/429/500, schema error after a refusal, selection, early stop, budget, empty list, classifier positives/negatives, and all 46 real refusal bodies.
- [x] 2.2 Run the existing MOPS suites unchanged alongside the new tests.
- [x] 2.3 Run the new offline tests for selector identity/uniqueness rejection and the issuer-refusal detail-budget boundary (parent executed the scoped command with normal host permission approval; 103 related tests passed on 2026-09-18).
- [x] 2.4 Independent implementation review (2026-09-18: selector identity/uniqueness and issuer-refusal coverage findings closed).
