# MOPS source dividend statements

`shp_utils.mops_dividend_statements.project_statement` is a pure offline
bytes-to-record transformation. It extracts what one ordinary dividend announcement
states. It does not determine what an investor received, choose an amendment,
merge announcements, calculate an entitlement or update any existing artifact.

```python
from shp_utils.mops_dividend_statements import project_statement

statement = project_statement(
    detail_body,
    company_id=company_id,
    announcement=verified_list_row,
    expected_sha256=verified_request_sha256,
)
```

The caller obtains bytes, list row and expected hash from verified capture evidence.
The helper verifies size, SHA-256, ordinary fixed-route parameters and detail
company/serial/subject identity. It performs no network or filesystem IO. Matching
a caller-supplied hash proves byte consistency, not issuer authentication or PIT
availability. The caller remains responsible for original capture provenance.

## Record contract

Schema `mops-dividend-statement.v1` identifies the source company, fixed route and
lookup parameters, original displayed date/subject and list revised-payment cell.
An announcement identity derives from its canonical route/lookup identity; the
body hash identifies a particular response, not a separate distribution. Preserve
all statements until event/revision linkage is explicitly adjudicated.

`fields` contains:

| Field                  | Stated value representation | Meaning                                                                                |
| ---------------------- | --------------------------- | -------------------------------------------------------------------------------------- |
| cash_per_share         | Decimal string              | Ordinary-share cash amount stated in this detail, not personal net cash                |
| dividend_period        | ROC year plus annual/Q1–Q4  | Income distribution period explicitly labelled in the rights block                     |
| record_date            | ISO calendar date           | Stated distribution record date                                                        |
| stated_ex_date         | ISO calendar date           | Stated ex-right/dividend day, not verified trading-calendar applicability              |
| announced_payment_date | ISO calendar date           | Date on the original labelled payment line, not final amended payment or actual credit |

Each field has `value`, `status` and `raw`. A decimal stays a string to preserve
source precision. Statuses are `stated`, `blank`, `missing`, `ambiguous`,
`unsupported` and `not_applicable`; only `stated` has a non-null value. Explicit
zero is stated, whereas empty text/date templates are blank. Duplicate labels are
ambiguous even when equal. Unknown nonblank syntax is unsupported, not zero.

Complete economic blocks and the foreign-currency answer remain raw evidence.
An amount alone does not establish currency, netness, investor rounding, taxes,
postage/transfer charges or receipt. Prefer no inferred unit over a silently wrong
TWD cash balance for an unreviewed foreign-currency statement.

Every record stays `event_link_status=unlinked` and
`payment_resolution=unresolved`; historical public availability and revision
completeness remain unverified even if all five fields parse successfully.

## Observed parsing scope

Named titles select the rights and record-date blocks; column count is irrelevant.
The helper recognizes observed ordinary-share amount and labelled annual/quarterly
period lines, positive ROC calendar dates and the standard original-payment line.
It does not search unrelated prose for a convenient date or reinterpret a cash
subscription amount as a cash dividend. Nonstandard condition text on the payment
line is retained without forcing a parsed unconditional date.

The representative retained source set includes:

- 6509/113: two announcements with the same cash terms and differing cash-subscription
  text. They remain two statements; adding their dividends would double-count.
- 6465/115: a cash-dividend announcement and a separate cash-increase template.
  The latter's blank amount/date and not-applicable period do not inherit values.
- 2330/113: explicitly year 112/Q3 income with payment in year 113. Payment year
  is not a substitute for the dividend period.
- 5511/105: an older template with amount/date but no labelled dividend period.
  Missing period stays missing; do not infer it from the announcement year.

These examples establish observed statement formats, not a universal deduplication
key, complete market/year coverage or final revision chain. A separate 6465 material
disclosure references the original payment date and limits postponement to affected
regions. Linking it and resolving applicability requires separate evidence; this
projector does not apply it. No `run_daily.py`, schedule, cache or raw collector
contract is changed.

## Verification

Focused verification on 2026-09-09: 29 tests passed with
`uv run --no-sync python -m pytest tests/test_mops_dividend_statements.py -q --tb=short`.
Ruff, changed-file pre-commit and strict OpenSpec validation passed. Source collectors
were unchanged, so their previously verified tests did not need another full run.

Retained-source inspection checked six details and all 30 projected field outcomes:
26 stated, two blank, one missing and one not-applicable, each agreeing with the
manually read source. All six records remain unlinked with unresolved payment;
zero cash events were created. Original capture/list/detail hashes and projector
identity were retained in the inspection artifact. No fresh request or cache write
was needed; this is selected-sample evidence, not historical population coverage.

Implementation review found one malformed cash-placeholder boundary. Genuine
whitespace is now blank; `元` must have a delimiter/end boundary before it is a
known blank template. Malformed nonblank suffixes remain unsupported. The final
tests and six-source replay include that correction.

This helper alone cannot make an executable account or strategy pass. In particular,
`response_revision_id` identifies response bytes, not a verified economic amendment;
current server timestamps can change bytes without changing distribution terms.
