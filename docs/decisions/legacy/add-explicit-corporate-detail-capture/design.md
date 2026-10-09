# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-explicit-corporate-detail-capture/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-explicit-corporate-detail-capture/design.md).
No production action or acquisition is authorized by this record.

## Context

The held annual TWSE rows carry a `詳細資料` key. Observed official page JavaScript maps
TWTAUU to `/rwd/zh/reducation/TWTAVUDetail?STK_NO=...&FILE_DATE=...`, and TWT49U to
`/rwd/zh/exRight/TWT49UDetail?STK_NO=...&T1=...`. Five observed replies span 2018–2023
cash/loss reductions and subscription/combined rights. Both have one 12-cell row but
different named schemas; field count is not a parser selector. Replies omit the event
date and sometimes use a current company name. Bind each reply to its verified request
and original row; do not infer dates or historical identity from the name.

## Goals / Non-Goals

Goals: bounded explicit capture, raw provenance, literal terms, truthful partial receipts.
Non-goals: canonical economic normalization, delivery/PIT guarantees, daily automation,
MOPS access, in-place data changes, resumable scheduler or arbitrary URL fetching.

## Decisions

- Plan schema `corporate-detail-plan-v1`: `events` list. Each entry has `source` (`twt49u`
  or `twtauu`), `raw_path`, `raw_sha256`, `row_index` (zero-based). Resolve relative raw
  paths against the plan directory. Validate all raw hashes, field uniqueness, row width,
  date/code/detail-key agreement, and duplicate endpoint keys before any request/output.
  Derive code, effective date and request key from the held row, not user-supplied URLs.
- Reject malformed key values: ASCII alphanumeric security code length 4–8, one comma,
  valid YYYYMMDD key date. TWT49U key date must equal outer ex-date; TWTAUU FILE_DATE is
  not suspension or restoration date, so only require it not later than restoration.
- One explicit output directory created exclusively; never resume/overwrite it. Persist
  plan bytes, raw responses by sequential safe filename, `requests.jsonl`, `events.jsonl`,
  final `manifest.json`. Source/input/output hashes, UTC retrieval, code and effective
  date, URL/status, exact literal fields and null historical/public/delivery timestamps
  accompany records. Identity proves capture, not historical accuracy of a restatement.
- Fixed HTTPS TWSE origin and two discovered endpoint paths; no redirect, retry or
  alternate host. Enforce positive finite request/time/timeout/pacing budgets. Count
  attempted requests even on network failure. Stop after first HTTP/non-JSON/security/
  schema/identity failure, retaining bytes and an incomplete receipt; empty data is
  unresolved, not success or zero entitlement. A known blocked source is not retried.
- Use requests already installed with an injected transport and monotonic clock/sleep
  seam for tests. Pace single requests, never sleep past the remaining overall budget;
  cap each request timeout to remaining time. No dependency or credential configuration.
- Known field sets are based on actual observed names; allow reordering only, preserving
  original labels and strings. Parse no economic numbers; don't turn NA into zero. Raw
  contradictions are not resolved here. A reply code must equal the original row code.
- This opt-in explicit event utility parallels explicit evidence/slice exports. No
  run_daily change: it does not discover or advance the generic daily source.

## Risks / Trade-offs

- Historical corrections/current names → label as retrospective reconstruction; no PIT claim.
- Detail reply has no date → keep originating annual-row hash, request key and exact URL;
  never claim response independently echoes the date.
- Missing delivery terms → literal evidence only, not account-ready entitlements.
- Budget/process interruption → append request receipts promptly; incomplete directory
  remains evidence and is never silently resumed. No all-done flag without all events.
- Existing dirty modules → new files only; do not stage or commit unrelated work.

## Migration Plan

Add CLI/helper/docs/tests, run focused tests and review, manually inspect a bounded live
capture. Existing artifacts and daily pipeline remain unchanged. No schema migration.
