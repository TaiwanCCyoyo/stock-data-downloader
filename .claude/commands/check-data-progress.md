---
description: Check how far data/official_daily.sqlite has been updated — latest date per market, gaps in daily coverage, and backfill checkpoint progress. Use when asked how far the database is updated, what date the data goes up to, or to check backfill progress.
---

# Check Data Progress

Follow [Read-only Data Progress](../../docs/data-progress.md), the shared procedure
for all agents. Resolve the intended primary checkout before querying production;
a worktree's missing or older database is not a production coverage finding.

Use the documented read-only queries and calendar verification. Report per-market
dates, candidate versus confirmed gaps and unsettled checkpoints in Traditional
Chinese. Do not initialize a
schema, download, migrate, rebuild or modify files as part of this check.
