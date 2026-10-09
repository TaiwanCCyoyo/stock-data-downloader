# MOPS historical material-disclosure evidence

`scripts/fetch_mops_material_evidence.py` captures raw historical material disclosures
for an explicit company, ROC year and month. It is an opt-in evidence tool, not a
dividend normalizer, actual payment feed, scheduled job or whole-history crawler.

```powershell
uv run --no-sync python scripts/fetch_mops_material_evidence.py --company-id 6465 --roc-year 115 --roc-month 7 --max-details 1 --output-dir D:/MarketEvidence/mops-material-6465-new-capture
```

The output directory must not already exist. Without an explicit directory, output
goes into a new UUID directory under
`config.DATA_DIR / raw/mops/material_announcements`. Observe scheduled-writer and
backup rules before writing beneath the data area. Existing cache files, database
rows and evidence captures are never updated by this command.

## Source and bounds

The official frontend page is `https://mops.twse.com.tw/mops/#/web/t05st01`.
Its observed list POST uses the fixed `/mops/api/t05st01` route with `companyId`,
`year`, `month`, `firstDay` and `lastDay`. Detail POST uses only
`/mops/api/t05st01_detail` and the list's validated `serialNumber`, `enterDate`,
`marketKind`, `companyId`. The currently observed material-detail market value is
`otc`; others fail explicitly pending source verification. Dividend-source TWSE
examples do not certify this material-disclosure source's TWSE contract.

One list plus at most 1–50 details (default 10) is requested, in returned row order,
without retries or keyword filtering. Capture stops on the first failure. A detail
budget is not a relevance filter: unrelated material disclosures also consume it.
Redirects and arbitrary remote URLs are not followed. Each complete body is capped
at 2 MiB, with 20-second connect and read-idle timeouts; these are not a total
wall-clock deadline. No credentials, paid access, daily integration or scheduling
are introduced.

## Artifacts and status

`manifest.json` has schema `mops-material-evidence.v1`. Exact response-body bytes
are saved as `response-NNN.json` before schema interpretation; the extension does
not guarantee valid JSON. The manifest includes scope, request URL/parameters,
HTTP/application status, current server timestamp when parseable, capture times,
raw file SHA-256 hashes and per-request outcomes. Every listed announcement retains
its raw row, displayed release date/time, subject, detail lookup parameters and
processing status. A validated detail also supplies its complete `explanation_raw`.

- `complete` / exit 0: all announcements in this returned list were captured.
- `source_empty` / exit 0: validated empty response, not proof of empty history.
- `partial` / exit 1: budget exhausted or a failure after some captured details.
- `error` / exit 1: source failed before any successfully captured detail.
- Invalid scope, existing output or filesystem error: exit 2. A raw write failure
  raises without publishing a final manifest. Interrupted manifest writes may leave
  invalid JSON; parse and verify hashes/status, not just filename existence.

`unprocessed_rows` includes both failed and never-attempted rows. Historical
coverage, historical public availability and revision completeness always remain
`unverified`. Python transport injection is labelled `injected_transport` and its
timestamps describe replay, not the original download. CLI transport is `live`.

## Publication, lookup and payment are different facts

Fields are mapped by unique title names rather than field count. Detail serial,
displayed release date/time and subject must match the list. Only trailing subject
whitespace is ignored for matching; source text itself remains unchanged.
`enterDate` is an opaque lookup date, not a certified publication timestamp.

In the observed 6465/115/7 list, nine announcements were returned. The first detail
(serial string `2`, key `1150709`) states an original payment date of `115/07/13`
and a changed date of `115/07/14`, but explicitly limits postponement to regions
affected by typhoon work suspension; unaffected regions keep the original date.
Both dates and the entire exception must remain available. Neither date proves
an individual investor's actual bank credit. This tool deliberately creates no
normalized payment date, entitlement, tax amount or cash event.

## Verification scope

The captured official pair used to establish the source contract has SHA-256:

- List: `096a62b2c13e36653777fa816d409fa9ee784ed4327e10f4bcf519c1f43ab321`
- Detail: `327821fbf44bd7a3f4a2e921b5aeb1e0d08c1ba92c9e6252b4cf5849f7e18315`

It was obtained on 2026-09-09 via two live requests. Replaying those bytes tests
the parser/capture path, not a fresh live HTTP request or full population coverage.
A subsequent live CLI capture of the same company/month, with `--max-details 1`,
made two successful HTTP/application-200 requests. Its manifest correctly says
`partial`, with 9 listed, 1 captured and 8 unprocessed; exit 1 is expected for this
budget, not a download failure. The full conditional explanation was inspected.
Current server timestamps differ from the first probe, so new hashes are expected:

- Live list: `c15ead160eb4c2b005cc74021238edf589a6f804d7ce448db62f9e3ab4ff4da5`
- Live detail: `1d86db0fe59380920dda4bcadc78dd911498396125ab69617dbb8679a30ef747`

Both saved-body hashes were independently recalculated. The earlier recorded pair
also passed offline byte-equality/hash checks, 9/1/8 accounting and exact explanation
preservation. This does not establish full history, revision coverage or personal
cash receipt. No formal data directory or schedule was changed.

Focused verification on 2026-09-09: 97 tests passed across
`test_mops_material_evidence.py`, `test_mops_material_cli.py`, the existing dividend
capture/CLI tests and `test_data_paths.py`. Tests cover field-name reordering,
identity/type failures, distinct lookup dates, no-follow links, bounded partial
accounting, source-empty limits, physical default paths and raw-write failures.
Ruff and changed-file pre-commit passed; the OpenSpec change validates strictly.
This is affected-component verification, not the complete pipeline suite.

Scoped security review found no concrete issue. Implementation review found one
low-severity invalid-key case: ROC year `000`. The new material validator now
rejects it before a detail request, covered by the 97-test run. Existing dividend
validation semantics were left unchanged. This correction does not alter the
verified live sample, whose key is `1150709`; no duplicate live request was needed.
