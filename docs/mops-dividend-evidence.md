# MOPS dividend announcement evidence

`scripts/fetch_mops_dividend_evidence.py` is an opt-in raw-evidence collector, not
a normalized dividend/payment feed. It is not installed in `run_daily.py` or any
schedule. It requires one company and ROC year:

```powershell
uv run --no-sync python scripts/fetch_mops_dividend_evidence.py --company-id 5511 --roc-year 105 --max-details 2 --output-dir D:/MarketEvidence/mops-5511-new-capture
```

Use a fresh output directory. With no `--output-dir`, a new UUID directory is
created below `config.DATA_DIR / raw/mops/dividend_announcements`. Follow the
repository's scheduled-writer/backup rules before writing to the data area; do
not run concurrently with scheduled writers. Tests and source probes can use
an explicit external output directory. An existing target is refused without
network access. No previous CSV, database, checkpoint or evidence run is modified.

## Scope and source

The official MOPS page is `https://mops.twse.com.tw/mops/#/web/t108sb19`.
Its public front end uses JSON POST to `https://mops.twse.com.tw/mops/api/t108sb19`.
The collector follows only that response's ordinary-announcement detail parameters
to the fixed `t108sb19_detail` route. It does not guess API keys from display dates,
follow remote URLs, log in, purchase access or place trades. Redirects are rejected.

One list request and at most 1..50 detail requests (default 10) are allowed, with
no retry and stopping at the first error. Each body is capped at 2 MiB; connect
and read-idle timeouts are 20 seconds each. These are I/O limits, not a strict
whole-process wall-clock deadline. Unsupported security kinds or field signatures
fail visibly rather than silently becoming ordinary-stock data.

## Artifacts

- `response-NNN.json`: exact complete HTTP response-body bytes (including HTTP or
  application errors), saved before JSON/schema validation. A body may not be valid
  JSON despite its extension. Oversized/interrupted bodies are not saved as complete.
- `manifest.json`, schema `mops-dividend-evidence.v1`: request method/URL/JSON,
  final URL/status, body filenames/SHA-256, application code/server timestamp when
  parseable, UTC capture times, per-request outcome, list rows and detail links.
  `total_listed`, `captured_details`, and `unprocessed_rows` are counts; the latter
  includes failed and never-attempted rows. Per-row status distinguishes them.
- The injectable Python transport exists for offline replay/testing. Such manifests
  say `capture_mode=injected_transport`; replay times are not original fetch times.
  Live CLI captures say `capture_mode=live`.

Run status/exit code:

| Status                  | Meaning                                                                      | CLI exit            |
| ----------------------- | ---------------------------------------------------------------------------- | ------------------- |
| complete                | Every ordinary announcement in this returned list was captured and validated | 0                   |
| source_empty            | Valid observed list was empty; not proof of empty history                    | 0                   |
| partial                 | Budget exhausted or a later detail failed after some successful details      | 1                   |
| error                   | Invalid/failed source before any successful detail                           | 1                   |
| no valid final manifest | Interrupted or filesystem failure; never complete                            | nonzero/interrupted |

Invalid arguments or existing/unwritable output return CLI exit 2. A raw-file write
failure does not publish a final manifest. Failure while writing the manifest itself
can leave an incomplete JSON file; mere filename existence is never completion.
Consumers must require and parse the final manifest, check its status, verify
referenced hashes, and enforce their own
required coverage. A source-empty success is not a settled whole-history checkpoint.

## Date semantics and remaining work

Observed source samples: TPEx 6509/113 and 5511/105, fetched on 2026-09-09.
Their selected displayed announcement dates were `113/07/18` and `105/06/28`,
but detail lookup keys were `1130717` and `1050624`. The key is not certified
publication time. `修正後現金股利發放日=無` coexists with an explicit original
payment date in both details; it does not mean no dividend or immediate cash.

The collector preserves raw text and does not select a final revision, normalize
payment events, calculate personal taxes/charges, or mark an announced payment as
actual bank credit. Server response time is current, not historical availability.
PIT, full revision history and population coverage remain unverified in every
manifest. TWSE and revised-payment branches still require measured live examples
before extending normalization/coverage claims. Existing code/data consumers are
unchanged; a future additive normalizer must keep these boundaries explicit.

## Verification — 2026-09-09

Focused verification command:

```powershell
uv run --no-sync python -m pytest tests/test_mops_dividend_evidence.py tests/test_mops_dividend_cli.py tests/test_data_paths.py -q
```

Result: 60 passed. Covers source/transport failures, no overwrite, field mapping,
date-key separation, request limits, raw-write failure, CLI status and default
physical-path checks. This is not the complete pipeline suite. Ruff and scoped
pre-commit passed; the OpenSpec change validates in strict mode.

A retained real 5511/105 list/detail pair was replayed offline with byte equality
and SHA-256 checks for both files; its manifest explicitly marks injected transport.
A separate live CLI probe of the same company/year with `--max-details 1` completed
two requests and one detail. No formal data directory or schedule was modified.
Live raw hashes (current response times differ from the earlier samples):

- List: `8f438ac1c78df903054def522d84e73a852f15af6bf1e2b6aed73a92717d1829`
- Detail: `51daa61679aefffa34a9f9db7f4fbe42cf48848c27feac9f1b60b8653f6837e4`

The successful live transport predates the final error-path-only corrections;
those corrections were verified offline, without spending another source request.
No live empty-list, changed-payment-date or TWSE coverage claim is made.
