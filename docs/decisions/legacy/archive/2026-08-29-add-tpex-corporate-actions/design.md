# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-29-add-tpex-corporate-actions/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-29-add-tpex-corporate-actions/design.md).
No production action or acquisition is authorized by this record.

# Design — add-tpex-corporate-actions

Decisions for tasks 1.1–1.4, plus what probing the live endpoints turned up. Three of
those findings contradict an assumption in `proposal.md`, and each would have shipped a
silently wrong index rather than a loud failure. Endpoint evidence:
`docs/tpex-history-sources.md`.

## What probing changed

`proposal.md` says the TPEx bulletins "map onto the existing normalizer almost
one-to-one". The _columns_ do. The _values in them_ do not, and neither does the payload
envelope. Everything below was read off the live endpoints on 2026-08-29, not assumed.

### F1 — `減資原因` uses a different word than TWSE for the same event

Every `bulletin/revivt` row from 2013-01-01 to 2026-08-29 (281 rows) carries one of
exactly two values:

| value      | rows | meaning                       |
| ---------- | ---- | ----------------------------- |
| `彌補虧損` | 196  | loss-offset capital reduction |
| `現金減資` | 85   | cash capital reduction        |

`classify_capital_reduction_event` already maps `彌補虧損`, because TWSE uses that word
too. It maps cash reductions from `退還股款` — **TWSE's spelling, which TPEx never
uses**. Reused unchanged, 85 of 281 rows (30%) would fall through to the generic
`CAPITAL_REDUCTION` instead of `CASH_CAPITAL_REDUCTION`: not an error, just a coarser
event type than the data supports.

### F2 — `權/息` uses a different vocabulary, and every row would be UNKNOWN

`classify_twt49u_event` maps exactly `息`, `權`, `權息`. `bulletin/exDailyQ` returns:

| value    | rows  |
| -------- | ----- |
| `除息`   | 2,450 |
| `除權息` | 435   |
| `除權`   | 211   |

Not one of the 3,096 observed rows matches a TWSE key, so reusing that classifier maps
**100% of TPEx ex-rights/dividend rows to `EVENT_UNKNOWN`**. This is F1's failure mode
with the volume turned up, and it is the reason task 1.4 was written to demand observed
values.

### F3 — `parse_twse_date` calls `SystemExit` on a TPEx date

TPEx returns two in-response date shapes: `115/08/19` and the compact 7-digit ROC
`1020116`. The slashed form already works (`year < 1911` gets `+1911`). The compact form
falls through to `normalize_yyyymmdd`, which requires exactly 8 digits and otherwise
raises **`SystemExit`** — not caught by `except Exception`, so it would tear down the
whole daily run on the first TPEx row rather than skipping one row.

### F4 — `exDailyQ` has two real schema eras; the empty response returns the older one

`docs/tpex-history-sources.md` records that an empty response carries a different, older
22-field list including `員工紅利轉增資`. Probing populated years shows that list is not
an empty-response artifact — it is a **real era**:

| year | fields | rows  | `員工紅利轉增資` |
| ---- | ------ | ----- | ---------------- |
| 2008 | 22     | 396   | present          |
| 2015 | 22     | 510   | present          |
| 2020 | 21     | 940   | absent           |
| 2025 | 21     | 1,250 | absent           |

So the rule is not "empty responses are weird" but the repository's existing one: **never
key off field count, read by field name.** The existing normalizers already do
(`dict(zip(fields, row))` then `raw.get("欄位名")`), so both eras parse without a mapping
table. The spec's "a source with no rows is not evidence about its schema" requirement
still holds and is what stops a future maintainer from "correcting" the mapping against
an empty 2005 response.

### F5 — a capital-reduction factor is not always `> 1`

`proposal.md` says the reduction factor "is `> 1` for a reduction". Across 281 observed
rows:

| reason     | n   | min       | median | max    |
| ---------- | --- | --------- | ------ | ------ |
| `彌補虧損` | 196 | 1.035     | 1.687  | 40.035 |
| `現金減資` | 85  | **0.922** | 1.114  | 3.750  |

Two cash reductions land below 1 — `3071` on 2013-10-07 (7.44 → 6.86, 0.922) and `6148`
on 2019-02-18 (9.98 → 9.97). A cash reduction returns capital _and_ shrinks the share
count, so the reference price can fall either side of the previous close depending on
which dominates. **No validation may assert `factor > 1`.** The useful assertion is
`factor > 0` plus a wide outlier log; 40.035 (`5364`, 2013-12-10, 1.15 → 46.04) is a real
97% loss-offset reduction, not a parse error.

### F6 — the payload envelope differs

TWSE returns `payload["fields"]` / `payload["data"]` at the top level. TPEx nests them:
`payload["tables"][0]["fields"]` / `["data"]`. The extraction must come from the source
config, not from a shared assumption about response shape.

## 1.1 — Multi-market shape of `fetch_corporate_actions.py`

**Decision: market becomes a field on `SourceConfig`; the module-level `MARKET` and
`BASE_URL` constants go away.** A parallel market dimension was rejected because every
per-market fact is already per-source — base URL, payload envelope, date parameter
format, data floor, classifier vocabulary — so a second axis would only ever be read
through the source anyway.

`SourceConfig` gains:

| field           | why                                                              |
| --------------- | ---------------------------------------------------------------- |
| `market`        | written to the `market` column; picks the raw subdirectory (1.3) |
| `base_url`      | replaces the `BASE_URL` constant                                 |
| `payload_shape` | `"flat"` (TWSE) or `"tables"` (TPEx) — F6                        |

Source keys stay flat and do not collide (`twt49u`, `twtcau`, `twtauu`, `exdailyq`,
`revivt`, `etfsplit`, `etfrvs`), so `SOURCE_CONFIGS` keeps one dict and `run_daily.py`'s
`for source in sorted(SOURCE_CONFIGS)` picks up the TPEx sources with no change to its
loop. What _does_ change there: the spec requires one market's failure not to cost the
other's refresh, and `_refresh_corporate_actions` currently has no per-source
`try`/`except`, so a single TPEx outage would abort the TWSE sources that follow it
alphabetically. Isolation goes in that loop.

**Classifiers are extended, never replaced.** Both markets' vocabularies live in one map
per event family, so a TWSE row and a TPEx row that mean the same thing produce the same
`event_type`:

- `現金減資` → `CASH_CAPITAL_REDUCTION`, alongside the existing `退還股款` (F1)
- `除息`/`除權`/`除權息` → the existing dividend/right/both events, alongside
  `息`/`權`/`權息` (F2)

Removing the TWSE spellings would silently reclassify the 1,023 listed symbols that
already have coverage, so they stay.

## 1.2 — Raw-range checkpoint table

**Decision: `ALTER TABLE twse_twt49u_raw_ranges RENAME TO corporate_action_raw_ranges`,
inside `init_db`, guarded on the old name existing and the new one not.**

The name is doubly wrong once TPEx rows arrive: not TWSE, and not `twt49u` (it has held
`twtcau` and `twtauu` rows since those sources were added). A rename is preferred over a
copy-and-drop because it moves the rows atomically and destroys nothing — the standing
rule in `.claude/rules/common/data-structures.md` rules out any migration with a `DROP`
in it. Adding a second table was rejected: the cache lookup would have to consult both
forever.

No key change is needed. The PK is `(source, start_date, end_date)` and `source` already
carries a market prefix (`twse_twt49u`), so TPEx values (`tpex_exdailyq`) coexist without
collision. `run_status` is the only other reader and is in the same module.

## 1.3 — Raw-response directory layout

**Decision: `data/raw/<market-lowercase>/<source>/`, which is what TWSE already uses.**

`DEFAULT_RAW_DIR` moves from `data/raw/twse` up to `data/raw`, and `run_fetch` joins
`args.raw_dir / source_config.market.lower() / source_config.cache_dir`. TWSE therefore
resolves to `data/raw/twse/twt49u/` — byte-identical to today, so **no existing file
moves and no migration is needed**; TPEx lands at `data/raw/tpex/exdailyq/`. This is also
what the change's own spec delta already requires ("under `data/raw/<market>/<source>/`").

`run_daily.py`'s `_corporate_action_fetch_args` passes `raw_dir=DEFAULT_RAW_DIR` and
inherits the new meaning with no edit beyond the import staying valid.

## 1.4 — `bulletin/revivt` field mapping

Field-for-field onto the existing row shape. Names differ from TWSE's throughout, which
is why this is a separate normalizer rather than a shared one:

| `revivt` field               | column                        | note                         |
| ---------------------------- | ----------------------------- | ---------------------------- |
| `恢復買賣日期`               | `ex_date`                     | compact ROC — see F3         |
| `股票代號`                   | `code`                        |                              |
| `名稱`                       | `name`                        |                              |
| `最後交易日之收盤價格`       | `previous_close`              |                              |
| `減資恢復買賣開始日參考價格` | `reference_price`             |                              |
| `漲停價格`                   | `limit_up_price`              |                              |
| `跌停價格`                   | `limit_down_price`            |                              |
| `開始交易基準價`             | `auction_base_price`          | TWSE calls it `開盤競價基準` |
| `除權參考價`                 | `ex_dividend_reference_price` |                              |
| `減資原因`                   | `action_type` → `event_type`  | F1                           |
| `詳細資料`                   | — **not parsed**              | embedded HTML table          |

`price_factor = reference_price / previous_close`, reusing `TWSE_REFERENCE_PRICE_RATIO`
unchanged. `evidence_status` is `REFERENCE_PRICE_ONLY`, matching how TWSE's `twtauu` rows
are already recorded — the bulletin publishes a reference price, not a per-share cash
amount.

`詳細資料` does contain the per-share figures (`每仟股換發新股份`, `每股退還股款`) inside
an HTML table. Parsing it is deliberately out of scope here: it would upgrade these rows
to `EVIDENCE_FULL` and is worth its own change, with its own tests, rather than being
smuggled in as part of adding a market.

## Consequences for the task list

Tasks 2.2 and 2.3 must extend the two classifiers (F1, F2) rather than reuse them as
written, task 2.x must add a compact-ROC branch to the date parser (F3), and task 4.x
must not assert `factor > 1` (F5). These are amendments to the plan, not new scope.
