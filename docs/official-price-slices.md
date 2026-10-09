# Explicit official price slices

`scripts/export_official_price_slice.py` exports an explicit selection from
`official_daily_price` without requiring Shioaji CSVs or current symbol metadata.
It is offline and additive. It does not change the daily update or rebuild
`data/price_daily.parquet`.

## Usage

Run from this repository with its locked uv environment. Supply an existing
source database and a destination directory that does **not** exist:

```powershell
uv run scripts/export_official_price_slice.py --db-path "D:/snapshots/official.sqlite" --market TWSE --codes 2475 3519 3579 --start-date 2019-01-01 --end-date 2019-01-31 --output-dir "D:/snapshots/january-export" --max-rows 100000
```

Dates are inclusive, canonical ISO dates. Codes are explicit strings and retain
leading zeroes; duplicate requested codes are de-duplicated and sorted. Only the
specified market is selected, so the same code in another market does not get
merged into this export. A missing database, invalid selection or exceeded row
budget fails rather than falling back to another source or truncating rows.

The SQLite read uses a read-only transaction. Existing destination directories
are refused, including empty ones. A failed write can leave a new partial output
directory without a final manifest; inspect it and choose a fresh destination.
Do not delete an existing artifact simply to make a rerun pass.

## Artifact contract

`prices.parquet` preserves the following columns in order:

| Columns                     | Arrow type / meaning                                        |
| --------------------------- | ----------------------------------------------------------- |
| market, code, date, name    | string; date is ISO, name may be null                       |
| open, high, low, close      | nullable float64; unadjusted stored prices                  |
| volume                      | nullable int64; **shares**, not lots                        |
| value                       | nullable float64; turnover in TWD                           |
| transactions                | nullable int64; stored transaction count                    |
| close_missing               | nullable int64 schema; selected source flags must be 0 or 1 |
| raw_fields_json, fetched_at | source strings preserved verbatim                           |

Null closes, zero shares and missing numeric fields are retained. No rows are
invented for absent dates. There are no moving averages, adjusted prices, fills,
stock-type decisions or inferred suspension labels. The schema remains the same
for an empty export. This is **not** the merged Parquet schema: do not interpret
its volume as the lots used in the consolidated cache.

`manifest.json` records the selection, observed/missing codes, row count, units,
source path and hashes of selected source rows and the emitted Parquet. Its
`coverage_certified` flag is false even when every requested code has some rows.
Having one row is not complete historical coverage or point-in-time eligibility.

The selected-row digest uses SHA-256 of UTF-8 compact JSON with keys `columns`
then `rows`; each row is an array in the column order above, sorted by code/date.
Use `ensure_ascii=False`, `separators=(',', ':')`, `allow_nan=False`. The hash is
of the selected SQLite values, not the entire database or original HTTP response.
Keep the acquisition manifest, original payload hashes and membership-selection
evidence with this export. The manifest is not a preregistration or signed seal.

## Source boundaries

Fetched time records acquisition, not when a historical fact became public.
Stored rows can have been restated, and two overlapping official feeds may use
different volume definitions. The exporter preserves those values; it does not
authenticate or reconcile them. An explicit code list does not certify that the
instruments were ordinary stocks, listed on the selected board, or tradable on
every date. Those claims need separate dated evidence.

For codes missing from the source database, use the existing explicitly bounded
history acquisition workflow with verified code/month inputs and source evidence.
Do not expect an export to recover rows that have not been acquired. Do not
expect automatic backfill's existing-code seed to discover previously unknown
historical instruments.

## Verification

The focused test module passes 11 cases, covering selection, empty/partial
output, nullable closes, integer volume validation, source duplicate/flag
rejection, row budget, non-overwrite, hashes and the real CLI. File-scoped
pre-commit checks, including Ruff, pass; scoped implementation review found no
concrete issue. OpenSpec strict validation passes for the additive change.

An offline January 2019 replay of three captured official monthly responses
exported 63 rows with a 63-row budget. Independent comparison matched all 14
fields in all rows, verified raw/output hashes and original fetch timestamps,
and confirmed the scratch source database was unchanged. This is a bounded
export validation, not certification of historical universe completeness or
auction execution. Existing source caches were not modified.
