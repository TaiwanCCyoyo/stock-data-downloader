# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-par-value-evidence/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-par-value-evidence/design.md).
No production action or acquisition is authorized by this record.

## Context

Observed TWSE TWTB8U rows contain stop/resume keys and reference prices, but not
exact exchange ratios. TWTB7UDetail supplies explicit ratios for a matching code
and stop date. TPEx pvChgRslt embeds these terms in each row's HTML detail.

## Goals / Non-Goals

**Goals:** Pure byte-to-evidence transformations with exact decimal/rational
quantities, explicit source binding, preserved missingness and no implicit timing.

**Non-Goals:** HTTP, live-cache mutation, trading events, coverage certification,
source-priority selection or a new corporate-action engine.

## Decisions

- Expose `extract_par_value_resumes(body, *, source, start, end)` for sources
  `twse/twtb8u` and `tpex/pvChgRslt`; date parameters are ISO strings.
- Expose `extract_twse_par_detail(body, *, resume_body, resume_row_index, start, end)`.
  Reparse the supplied resume response, then verify detail code and stop date.
  Empty detail stays unresolved; it does not remove its resume event.
- Return JSON-compatible dictionaries with schema version, source, raw SHA256,
  row index, market/code, stop/resume dates, original field literals, explicit
  ratio and face values, and distinct previous/reference/auction prices.
  Exact ratio uses numerator/denominator, not binary floats; historical_available_at
  is always null. Source schema/status or conflicting identities fail closed.
- Validate named fields irrespective of order, row widths, uniqueness, declared
  row counts, valid ROC dates, requested resume-date bounds, stop strictly before
  resume, and duplicate event keys. A stop date may precede the request window.
  Missing numeric source values remain null with unresolved status, never zero.
  Reject malformed/nonfinite/nonpositive quantities; compare ratio to face values
  where both are present. Do not demand exact price equality after rounding.
- Parse TPEx embedded table cells as inert text. Reject duplicate labels or
  incomplete/mismatched code and dates. No execution, DOM, network or links.
- Keep empty-source status distinct from event-free interval certification.
  A current notice-list response cannot be substituted for a resume response.

## Risks / Trade-offs

- Rounded reference price differs from an exact share ratio: keep both and the
  auction base separate; callers choose a named signal adjustment policy.
- Original historical availability is unknown: emit null, not retrieval time.
- Unsupported source shape: raise a specific evidence error rather than guess.
- Missing detail: retain the event and unresolved terms; no fabricated ratio.

## Migration Plan

Additive helper and tests only. Existing cache schemas and readers remain unchanged.

## Open Questions

Historical availability and interval coverage are source/policy questions outside
the parser. Neither a successful parse nor a later report proves historical PIT.
