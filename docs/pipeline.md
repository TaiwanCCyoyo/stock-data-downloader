# Pipeline and Artifact Guide

[Documentation index](README.md) · [Operations and data safety](operations.md)

Run commands from the primary production checkout, after configuring its environment.
A development worktree's partial snapshot is not a source for a production refresh.

## Daily update

```powershell
uv run scripts/run_daily.py
```

The order is download → minute-to-daily conversion → corporate actions for both
markets → official daily prices → parquet rebuild → monthly revenue → institutional
reports → value-chain classification → integrity check → backup. The official fetch
must precede the parquet rebuild; backup runs last. See the
[orchestration contract](contracts/daily-update-orchestration/spec.md).

The official step also attempts TWSE's dated MI_INDEX when TWSE bulk has no
usable dated rows or trails a successful TPEx bulk date. The TPEx date is the
request-date witness; without it no dated supplement is inferred. Failed bulk
writes are rolled back before another market commits. Available supplement rows
are inserted before the parquet rebuild;
an empty or failed supplement is logged without undoing successful bulk writes.
Publication at a particular clock time is not assumed.

## Official daily history and gap repair

```powershell
uv run scripts/backfill_official_history.py --max-seconds 3600 --max-consecutive-failures 3
uv run scripts/backfill_official_history.py --twse-dates 2026-10-02 --max-requests 1
```

Automatic runs prioritize TWSE whole-market days from MI_INDEX, then TPEx days,
then TWSE symbol-months from STOCK_DAY. Candidates include observed dates from
either market and weekdays in the recent 45-day window, so consecutive missed
runs can recover a day absent from both markets. Unfinished TWSE day checkpoints
remain retry candidates after that window, including dates absent from both
markets. Automatic TPEx planning is
recomputed after TWSE day repair in the same invocation. Explicit `--twse-dates`
or `--tpex-dates` alone selects day-only work for the requested market; it does
not enqueue automatic work for the other market or the symbol-month backlog.

`--max-consecutive-failures` is optional and accepts a positive integer. The
failed unit's error checkpoint is committed before the run stops; a successful
fetch resets the streak, and a cached skip does not. Omit the option to retain
the existing retry behavior. `--max-requests` counts successful fetched units;
combine a time budget and failure limit when an endpoint is unavailable.

MI_INDEX inserts only absent `(market, code, date)` rows into
`data/official_daily.sqlite`, preserving existing prices, raw fields and fetch
timestamps. Bulk and STOCK_DAY retain their existing update precedence. TWSE
day checkpoints use `code = ''` and ISO dates in `period`; symbol-month
checkpoints keep nonempty codes and `YYYY-MM`. Recent empty responses and empty
responses contradicting existing TWSE rows remain retryable. Older empty dates
without TWSE rows settle empty. History starts at 2010-01-04.

New TPEx history responses containing no rows remain `partial`, including dates
with no existing TPEx prices and dates retried after long interruptions. An empty
answer can mean the provider has not published yet; it does not establish a
permanent absence. Automatic TPEx planning includes unfinished whole-market
`partial`/`error` checkpoint dates even when neither market has stored prices
for them, as well as existing gaps and unsettled bulk-written dates. It supports
the current `period` and legacy `year_month` checkpoint columns. Malformed,
before-floor and future checkpoint dates do not create work.

Existing `ok`/`empty` TPEx whole-market checkpoints remain settled; this code
change does not relabel history or repair legacy empties automatically. A
separately audited missing date can be selected explicitly with `--tpex-dates`
and retried with `--force` under a bounded, authorized data repair. New empty
attempts need no `--force` and remain retryable until nonempty history arrives.
Without a checkpoint table, automatic TPEx planning retains gaps-only behavior.

The symbol-month universe consists of codes with prior symbol checkpoints plus
those on the newest TWSE date, so newly discovered historical codes do not
multiply redundant STOCK_DAY requests. Historical observations retain security
names; a code alone does not establish instrument identity or a delisting.

The parquet symbol universe remains restricted to existing day CSVs. Newly
available official history can extend those instruments' bar counts and replace
fallback coverage when the parquet is next rebuilt. Validate overlapping closes
and identities before interpreting results across that change; existing stored
research results and physical snapshots remain unchanged.

## Official Monthly Revenue

The daily pipeline fetches the latest listed and OTC batches from the official
MOPS OpenAPI and stores them in `data/monthly_revenue.sqlite`. Run it or inspect
coverage independently with:

```powershell
uv run scripts/fetch_monthly_revenue.py latest
uv run scripts/fetch_monthly_revenue.py status
uv run scripts/fetch_monthly_revenue.py backfill --start-month 2020-01 --end-month 2026-07 --max-requests 24
```

The database contains `monthly_revenue_current`, append-only
`monthly_revenue_revisions`, and `monthly_revenue_raw_snapshots`. Revenue
amounts use the source unit: thousands of New Taiwan dollars. Raw JSON/CSV
evidence is stored under `data/raw/monthly_revenue/`, and repeated historical
backfills resume from successful market/month checkpoints.

Time fields are intentionally distinct. `source_report_date` is the official
table date, `first_observed_at`/`last_fetched_at` are local observations, and
`announcement_at` is reserved for an exact company filing timestamp. The
latest endpoint receives a conservative `available_at` equal to its first
local observation (`FIRST_OBSERVED`). Historical MOPS CSVs do not expose exact
company filing times, so their `announcement_at` and `available_at` remain
`NULL` with `HISTORICAL_NO_EXACT_TIME`; do not substitute the revenue month or
batch date in a point-in-time backtest.

Sources: [listed latest batch](https://openapi.twse.com.tw/v1/opendata/t187ap05_L),
[OTC latest batch](https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O), and the
official month-addressed MOPS historical CSV archive.

## Institutional Investor Reports

The daily step stores point-in-time TWSE/TPEx institutional flows in
`data/institutional.sqlite` and preserves raw responses under
`data/raw/institutional/`. Completed `(market, date)` checkpoints are skipped;
use `--force` only when a source date must be replaced deliberately. Historical
backfills are inclusive and bounded by request/time budgets:

```powershell
uv run scripts/fetch_institutional.py backfill --start-date 2024-01-01 --end-date 2024-12-31
uv run scripts/fetch_institutional.py daily
```

`published_at` remains null unless an authoritative source timestamp exists;
`fetched_at` is the first safe point-in-time availability boundary.

## Official Industry Value-Chain Classification

The weekly-gated daily step captures the complete current taxonomy from the
[Taipei Exchange Industry Value Chain Information Platform](https://ic.tpex.org.tw/).
It includes fine-grained nodes such as memory IC, passive components, PCB, and
glass-fiber cloth, plus the domestic security codes published under each node.
Run a forced standalone refresh with:

```powershell
uv run scripts/fetch_value_chain_classification.py --force
```

The stable version-1 contract is written to:

```text
data/value_chain_classification.json
data/value_chain_classification_history/YYYY-MM-DDTHHMMSSZ.json
```

The root JSON fields are `schema_version`, `source`, `fetched_at`, `snapshot_id`,
`chains`, and `symbols`. `chains` preserves the published stage/node hierarchy;
`stage` is `null` when an official non-linear diagram does not publish one.
`symbols` is the inverse security-to-node index. History snapshots are
append-only and byte-identical to the latest artifact written by the same run.

The source exposes current state, not historical classifications. Never use a
snapshot to label dates before its `fetched_at` value. Informal market concepts
such as "old AI" are not canonical labels; query the official AI/server nodes
and keep any interpretation separate.

## Corporate-action history

`run_daily.py` keeps the _current year_ of each source in both markets fresh, and closes out
the _previous year_ once (one full-year request that its raw-range cache then skips
forever, so a year is never left ending on whatever date the last run of December
happened to reach). Everything older is not its job: for a first-time setup or to
backfill older years, run `fetch_corporate_actions.py` directly. See
[`corporate-actions-index`](contracts/corporate-actions-index/spec.md) for event
classification and evidence-status details.

```powershell
uv run scripts/fetch_corporate_actions.py fetch
uv run scripts/fetch_corporate_actions.py fetch --source twtcau --start-date 20250501 --end-date 20250630
uv run scripts/fetch_corporate_actions.py fetch --source twtauu --start-date 20220901 --end-date 20220930
uv run scripts/fetch_corporate_actions.py status --code 0050
```

`fetch_dividends.py` (yfinance-based dividend fallback, `data/dividends/*.csv` /
`dividends.parquet`) is **deprecated**; `corporate_actions.sqlite` is the sole
primary source going forward.

Local outputs are ignored by Git:

```text
data/corporate_actions.sqlite
data/raw/twse/
data/raw/tpex/
```

## Adjusted daily prices

Raw daily K-line CSVs are kept unchanged. Build a separate adjusted daily cache
for consumers that need continuous chart-scale prices. See
[`adjusted-price-cache`](contracts/adjusted-price-cache/spec.md) for the
adjustment contract and output columns.

```powershell
uv run scripts/build_adjusted_prices.py --codes "0050,2330,2603"
uv run scripts/build_adjusted_prices.py --all
```

Adjusted files are written under `data/adjusted_prices/daily/`.

## Consolidated parquet cache

`data/price_daily.parquet` is derived from `official_daily.sqlite`, with
`{code}_day.csv` filling symbol/date gaps. Each row has a `Source` indicator;
`Volume` is in lots, whereas the official database stores shares. Moving averages
are computed over the merged per-symbol series. See the
[price pipeline contract](contracts/price-data-pipeline/spec.md).

A newer CSV alone does not determine which feed owns a row. Preserve both source
artifacts and their source definitions. With a complete, approved source set, the
standalone rebuild is:

```powershell
uv run scripts/build_price_parquet.py
```

Do not run this against an incomplete worktree to replace the production cache.

The optional `--technical-features` build flag also publishes a separate, versioned
technical-component generation under `data/technical_features/`. The daily default
does not accumulate these historical generations. Existing price columns remain unchanged. See the
[technical-feature contract](technical-features.md) for definitions, pinned-manifest
reading, local-only generation, reuse limits and preservation requirements.

## Other entry points

| Need                                      | Entry point or guide                                                                                             |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Official bulk daily prices                | [fetch_official_daily_price.py](../scripts/fetch_official_daily_price.py)                                        |
| Bounded official history                  | [backfill_official_history.py](../scripts/backfill_official_history.py), [source notes](tpex-history-sources.md) |
| Security metadata                         | [fetch_symbol_meta.py](../scripts/fetch_symbol_meta.py)                                                          |
| Local integrity report                    | [check_data_integrity.py](../scripts/check_data_integrity.py)                                                    |
| Explicit evidence capture or price export | [Evidence guides](README.md#evidence-and-source-guides)                                                          |

Use `uv run scripts/<name>.py --help` to inspect options on argparse-based CLIs.
The deprecated dividend downloader remains at `scripts/fetch_dividends.py`, with
its yfinance dependency and existing outputs preserved. It is not part of the
daily update.
