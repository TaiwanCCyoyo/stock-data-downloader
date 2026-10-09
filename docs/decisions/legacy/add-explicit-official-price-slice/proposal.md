# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-explicit-official-price-slice/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-explicit-official-price-slice/proposal.md).
No production action or acquisition is authorized by this record.

## Why

The consolidated cache intentionally imports official rows only for symbols with
Shioaji CSVs. An explicit export is needed for recovered historical instruments
without changing that cache's established contract or requiring current metadata.

## What Changes

- Add an offline CLI exporting one explicit market, code list and inclusive date
  interval from a read-only official SQLite database to a newly created directory.
- Preserve market identity, nullable prices, share units, raw fields and fetch time;
  record the selection, observed-row digest and output digest in a JSON manifest.
- Report requested codes absent from the selected interval, without inventing rows.
- Leave the daily builder, automatic backfill, source databases and existing output
  schemas unchanged. No network, credentials, scheduling or trading changes.
- Non-goals: classify stock types, certify PIT membership or coverage, fill missing
  sessions, infer executable auction liquidity, or build another download system.

## Capabilities

### New Capabilities

- `explicit-official-price-slice`: Additive source-preserving official row export.

### Modified Capabilities

None.

## Impact

One new standalone script and focused tests, documentation and an additive
`prices.parquet` plus `manifest.json` artifact contract. Existing pandas/pyarrow
and SQLite suffice; no new dependency or downstream application import.
