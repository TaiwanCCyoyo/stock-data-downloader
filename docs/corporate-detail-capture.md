# Explicit corporate detail capture

`scripts/capture_corporate_details.py` is an opt-in bounded TWSE evidence capture. It does not alter data stores or scheduled jobs.

Plan JSON uses `"schema": "corporate-detail-plan-v1"` with nonempty `events`; each event has exactly `source` (`twt49u` or `twtauu`), `raw_path`, `raw_sha256`, and zero-based `row_index`. The annual raw row, hash, code, effective date, and one-comma detail key are validated before output or requests.

```powershell
uv run python scripts/capture_corporate_details.py --plan PLAN.json --output CAPTURE_DIR --max-requests 5 --max-seconds 120
```

`--timeout` defaults to 30 seconds and `--interval` to one second; both must be positive
and finite. Only fixed TWSE endpoints are used, with redirects and retries disabled.
Output is exclusive and contains copied `plan.json`, raw reply bytes, JSONL request/event
receipts, and a manifest with source/code/output hashes and applied budgets. Request-start
and reply-retrieval timestamps are distinct; a transport failure has no retrieval time.
Failure, empty or malformed reply, redirect, or budget expiry stops later events and leaves
an incomplete receipt, with nonzero CLI exit status. The overall budget bounds request
scheduling and each socket timeout; it is not a separate operating-system process deadline.

Values are literal evidence; historical/public availability and delivery timestamps are
null and no entitlement or PIT claim is made. A detail reply does not independently echo
the event date: retain its request key and annual-row provenance. Current company names
can differ from historical annual-row names; code matching does not certify name history.
