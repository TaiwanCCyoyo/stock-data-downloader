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
Each SQLite artifact has one 120-second deadline across its reads, and
per-artifact elapsed times are logged. This work happens during refresh, never
during a page request.

Rows, codes and period bounds come from one `GROUP BY code` statement. Its plan
depends on each artifact's indexes:

| Artifact                                               | Plan                                                               |
| ------------------------------------------------------ | ------------------------------------------------------------------ |
| Official prices, institutional data, corporate actions | Scan the `(code, date)` covering index, without reading table rows |
| Monthly revenue                                        | Scan its `(revenue_month, code)` index with a temporary GROUP BY   |
| Symbol metadata                                        | Read the table, which is small                                     |

Codes count as before: rows with a NULL code are counted but are not a symbol.

The single mixed aggregate used before 2026-10-28 read the whole table instead:
16 GB for official prices. It exceeded the deadline in the daily runs of
2026-10-26 and 2026-10-27, publishing healthy official-price and institutional
data as `missing`.

Measured on 2026-10-28 with a warm cache, the new reads took 8.5 seconds for
official prices and 1.2 seconds for institutional data. After a reboot, one cold
`(code, date)` index pass over official prices took about 30 seconds. A complete
cold collection has not been measured.

All reads of an artifact share one SQLite snapshot and one deadline. That
deadline includes the stability retry, which never starts after it has passed.

The JSON object has `schema: "producer-data-status.v1"`, a UTC `generated_at`, and
`datasets`. Each dataset has `id`, a plain Chinese `name`, `data_period` with
`from`/`to`, `last_updated`, `symbols`, `rows`, and `status` (`ok`, `stale`, or
`missing`). Counts describe stored rows and distinct codes, not completeness.
Unknown metadata is JSON null; an empty, successfully read artifact has zero rows
and codes. Unavailable, empty, corrupt, or changing artifacts are `missing`.

A `missing` entry that could not be read also carries `unavailable_reason`:

| Reason                | Meaning                                                     |
| --------------------- | ----------------------------------------------------------- |
| `absent`              | The file does not exist.                                    |
| `read_deadline`       | The read exceeded the 120-second deadline; the file exists. |
| `changed_during_read` | The file moved during two consecutive reads.                |
| `unreadable`          | Any other read or validation failure.                       |

A readable empty artifact has no reason. The field is additive, so `v1` readers
that ignore unknown keys are unaffected. An artifact whose size or modification
time (including its WAL) moves during a read is read once more before it is
reported as `changed_during_read`, because a WAL checkpoint by another reader can
move it without any data change.

| ID                  | Artifact                   | Period                     | Freshness tolerance                        |
| ------------------- | -------------------------- | -------------------------- | ------------------------------------------ |
| `official_daily`    | `official_daily.sqlite`    | trading dates              | 7 days                                     |
| `price_daily`       | `price_daily.parquet`      | trading dates              | 7 days                                     |
| `institutional`     | `institutional.sqlite`     | report dates               | 7 days                                     |
| `monthly_revenue`   | `monthly_revenue.sqlite`   | revenue months (`YYYY-MM`) | 45 days after month end                    |
| `corporate_actions` | `corporate_actions.sqlite` | ex-dates                   | 7 days since observation                   |
| `symbol_meta`       | `symbol_meta.sqlite`       | local observation dates    | 14 days since observation (weekly refresh) |

For dated data (official prices, institutional reports, monthly revenue),
`last_updated` is the latest fetch timestamp among the rows of the newest stored
period: when the newest data was last observed. A historical backfill of old
periods therefore does not make the dataset look recently updated. When no index
leads with the period column (official prices), a `DISTINCT code` index scan
finds each code and an index seek reads its newest-period row. For
observation-only data (corporate actions, symbol metadata), `last_updated` is
the maximum stored fetch timestamp. Parquet uses its file modification time. Both the observation and the period must be recent for daily
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
