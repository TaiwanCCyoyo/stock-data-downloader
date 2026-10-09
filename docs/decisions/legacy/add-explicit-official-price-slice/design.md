# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-explicit-official-price-slice/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-explicit-official-price-slice/design.md).
No production action or acquisition is authorized by this record.

## Context

`official_daily_price` already has market/code/date keys, nullable OHLC,
share-volume, raw fields and fetched time. The normal merged cache deliberately
restricts codes and omits no-close rows. Its behavior must remain unchanged.

## Goals / Non-Goals

**Goals:** Export a bounded, explicit official selection without current-symbol
or CSV joins; preserve source rows; never overwrite an existing destination.

**Non-Goals:** Download, backfill planning, membership classification, moving
averages, adjustment, fill simulation, schedule changes or a new sealing framework.

## Decisions

- Add `scripts/export_official_price_slice.py` with importable
  `export_official_price_slice(*, db_path, market, codes, start_date, end_date,
output_dir, max_rows=100000) -> Path` and an equivalent CLI. Require every
  source/selection/output argument; market is TWSE or TPEx, dates are canonical
  ISO and inclusive. Sort/de-duplicate nonempty string codes. Reject invalid
  range/row budget before creating any output. Do not infer a code list.
- Read a `mode=ro`, `query_only` transaction using market/code/date-indexed
  queries. Preserve all selected rows, including null closes and zero volume.
  Raise if the row budget is exceeded; never silently truncate.
- `prices.parquet` uses the existing column names in this fixed order:
  market, code, date, name, open, high, low, close, volume, value,
  transactions, close_missing, raw_fields_json, fetched_at. String columns stay
  strings; OHLC/value are nullable float64; volume/transactions/close_missing
  are nullable int64. Dates remain ISO strings. Volume is shares. No SMA/EMA.
  Use an explicit Arrow schema even for empty/all-null columns.
- Preserve market/code and raw field/fetched strings. Reject duplicate source
  keys, invalid row dates/identities and close_missing values outside 0/1;
  do not fabricate a missing date or infer an event from it.
- `manifest.json` records schema version, source path, selection, row count,
  observed/missing codes, column names, units, `coverage_certified: false`,
  selected-row SHA-256 and output-file SHA-256. Selected rows are sorted by
  code/date. Hash UTF-8 compact JSON with keys `columns` then `rows`, where rows
  are arrays in column order, `ensure_ascii=False`, `allow_nan=False`.
  The digest identifies consumed rows, not the entire DB, PIT completeness
  or original-response authenticity. It does not replace acquisition evidence.
- Validate/read/hash before making `output_dir` with `exist_ok=False`; write
  files exclusively and manifest last. Existing directories (even empty ones)
  are rejected. A failed write may leave a new partial directory with no final
  manifest; leave it for inspection, never delete or reuse an existing directory.
- All-empty or partially missing selections produce typed output and explicit
  missing codes. Successful export means the read finished, not that coverage
  is complete. Use source fixtures to verify no changed legacy behavior.

## Risks / Trade-offs

- Current data may have been restated -> expose exact selected-row identity and
  acquisition fields; never label this a historical availability certificate.
- Fetch timestamp does not identify which overlapping feed won -> retain raw
  evidence and report that the exporter cannot reconcile feed definitions.
- No-close rows affect bar counts -> publish this as a separate artifact, not
  silently replace `price_daily.parquet`.
- First implementation is in-memory -> explicit row budget bounds it; partition
  larger requested exports deliberately rather than truncate them.

## Migration Plan

No migration, default-job wiring or existing-cache rewrite. Call the new CLI
explicitly on an approved snapshot, inspect output, and retain old artifacts.

## Open Questions

None for this export. Complete historical membership, source coverage and
downstream executable-account decisions remain outside its certification.
