# Local data status

`shp_utils.data_status.write_data_status()` publishes `data-status.json` in the
configured data directory. The daily runner calls it after the health check and
before backup, including runs with failed refresh steps. A standalone
`uv run scripts/write_data_status.py` reads existing artifacts and writes only this
summary; it neither downloads nor repairs data.

No usable daily CSV input is an ordinary data-validation failure from the Parquet
library entry point. The runner records it as a failed cache refresh, continues
to publish the status summary, returns nonzero, and skips backup. A readable old
Parquet remains untouched and is marked stale; an absent cache is marked missing.
Process termination signals, including `KeyboardInterrupt` and explicit
`SystemExit`, still propagate rather than being treated as refresh failures.

The 2026-10-24 read-only audit measured 28,029,043 official-price rows over 51,414
distinct source codes; that full aggregate took 54.658 seconds on local disk.
Each SQLite aggregate has a 120-second deadline, and per-artifact elapsed times
are logged. This work happens during refresh, never during a page request.

The JSON object has `schema: "producer-data-status.v1"`, a UTC `generated_at`, and
`datasets`. Each dataset has `id`, a plain Chinese `name`, `data_period` with
`from`/`to`, `last_updated`, `symbols`, `rows`, and `status` (`ok`, `stale`, or
`missing`). Counts describe stored rows and distinct codes, not completeness.
Unknown metadata is JSON null; an empty, successfully read artifact has zero rows
and codes. Unavailable, empty, corrupt, or changing artifacts are `missing`.

| ID                  | Artifact                   | Period                     | Freshness tolerance                        |
| ------------------- | -------------------------- | -------------------------- | ------------------------------------------ |
| `official_daily`    | `official_daily.sqlite`    | trading dates              | 7 days                                     |
| `price_daily`       | `price_daily.parquet`      | trading dates              | 7 days                                     |
| `institutional`     | `institutional.sqlite`     | report dates               | 7 days                                     |
| `monthly_revenue`   | `monthly_revenue.sqlite`   | revenue months (`YYYY-MM`) | 45 days after month end                    |
| `corporate_actions` | `corporate_actions.sqlite` | ex-dates                   | 7 days since observation                   |
| `symbol_meta`       | `symbol_meta.sqlite`       | local observation dates    | 14 days since observation (weekly refresh) |

`last_updated` is the maximum stored fetch timestamp, except Parquet uses its file
modification time. Both the observation and the period must be recent for daily
and monthly data. Freshness is a conservative calendar-day heuristic, allowing
ordinary weekends and holidays; it is not an exchange-calendar completeness
certificate. Events need not happen every day, so corporate-action freshness
uses observation time only. A failed daily refresh forces that dataset to `stale`
when existing nonempty data can still be read.

Official-price row counts carry a separate `failed_markets` set. A caught bulk or
dated-supplement failure marks the dataset stale and makes the daily run fail even
when another market succeeded. Successful zero-row responses have no failure flag;
zero alone is never used as evidence of an error. The mapping's existing count keys
and values remain compatible with callers.

SQLite opens with `mode=ro` and reads committed WAL contents. Inputs are checked
for size/modification changes (including WAL) across a read. Parquet scans only
Code/Date in bounded batches. No table is created or modified. Readers must not
open live artifacts during a scheduled write; the summary itself is a small file
for later metadata-only discovery.

Publication writes and flushes a unique temporary file beside the destination,
then uses `os.replace`. A failure preserves the previous summary and makes the
daily run fail, without suppressing the normal backup of existing safe artifacts.
