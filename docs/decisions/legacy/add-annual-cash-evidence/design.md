# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-annual-cash-evidence/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-annual-cash-evidence/design.md).
No production action or acquisition is authorized by this record.

## Context

Observed TWSE responses use top-level fields/data, `權/息` values 權/息/權息 and
`權值+息值` (including six-decimal strings). TPEx responses use tables[0], distinct
除權/除息/除權息 labels, and an explicit `現金股利` field. Existing index output is not changed.

## Goals / Non-Goals

Goals: source-bound offline cash evidence with original precision and missingness.
Non-goals: downloads, issuer revision choice, net payment calculation, timing
certification, market eligibility, canonical cache writes or account execution.

## Decisions

- Accept bytes plus one of two explicit source identifiers; compute raw SHA256.
  Named fields, not field count, bind the source schema. Reject malformed/duplicate
  fields, duplicate code/date keys, unexpected event vocabulary and invalid dates.
- TWSE cash-only rows project the reported combined value with an explicit
  `cash_only_total` basis. Rights-only rows are `cash_not_indicated`; mixed rows
  are `requires_detail`. Neither becomes zero. Do not subtract displayed prices.
- TPEx projects its explicit cash column for all event types. Preserve literal
  text and validated decimal text; missing/invalid numbers remain null with status.
- Both sources leave historical availability and payment timing null. The helper
  emits no current financial-report fields, inferred trade times or net-cash claims.
- Empty responses produce `unobserved_empty`, never a certified event-free interval.

## Risks / Trade-offs

- Reported precision need not equal issuer precision: preserve the literal and basis;
  compare independently held announcements separately, never silently round agreement.
- Partial schemas can look plausible: validate required names and each row width;
  no fallback between TWSE/TPEx vocabulary or current data routes.
- A source record is not a PIT certificate: unknown times remain explicit.

## Migration Plan

Additive pure API only. Existing readers and scheduled jobs remain unchanged; removing
the new helper affects no existing on-disk contract.

## Open Questions

Issuer revision and individual cash credit policies are downstream decisions, not
resolved by this evidence projection.
