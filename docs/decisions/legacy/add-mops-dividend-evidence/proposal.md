# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-mops-dividend-evidence/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-mops-dividend-evidence/proposal.md).
No production action or acquisition is authorized by this record.

## Why

Existing dividend artifacts do not preserve announced payment timing or the source
revision chain. Bounded live MOPS samples establish an obtainable official source,
but displayed announcement dates differ from detail lookup keys and must not be
silently interpreted as historical availability timestamps.

## What Changes

- Add a bounded, opt-in company/year evidence collector for MOPS `t108sb19` and
  response-linked `t108sb19_detail`, with raw JSON bodies and a versioned manifest.
- Preserve displayed dates, revision text, exact detail parameters, fetch timing,
  HTTP/application outcomes and body hashes without rewriting prior evidence.
- Validate source-linked routes/parameters and report partial, empty and failed
  captures distinctly. Never turn an API failure into an empty-history success.
- Non-goals: bank-credit modeling, normalized dividend ledger, full-history coverage,
  broker credentials, purchases, daily orchestration or scheduled task changes.

## Capabilities

### New Capabilities

- `mops-dividend-evidence`: Bounded immutable raw announcement capture with explicit
  provenance and completeness limited to the selected response and request budget.

### Modified Capabilities

None. Existing corporate-action, CSV, SQLite and Parquet contracts are unchanged.

## Impact

New standalone CLI/helper, tests and usage documentation. A new run directory under
`config.DATA_DIR / raw/mops/dividend_announcements` by default contains only raw
responses and `manifest.json`; an explicit fresh output directory is supported.
Existing requests/truststore dependencies suffice. No existing data is rewritten.
