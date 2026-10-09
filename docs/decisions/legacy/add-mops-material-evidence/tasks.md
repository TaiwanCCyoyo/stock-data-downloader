# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-mops-material-evidence/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-mops-material-evidence/tasks.md).
No production action or acquisition is authorized by this record.

## 1. Implementation

- [x] 1.1 Share raw capture mechanics without changing dividend API or artifact semantics.
- [x] 1.2 Add the bounded historical material parser and CLI with fixed routes and full raw explanation.
- [x] 1.3 Add focused success, identity, budget, malformed-input, storage and CLI tests; document scope and limits.

## 2. Verification

- [x] 2.1 Run affected tests through uv run python -m pytest, uv run ruff check and changed-file pre-commit; validate OpenSpec strictly.
- [x] 2.2 Replay and manually inspect the recorded official list/detail pair, verifying hashes, unprocessed counts and preserved conditional text without creating a cash event.
- [x] 2.3 Complete scoped implementation and trust-boundary review; record evidence and leave submodule commit/gitlink pending owner agreement.
