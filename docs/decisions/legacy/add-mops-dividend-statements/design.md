# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-mops-dividend-statements/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-mops-dividend-statements/design.md).
No production action or acquisition is authorized by this record.

## Context

Observed ordinary `t108sb19_detail` bodies contain named rights-distribution and record-date blocks. 6509/113 has two different announcements of the same stated cash distribution with different cash-subscription text. 6465/115 has a cash statement and a capital-increase statement with blank dividend amount/date; 2330/113 specifies dividend year 112, quarter 3, not payment-year income. A separate material disclosure can later conditionally amend payment.

## Goals / Non-Goals

**Goals:** deterministic statement projection, field evidence, exact decimal precision, missing/unsupported distinctions and unique announcement provenance for later linking.

**Non-Goals:** final economic event IDs, automatic deduplication, selecting latest revisions, applying material amendments, personal net cash, verified trading dates, PIT certification, network or filesystem IO in the helper.

## Decisions

- Add `project_statement(detail_body: bytes, *, company_id: str, announcement: dict, expected_sha256: str) -> dict` in `shp_utils/mops_dividend_statements.py`. Require a bounded body, exact SHA-256 equality and ordinary detail identity validation against the supplied list row; malformed structural/identity inputs raise `EvidenceError`. A trusted caller must first obtain that list row/hash from verified capture evidence. Hash equality is consistency, not issuer authentication.
- Emit `schema_version=mops-dividend-statement.v1`, company ID, source route/parameters, raw body SHA-256, stable announcement identity derived from canonical fixed route and lookup parameters, source subject/displayed date and revised-payment list cell. Different response bytes remain separate revisions of an announcement, never different payments automatically.
- Project fields `cash_per_share`, `dividend_period`, `record_date`, `stated_ex_date`, `announced_payment_date`. Each is `{value, status, raw}`. Status vocabulary: `stated`, `blank`, `missing`, `ambiguous`, `unsupported`, `not_applicable`. All non-stated values are null; explicit decimal zero stays stated. Amount is a nonnegative decimal string, not float or a personal net amount. Period value is `{roc_year, period}` with annual or Q1–Q4. Dates are ISO calendar dates converted from positive ROC years, not verified trading/payment dates.
- Map unique top-level title names, not field count. Required text blocks are `（五）權利分派內容：` and `（八）權利分派基準日：`; absent blocks yield missing fields, present non-string blocks reject. Keep complete raw blocks and named foreign-currency answer for later interpretation. Do not infer currency/netness from an amount alone.
- Parse only observed labelled lines: ordinary-share `※除息--普通股：每壹股配發現金(股利)...元`, dividend year/period line, `＊現金股利發放日：...（發放現金股利時適用。）`, first record-date line and `除權/除息交易日：...`. Duplicated matching labels are ambiguous even if values coincide. Missing labels are missing; known whitespace/date placeholders are blank; unrecognized nonblank formats are unsupported. The observed period `其他　不適用` is not_applicable, never a zero-income statement. Retain full matched raw lines; never discard conditional trailing text to force a parsed payment date.
- Always mark `payment_resolution=unresolved`, `event_link_status=unlinked`, historical public availability/revision completeness unverified. No generic regex search through other text, summing records, inherited date, completed-history claim or actual cash field. Later linking is an explicit separate responsibility; identical projected values are only matching evidence, not proof of one economic event.

## Risks / Trade-offs

For the unlabelled record-date line, multiple date candidates are ambiguous rather
than first-value wins. Whitespace-only values of a present label are blank; extra
nonstandard condition text is unsupported. Keep this distinction in field tests.

- Historical templates differ → unsupported/missing is visible; expand only after inspecting a real source example.
- Announcement keys are not distribution IDs → retain two 6509 statements; a bounded inspection documents a reviewed relation instead of automatic merging.
- A payment field can be superseded/conditional → it stays a statement with unresolved payment semantics, even with a parsed date.
- Supplied list context may be forged → caller provenance remains explicit; the helper validates identity/bytes, not the whole capture envelope or historical authenticity.

## Migration Plan

Pure additive helper and tests. No existing artifacts or collectors change. Inspect five retained official statements (6509 twice, 6465 twice, 2330 once), optionally the existing older 5511 template; compare hand-read source fields and report each supported/unsupported result. Do not fabricate a generic deduplication or payment policy from this sample. Local submodule commit/gitlink remain pending owner decision.

## Open Questions

Global revision linkage, foreign-currency distributions, more historical templates, final amended payment applicability and investor cash credit require separate evidence/decisions.
