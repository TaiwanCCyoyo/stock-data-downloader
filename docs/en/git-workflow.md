# Git Workflow Contract

Shared by Codex and Claude Code. This repository owns its delivery workflow
independently of any project reading its data artifacts.

## Task isolation

- Never commit or push directly to `main`. Every implementation reaches it through
  a pull request for review; merging remains the owner's decision.
- Before staging or committing, run `git branch --show-current`. A blank result
  means detached HEAD. Use a named non-main branch before writing a commit.
- Prefer a dedicated task branch from refreshed `origin/main`; report if refreshing
  is unavailable. An owner-selected long-lived workspace branch is also valid.
- Use the reserved `<primary-checkout>/.worktrees/codex-workspace` or
  `<primary-checkout>/.worktrees/claude-workspace` when available. Check ownership,
  branch, uncommitted changes and active writers before reuse; keep one primary
  writer per worktree. Preserve unrelated changes and shared Git metadata.
- Keep `.tmp/` handoffs and archived workspaces. Worktree data is a partial physical
  snapshot, not a mirror of production. Follow [data safety](../operations.md)
  before any worktree or data operation.

## Delivery authorization

- This clean rebuild candidate is local-only. Do not create a remote repository,
  publish files, push branches or open a PR until the owner approves its contents.
- Verify and commit task-owned changes on a named non-main branch. The initial
  clean root uses the owner's repository-local GitHub noreply identity.
- Preserve unrelated work, production data, credentials and scheduler settings.
  Do not change global settings or weaken checks to complete delivery.
- Once publication is separately approved, report the actual remote, branch,
  CI and review evidence; merging remains the owner's decision.

## Review and integration

- Report the PR URL, commit and actual checks. A local commit or a passing local
  test is not evidence of a remote review or successful CI run.
- Review must assess the current PR head. New commits require review of the new
  head; findings should identify actionable defects, compatibility breaks, source
  definition changes, data-loss risks or credential exposure, not freeze prose.
- A review request authorizes analysis and a report. Posting review comments or a
  formal GitHub approval needs explicit authorization; it does not grant merging.
- The owner decides whether and when to merge after review. Agents must not merge,
  push main, change repository settings or bypass checks. Force pushes, history
  rewrites and deletion of branches/worktrees require explicit owner authorization.
- This is a cooperation contract. It adds no push-blocking hook, server ruleset,
  scheduled reviewer or automatic review integration. Do not inherit another
  repository's protection, reviewer role or authorization by copying its template.

## PR follow-through

For an authorized implementation PR, the main agent owns the following without
waiting for another reminder. Review-only/status-only requests remain read-only
unless implementation is also authorized.

1. After opening or updating the PR, wait for configured review and required checks
   on its current head. Check at bounded intervals and report meaningful changes.
   If review does not start or an external blocker prevents progress, report the
   exact pending state; do not claim completion.
2. Assess findings against the current diff, fix valid issues within scope, verify
   changed behavior and push the fix. New commits require renewed review and
   current-head checks. Bring disputed findings, scope expansion and authorization
   decisions to the owner.
3. After a finding is addressed, relevant verification passes and the fix is pushed,
   resolve its discussion before waiting for renewed review. The owner uses
   resolution to initiate automatic re-review here; do not leave fixed threads open
   while waiting for that review. Re-read the thread list to confirm resolution,
   then monitor current-head review. An outdated thread or passing check alone does
   not prove a fix. Avoid duplicate manual review requests while review is running.
4. Report completion only when current-head required checks pass, review is complete
   and no threads remain unresolved; otherwise name the remaining blocker or owner
   decision. Include the PR URL and verified head. The owner still decides merging.

This follow-through authorizes resolving addressed threads on this implementation
PR. It does not authorize formal approval, resolving disputed/unfixed findings,
merging or changing repository settings.
