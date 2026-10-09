# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-07-add-official-daily-price-source/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-07-add-official-daily-price-source/proposal.md).
No production action or acquisition is authorized by this record.

## Why

Local daily bars are currently derived entirely from Shioaji 1-minute kbars. Reconciling
1,953 symbols against the exchanges' own published figures for 2026-07-22 showed two things:

- **Close is reliable** — 99.9% of symbols matched the official close within 0.01.
- **Volume is systematically low** — the Shioaji-kbar-summed daily volume is below the
  official volume for 85% of symbols and above it for 0%, with a median ratio of 0.985
  (a ~1.5% undercount, consistent with intraday minute bars excluding the closing call
  auction). A small tail (~10 symbols) is off by more than 30%.

Shioaji is also a single point of failure for daily data: it needs a broker account and
credentials, enforces a 500 MB/day usage quota (symbols get skipped when it is hit), and can
only be queried in ≤30-day windows. There is no independent source to cross-check values or
to fall back on.

Taiwan's two exchanges publish their own official daily figures through free, no-auth OpenAPI
endpoints (TWSE and TPEx), catalogued on the government open-data platform under the FSC
Securities and Futures Bureau. This change adopts the official daily data as a second,
authoritative source: a backup and cross-check now, and the intended long-term source of
record for daily bars. Minute-bar acquisition stays with Shioaji.

**Target architecture**: the intended end state is official-data-as-primary — the official
OpenAPI endpoints become the authoritative daily source (price now; other officially
published daily series, e.g. institutional 三大法人 buy/sell, are a natural later extension
of the same fetch foundation), with Shioaji reduced to a minute-bar-only auxiliary source
feeding intraday needs. This change reaches that end state gradually rather than in one
step: official data is introduced alongside Shioaji now, official history is backfilled
over time via the rate-limited crawler (see Non-Goals), and the daily/Shioaji cross-check
this change adds to the data-quality health check (see the `data-quality-report` spec delta
below) is what lets the system detect gaps on either side while both sources coexist.

## What Changes

- Add an official daily-price fetcher (new module) that pulls one trading day of whole-market
  daily quotes from the two official bulk endpoints and writes a per-symbol daily artifact in
  a stable, documented schema:
    - TWSE listed: `https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL`
    - TPEx OTC: `https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes`
- Wire the official daily fetch into `run_daily.py` as an additional acquisition step, so each
  daily run produces both Shioaji-derived bars and official bars for the latest trading day.
- Add a standalone, rate-limited, resumable **historical backfill crawler** that walks past
  dates per symbol via the official TWSE single-symbol history endpoint
  (`https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY`; TPEx has no equivalent endpoint,
  see `design.md` decision #6), throttling requests and checkpointing progress so it can be
  stopped and resumed without re-fetching, and backs off when the endpoint signals overload.
  A default, no-argument invocation crawls every TWSE code the official artifact already knows
  about, newest month first, down to the endpoint's earliest supported date (2010-01-04),
  bounded to a default per-run time budget, resumable across runs and safe to interrupt or to
  run alongside the daily refresh.
- Keep the official data **separate from** the Shioaji-derived data (distinct files/columns);
  this change stores and exposes the official values but does not yet replace the Shioaji
  volume in `price_daily.parquet`. Choosing which source feeds the consolidated cache is a
  deliberate follow-up (it would introduce a seam in the historical volume series).
- Extend the local data-quality health check to cross-check the official daily artifact
  against the Shioaji-derived `{code}_day.csv` for the same symbol/date, flagging
  close/volume discrepancies beyond a documented threshold and symbol/date pairs present in
  one source but missing in the other, recorded in `data/data_quality_report.json`. This is
  a reporting check only — it does not judge which source is "correct" or gate the run.

## Capabilities

### New Capabilities

- `official-price-source`: the official daily fetcher, the historical backfill crawler, their
  rate-limit / resume behavior, and the official daily artifact contract (file layout,
  columns, units, date and no-trade conventions).

### Modified Capabilities

- `daily-update-orchestration`: the one-click refresh gains an official daily-fetch step
  alongside the existing Shioaji-based steps.
- `data-quality-report`: the health check gains an official-vs-Shioaji daily cross-check
  alongside the existing corporate-action price-gap cross-check.

## Non-Goals

- **No replacement of the Shioaji volume in `price_daily.parquet` yet.** The consolidated
  daily cache keeps its current Shioaji-derived source in this change; switching the
  authoritative volume (and handling the resulting historical seam) is a separate decision.
- **No change to minute-bar acquisition.** Intraday/minute data remains sourced from Shioaji;
  the official endpoints provide daily granularity only.
- **No new credentials or secrets.** The official endpoints require no API key; `.env` and
  Shioaji credentials are untouched.
- **No corporate-action or price-adjustment math changes.** This adds a raw daily source; it
  does not alter adjustment logic.
- Does not define how any downstream consumer reads the official artifact — the pipeline stays
  decoupled from consumers per `openspec/config.yaml`.

## Impact

- **Archive dependency**: this change MODIFIES `daily-update-orchestration` requirements and
  the `data-quality-report` capability, both introduced by `add-data-quality-report`, whose
  delta is implemented in code (`run_daily.py` already has 5 steps, `check_data_integrity.py`
  exists) but not yet archived into the baseline specs (the baseline `daily-update-orchestration`
  still shows 4 steps, and no baseline `data-quality-report` spec exists yet at all).
  `add-data-quality-report` MUST be archived before or together with this change, so the
  baseline this change's deltas apply against already reflects the five-step pipeline and the
  `data-quality-report` capability this change extends.
- New module(s): an official daily fetcher and a historical backfill crawler.
- New artifact(s): official daily price files under `data/` (layout defined in the spec),
  kept distinct from the existing `{code}_day.csv` / `price_daily.parquet`.
- `run_daily.py`: one additional acquisition step; failure isolated and logged like the others.
- External dependency: the change adds calls to public TWSE/TPEx endpoints. These are official
  (exchange-published, catalogued on data.gov.tw under the FSC Securities and Futures Bureau,
  Government Open Data License v1). No published hard rate limit exists; the bulk endpoints
  need only one call per market per day, while the single-symbol history endpoint has an
  observed soft limit (~a few requests per 5 seconds) that the crawler must respect.
