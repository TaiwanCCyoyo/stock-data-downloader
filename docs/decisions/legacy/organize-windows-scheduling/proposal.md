# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/organize-windows-scheduling/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/organize-windows-scheduling/proposal.md).
No production action or acquisition is authorized by this record.

## Why

The owner authorized relocating root scheduling files into a recognizable cron/
directory. Production task triggers are disabled now; action migration and
re-enabling must wait until the owner merges and pulls the PR.

## What Changes

- Move the two CMD/VBS launcher pairs and installer into cron/.
- Resolve the repository root from the launcher directory, preserving Python
  commands, arguments, hidden execution and failure exit codes.
- Add an action-only installer mode for the post-pull migration, preserving
  existing triggers, principals, settings and disabled state.
- Update current guidance and verify the launcher chain with offline substitutes.

## Impact

No data artifacts, schemas, credentials, download progress or runtime acquisition
behavior change. No downloads, backtests or production action migration during
this implementation. Existing task XML is retained in ignored local evidence.
