# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-29-add-tpex-corporate-actions/tasks.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-29-add-tpex-corporate-actions/tasks.md).
No production action or acquisition is authorized by this record.

# Tasks

Source evidence for every endpoint fact below: `docs/tpex-history-sources.md`.

## 1. Design

- [x] 1.1 Market becomes a field on `SourceConfig` (plus `base_url` and `payload_shape`);
      the module-level `MARKET`/`BASE_URL` constants go away. A parallel dimension was
      rejected -- every per-market fact is already per-source. Source keys stay flat and
      do not collide, so `run_daily.py`'s loop picks up TPEx unchanged; what it does need
      is per-source failure isolation, which it has none of today. See `design.md` §1.1.
- [x] 1.2 `ALTER TABLE twse_twt49u_raw_ranges RENAME TO corporate_action_raw_ranges`,
      guarded inside `init_db`. Rename over copy-and-drop because it moves rows atomically
      and destroys nothing; a second table was rejected because the cache lookup would have
      to consult both forever. No key change -- `source` already carries a market prefix.
      See `design.md` §1.2.
- [x] 1.3 `data/raw/<market-lowercase>/<source>/` -- which is exactly what TWSE already
      writes, so no file moves and no migration. `DEFAULT_RAW_DIR` moves up to `data/raw`
      and the market segment comes from the source config. See `design.md` §1.3.
- [x] 1.4 Full field map in `design.md` §1.4, from the live endpoint (2013-01-01..2026-08-29,
      281 rows). The observed `減資原因` values are exactly two: `彌補虧損` (196) ->
      `LOSS_OFFSET_CAPITAL_REDUCTION`, and `現金減資` (85) -> `CASH_CAPITAL_REDUCTION`.
      **TWSE spells the latter `退還股款`**, so the existing classifier must gain the TPEx
      word or 30% of rows degrade to the generic `CAPITAL_REDUCTION` (design.md F1).
      Probing also turned up four further plan corrections -- F2 (`權/息` is `除權`/`除息`/
      `除權息`, so the existing map would send 100% of exDailyQ rows to `UNKNOWN`), F3
      (`parse_twse_date` raises `SystemExit` on TPEx's compact ROC dates), F5 (the reduction
      factor is NOT always > 1; observed min 0.922) and F6 (TPEx nests `fields`/`data` under
      `tables[0]`).

## 2. Implementation

- [x] 2.1 Add the four TPEx sources to the source registry with their endpoints, date-range
      parameter names (`startDate`/`endDate`, `YYYY/MM/DD`), and data floors (2008-01-02,
      2013-01-01, 2021-06-01, 2021-06-01).
- [x] 2.2 Add a normalizer for `bulletin/exDailyQ` producing the existing row shape, reusing
      the reference-price ratio factor method on `除權息前收盤價` / `除權息參考價`.
- [x] 2.3 Add a normalizer for `bulletin/revivt`, classifying by `減資原因` per 1.4 and
      ignoring the embedded-HTML `詳細資料` column.
- [x] 2.4 Add normalizers for `bulletin/etfSplitRslt` and `bulletin/etfRvsRslt` against their
      9-field header, and mark them registered-but-unobserved in coverage inspection.
- [x] 2.5 Never select a field mapping from an empty response.
- [x] 2.6 Include both markets in `run_daily.py`'s corporate-action step, on the existing
      current-year-plus-previous-year policy, with one market's failure not aborting the other.

## 3. Tests

- [x] 3.1 `exDailyQ` fixture normalizes to the expected rows, including a computed
      `price_factor` matching the reference-price ratio.
- [x] 3.2 `revivt` fixture classifies a loss-offset reduction and a cash reduction correctly,
      and its factor is `> 1`.
- [x] 3.3 An empty `exDailyQ` response (which carries the older field list) does not change
      any mapping and is recorded as fetched-and-empty.
- [x] 3.4 TWSE and TPEx rows for the same code and ex-date coexist without key collision.
- [x] 3.5 A TPEx source failing does not prevent the TWSE sources from refreshing in the
      daily run, and vice versa.

## 4. Validation

- [x] 4.1 `uv run python -m pytest tests/ -q`
- [x] 4.2 `uv run pre-commit run --files <changed files>`
- [x] 4.3 Fetch one real TPEx year and inspect by hand: spot-check three OTC symbols'
      ex-dividend dates and reference prices against the raw response, and confirm the stored
      `price_factor` reproduces the published reference price from the previous close.
- [x] 4.4 Re-run `check_data_integrity.py` and compare `corporate_action_mismatches` for OTC
      symbols before and after. Count extreme gaps (|return| >= 40%) rather than the raw
      mismatch total, which is dominated by ordinary limit-up/down days.
- [x] 4.5 Record in the change's completion notes that price adjustment for OTC symbols now
      happens where it previously did not, so analyses spanning the change are not comparable.

## Implementation notes

**2.x** — `SourceConfig` gained `market`, `base_url`, `payload_shape`,
`date_param_format` and `implied_event_type`; `MARKET`/`BASE_URL` are gone.
`fetch_twse_range` is now `fetch_source_range`, and `extract_fields_and_data` hides the
flat-vs-`tables[0]` envelope split. Three new normalizers, all reading by field name.
Beyond the plan as written, four corrections from `design.md` were needed:
`classify_capital_reduction_event` gained `現金減資` (F1), `classify_twt49u_event` and
`estimate_cash_dividend` gained `除權`/`除息`/`除權息` (F2), `parse_twse_date` gained a
compact-ROC branch and now returns `None` instead of reaching `normalize_yyyymmdd`'s
`SystemExit` (F3), and `build_fetch_ranges` clamps to the source's own floor instead of
a hardcoded 2003 — otherwise a no-argument run asks the 2021-floor ETF bulletins for 2003.

**4.1** — 267 passed (was 254; +11 corporate-action, +2 run_daily).

**4.3** — Fetched 2025 live: 1,250 `exdailyq` rows, 10 `revivt`. Spot-checked one row per
event vocabulary (`00950B` 除息, `4109` 除權, `6762` 除權息) against the stored raw JSON:
dates, previous close and reference price all agree, and `previous_close * price_factor`
reproduces the published reference price **for 1,250 of 1,250 rows** within 0.005.
**Zero rows classified UNKNOWN** — every one of them would have been, without F2.

Backfilled 2018–2024 as well, so TPEx now matches the depth TWSE was backfilled to and
the whole price-cache window is covered. `etfsplit` and `etfrvs` were fetched across their whole
2021-06-01 window and returned zero rows every time, which is recorded in the checkpoint
table so they read as registered-but-unobserved rather than never-configured.

**4.4** — Extreme gaps (`|return| >= 40%`), which is the metric that actually moves when a
corporate action starts being recorded; the raw mismatch total is dominated by ordinary
limit-up/down days no corporate-action row would ever explain.

|                                   | before | after  |        |
| --------------------------------- | ------ | ------ | ------ |
| OTC extreme gaps                  | 115    | **39** | −76    |
| OTC symbols carrying one          | 95     | **36** | −59    |
| all OTC mismatch events           | 4,862  | 4,487  | −375   |
| **listed extreme gaps (control)** | **24** | **24** | **±0** |

The listed control is the part that makes this attributable. Listed symbols already had
97% coverage, so if the TPEx sources were the cause, listed must not move — and it did
not, by a single event. Coverage of the backtest universe went from 1 of 891 OTC symbols
to **850 of 891 (95.4%)**, against listed's 97.3%.

The 39 that remain are expected: a price gap is not proof of a corporate action, and this
check flags any large move without a matching row. Trading halts, resumptions and genuine
one-day moves beyond 40% all live in that residue.

**4.5** — **Analyses spanning this change are not comparable for OTC symbols.** Price
adjustment for them now happens where it previously, silently, did not: an ex-dividend
drop that a strategy used to read as a real decline is now adjusted away. Any stored
backtest result touching OTC names was produced against unadjusted prices. Listed symbols
are unaffected — their coverage moved 1,023 -> 1,024. Check `generated_at` on a stored
summary before comparing it with a fresh run.
