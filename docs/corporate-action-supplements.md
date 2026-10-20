# Corporate-action supplement batches

[Documentation index](README.md) · [Corporate-actions contract](contracts/corporate-actions-index/spec.md)

The annual exchange bulletins in `corporate_actions` record that an event happened and
its reference price. They do not carry most of the terms behind it: subscription
quantity and price, the split between earnings and reserve grants, record and payment
dates, capital-reduction ratios, par-value exchanges, or trading suspensions. Those
terms come from per-event documents captured separately. A **supplement batch** is an
immutable directory of such captures; `scripts/import_corporate_action_supplement.py`
normalizes it into four additive tables in the same `corporate_actions.sqlite`.

```powershell
uv run python scripts/import_corporate_action_supplement.py --batch <batch_id> --dry-run
uv run python scripts/import_corporate_action_supplement.py --batch <batch_id>
```

The importer reads `<DATA_DIR>/corporate_action_supplements/<batch_id>/`, verifies every
member listed in its `publication.json` (bytes and SHA-256), and writes in one
transaction. It never reads or changes `corporate_actions` or
`corporate_action_raw_ranges`, never creates the database, and refuses the weekday
18:00–18:45 Asia/Taipei window. Re-running an imported batch with identical content is
a no-op; different content under the same batch id is refused, so a corrected batch
needs a new id. Check the scheduled tasks before a real import, as for any write to
the data directory ([operations](operations.md)).

## Tables

All four carry `batch_id`. A batch never claims completeness: a code or date absent
here was simply not captured.

### `corporate_action_terms`

One row per (document, code, event date, term). Primary key
`(batch_id, member_path, code, event_date, term)`; indexed on `(code, event_date)`.

| column                 | meaning                                                                                                                                                                                                                                           |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `market`, `code`       | `TWSE` or `TPEx`; issuer code                                                                                                                                                                                                                     |
| `event_date`           | the date the document attaches the terms to                                                                                                                                                                                                       |
| `event_date_role`      | what that date is: `stated_ex_date` (MOPS), `exchange_ex_date` (TPEx exDailyQ), `annual_row_effective_date` (TWSE detail, bound by the annual row it was requested for), or a notice role such as `resumption_date` or `new_shares_start_trading` |
| `event_type`           | the producer vocabulary when the document itself states it (exDailyQ `權/息`, notices); otherwise NULL. Join `corporate_actions` on `(market, code, ex_date)` for the bulletin's type                                                             |
| `term`, `unit`         | see the vocabulary below                                                                                                                                                                                                                          |
| `value`                | the stated value as text: an exact decimal in source units (thousands separators removed, trailing zeros kept), an ISO date, or a literal. NULL unless `value_status = 'stated'`                                                                  |
| `value_status`         | `stated`, `blank` (the template has the slot but no number), `missing`, `unsupported` (the parser does not recognize this template), `ambiguous`, `invalid`                                                                                       |
| `extraction`           | `parsed` (re-read from the raw response by a producer parser) or `curated_transcription` (transcribed from a notice that has no parser)                                                                                                           |
| `source_kind`          | `mops_t108sb19_detail`, `tpex_exdailyq`, `twse_twt49u_detail`, `official_notice`                                                                                                                                                                  |
| `member_path`          | the document inside the batch; join `corporate_action_source_documents`                                                                                                                                                                           |
| `source_announced_raw` | MOPS displayed announcement date, as listed                                                                                                                                                                                                       |
| `raw_literal`          | the source cell or line the value came from                                                                                                                                                                                                       |

Term vocabulary:

| term                                                                                                                                                                                                                                | unit                    |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------- |
| `cash_dividend_per_share`, `stock_dividend_money_per_share`, `subscription_price`, `old_par_value`, `new_par_value`                                                                                                                 | `NTD_per_share`         |
| `free_shares_per_1000` (exchange total), `earnings_free_shares_per_1000`, `reserve_free_shares_per_1000` (MOPS components), `subscription_shares_per_1000`, `shares_reduced_per_1000`                                               | `shares_per_1000_held`  |
| `new_shares_per_old_share`                                                                                                                                                                                                          | `shares_per_share_held` |
| `cash_capital_increase_shares`, `public_offering_shares`, `employee_subscription_shares`, `shareholder_subscription_shares`, `employee_bonus_shares`, `ordinary_shares_before`/`_after`, `private_placement_shares_before`/`_after` | `shares` (issue totals) |
| `record_date`, `scheduled_cash_payment_date`                                                                                                                                                                                        | `iso_date`              |
| `subscription_class`                                                                                                                                                                                                                | `share_class`           |
| `reference_price_rule`                                                                                                                                                                                                              | `formula_text`          |

Reading rules:

- **Each source is its own row.** MOPS and the exchange can differ in a late decimal
  place (one 2016 TPEx event: `54.55595036` versus `54.55595037` subscription shares
  per thousand). Nothing here chooses, rounds or averages; that is consumer policy.
- **MOPS revisions are separate documents.** Two details for the same event can both
  be present, for example one with a blank subscription price and a later one stating
  it. The projector neither selects a revision nor certifies completeness.
- **Blank, missing and unsupported are not zero.** Only `stated` rows carry a value.
- **A subscription is an offer, not a delivery.** Subscription quantity and price are
  stated terms; whether anyone subscribed, and when shares or cash arrived, is not
  recorded. `scheduled_cash_payment_date` is the announced schedule.
- **Nothing is derived.** Share factors, price factors and effective trading dates are
  not stored; compute them from terms with a stated rule.
- **Curated transcriptions are checked where possible.** A transcription that cites
  only parsed documents is compared with the parsed value and the import stops on any
  difference; it is not stored twice. A transcription citing a document without a
  parser is stored with `extraction = 'curated_transcription'`.
- **exDailyQ rows cover the whole captured day**, not only the issuer that prompted the
  capture.

### `trading_interruptions`

One row per interval. Primary key `(batch_id, market, code, interruption_type, start_date)`.

| column                  | meaning                                                                       |
| ----------------------- | ----------------------------------------------------------------------------- |
| `code`                  | issuer, or `''` for a whole-market closure                                    |
| `interruption_type`     | `trading_suspension` or `market_closure`                                      |
| `start_date`            | first affected day, inclusive                                                 |
| `end_date`              | last affected day, inclusive; NULL when the source does not establish the end |
| `resumption_date`       | first trading day after; NULL when not established                            |
| `evidence_member_paths` | JSON array of the documents the interval was transcribed from                 |
| `note`                  | the transcription's endpoint wording                                          |

An interval says the day was officially closed or suspended. Its absence says nothing:
a day without an interval and without a quote has an unknown cause.

### `corporate_action_source_documents`

One row per document a term or interval cites: `member_path`, `sha256`, `bytes`,
`document_kind`, `publisher`, the batch's `evidence_id` when it registered one,
`source_url` and `retrieved_at_utc`. Historical public availability is never recorded.

### `corporate_action_supplement_batches`

One row per imported batch: publication schema and SHA-256, member count and bytes,
`root_relative` (the batch directory relative to the data directory),
`content_sha256` over every imported row, importer version and import time.

## Batch layout the importer understands

`publication.json` with `schema = "annual-source-evidence.v1"`, `batch_id` equal to the
directory name, `members` (path → bytes, sha256) and an optional `candidate`. Captures
are recognized by their manifests:

| manifest                                         | parser                                                                                                 |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------ |
| `schema_version = "mops-dividend-evidence.v1"`   | each validated detail, bound to its list row by request parameters, through `project_rights_statement` |
| `schema_version = "corporate-detail-capture-v1"` | each receipted TWT49U detail, by field name; date from the annual row it was requested for             |
| `request_url` containing `/bulletin/exDailyQ`    | every row, by field name; an empty response adds nothing                                               |

The candidate (`annual-winner-supplement-candidate.v1`) supplies evidence ids, the
market of MOPS issuers, curated notice facts and interruption intervals. Its other
content (derivations, per-day gap observations, cases) is not imported. An explicit
source fact the importer has no mapping for stops the import rather than being dropped.

## Backup

`corporate_actions.sqlite` is in both the additive mirror and the quarterly
`data_meta` archive, so the four tables are too. Batch directories are covered by the
recursive mirror only.

## Batches

| batch                       | documents | terms | intervals | not covered |
| --------------------------- | --------- | ----- | --------- | ----------- |
| `annual-winner-20261013-v1` | 21        | 273   | 6         | see below   |

`annual-winner-20261013-v1` holds what its captures state. It does not hold:

- the 2011 MOPS rights template for one TWSE issuer, which the projector does not
  support (those rows are `unsupported`; the TWSE detail for the same event is parsed);
- unit confirmation for MOPS free-share fields beyond the projector's labels;
- a trading-method change notice (2018), which is not a corporate action;
- per-security monthly quote observations and whole-market daily quote responses,
  which belong to the official price pipeline;
- a resumption date for one suspension starting 2025-10-14 (open interval);
- a reference price or price factor for a 2012 capital reduction whose notice gives none;
- actual subscription, payment or delivery, historical public availability, and
  revision completeness, for every event.
