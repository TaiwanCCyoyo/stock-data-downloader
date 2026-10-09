# Additive official-data repair method

This public candidate retains the method, not private run receipts or market rows.
The original 2026-10-04/05 report remains in the private legacy source commit;
its source hash is recorded in the migration manifest.

- Coordinate writers and preserve scheduler definitions before approved maintenance.
- Use SQLite online backups and integrity checks, not file copies of a live database.
- Insert absent market/code/date keys while preserving existing values, raw evidence
  and original observation times. Verify original rows across every column and NULL.
- Compare historical dates to an independent trading calendar, including historical
  Saturday sessions. Date presence does not prove per-security coverage.
- Preserve checkpoint/error/partial distinctions. Retain raw maintenance responses;
  an HTTP 200 HTML failure is not a valid empty trading-day response.
- Historical revenue backfills retain unknown publication times as NULL. A fetched
  historical value does not establish when a trader could have known it.
- Compare source vintages without overwriting either. Evaluate reused codes and
  venue transfers before joining security history by code alone.
- Data acquisition, code integration, parquet rebuild and scheduler resumption are
  separate authorized operations. A completed repair authorizes none of the others.

See [daily source contracts](contracts/official-price-source/spec.md),
[operations](operations.md), and [pipeline](pipeline.md).
