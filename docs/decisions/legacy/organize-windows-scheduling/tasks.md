# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/organize-windows-scheduling/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/organize-windows-scheduling/tasks.md).
No production action or acquisition is authorized by this record.

## Implementation

- [x] Inspect task actions and state; export definitions and disable both triggers.
- [x] Relocate launchers and installer to cron/ and update caller documentation.
- [x] Verify working-directory resolution, arguments and exit codes offline.
      Delivery and current-head review are tracked on the scheduling PR. Ten offline launcher and
      action-migration cases passed, including failure exits and a missing second task.

## Review follow-up

- [x] Correct both stale English README claims that root launchers were preserved.
- [x] Cross-check the production action migration command against the installer
      and operations guide; no scheduler or data operation is needed for this fix.

## Deferred by owner

After the owner merges and pulls, update local task actions from the production
checkout, verify preserved settings, then re-enable the two tasks. Do not perform
these deployment steps from the development worktree.
