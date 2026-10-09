# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-29-add-tpex-corporate-actions/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-29-add-tpex-corporate-actions/proposal.md).
No production action or acquisition is authorized by this record.

## Why

The corporate-action index covers TWSE only. `fetch_corporate_actions.py` hardcodes
`MARKET = "TWSE"` and a TWSE base URL, so no OTC symbol has ever had an ex-dividend,
ex-right, or capital-reduction row recorded. Every row in `corporate_actions.sqlite`
carries `market = 'TWSE'`.

The consequence is silent rather than loud. A consumer that adjusts prices from this
index gets correct adjustments for listed symbols and no adjustment at all for OTC
ones — not an error, not a warning, just an unadjusted price series that looks
plausible. The data-quality report shows the scale: 870 flagged symbols have no
corporate-action rows whatsoever.

This was left open because TPEx was believed not to publish the equivalent data. It
does. `docs/tpex-history-sources.md` records the probe evidence for two dated,
range-queryable bulletins that correspond to the two TWSE sources already indexed, and
their fields map onto the existing normalizer almost one-to-one — including the
previous close and reference price the existing price-factor computation is built on.

## What Changes

- Add TPEx as a second market in the corporate-action index, written into the same
  `corporate_actions` table with `market = 'TPEx'`, keyed as today on
  `(source, market, code, ex_date)`.
- Add two TPEx sources, both queried by `startDate`/`endDate` like the TWSE sources:
    - `bulletin/exDailyQ` — ex-rights / ex-dividend calculation results, the counterpart
      of `twt49u`, available from 2008-01-02. Its `除權息前收盤價` and `除權息參考價`
      give the same previous-close / reference-price pair the existing
      `TWSE_REFERENCE_PRICE_RATIO` factor method consumes, so the factor computation is
      reused rather than reinvented.
    - `bulletin/revivt` — capital reduction, resumed-trading reference price, the
      counterpart of `twtauu`, available from 2013-01-01. Its `減資原因` carries the
      subtype the existing classifier uses to separate a cash reduction from a
      loss-offset reduction.
- Register `bulletin/etfSplitRslt` and `bulletin/etfRvsRslt` (ETF split and reverse
  split, from 2021-06-01) as sources, while recording that both currently return zero
  rows across their whole available window. They are wired but unproven: no TPEx ETF
  split has been observed to test the parse against, and an empty result here must not
  be read as a confirmed absence of the event type.
- Never infer a schema era from an empty response. `bulletin/exDailyQ` returns a
  different, older field list when it has no rows than when it has rows.
- Refresh TPEx alongside TWSE in the daily run's corporate-action step, on the same
  current-year-plus-previous-year policy, so the index does not immediately start
  falling behind for the new market.
- Rename the raw-range checkpoint table, or otherwise stop its TWSE-specific name
  (`twse_twt49u_raw_ranges`) from describing rows that are no longer TWSE-only. The
  migration path for existing rows is a design-step decision.

## Capabilities

### Modified Capabilities

- `corporate-actions-index`: stops being TWSE-only; gains TPEx sources, TPEx rows, and
  a market dimension that is actually exercised.
- `daily-update-orchestration`: the corporate-action refresh step covers both markets.

## Non-Goals

- **No new credential or authenticated call.** These endpoints are public and
  unauthenticated, like every other official source this repository reads.
- **No change to the stored row shape.** TPEx rows use the existing `corporate_actions`
  columns and the existing event-type vocabulary. A reader written against the current
  contract sees more rows, not different ones.
- **No change to how price factors are computed.** The reference-price ratio method is
  reused unchanged; this change supplies it with TPEx inputs.
- **No backfill of TPEx history beyond what the endpoints serve.** Their floors
  (2008-01-02 and 2013-01-01) are the floors.
- **No forward-looking announcement data.** TPEx's pre-announcement boards
  (`bulletin/prePost`, `bulletin/decap`) have no date parameters and are not history
  sources; they are out of scope.

## Impact

- Downstream artifact contract: `data/corporate_actions.sqlite`'s `corporate_actions`
  table gains rows with `market = 'TPEx'`. Existing columns, keys, event types, and the
  `price_factor` / `evidence_status` semantics are unchanged. A reader that filters on
  `market` keeps working; a reader that assumed every row is TWSE now sees OTC rows.
- The raw-range checkpoint table is renamed or otherwise generalized, which changes a
  table name in the same database. Nothing outside the fetcher reads it.
- Any analysis that compared "symbols with corporate-action coverage" before and after
  this change will not be comparable across it, because price adjustment for OTC
  symbols becomes possible where it previously silently did not happen.
