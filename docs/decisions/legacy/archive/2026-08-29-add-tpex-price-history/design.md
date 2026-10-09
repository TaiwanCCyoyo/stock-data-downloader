# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-29-add-tpex-price-history/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-29-add-tpex-price-history/design.md).
No production action or acquisition is authorized by this record.

# Design

Endpoint facts and probe evidence live in `docs/tpex-history-sources.md`. This
records the three decisions that shape the implementation.

## Decision 1: history volume matches Shioaji; the nightly feed is the outlier

The nightly OpenAPI bulk fetch and this history endpoint do not report the same
volume. Every `afterTrading/otc` volume is an exact multiple of 1,000 (10,602 of
10,602 for 2026-08-19), and volume and value are scaled by the same factor
against the stored bulk row — 0.9796/0.9796 for 3105, 0.9335/0.9333 for 4966 —
which rules out rounding, since that would move volume alone.

Which of the two is the odd one out was settled by checking both against a third,
independent source: this repository's own Shioaji-derived daily bars. On
2026-07-01 and 2026-07-02, across five OTC symbols, **the backfilled rows match
Shioaji exactly — every close to the cent and every volume to the share, 0.00%
difference.** For 3105 on 2026-08-19 the history endpoint and Shioaji both say
17,582,000 while the nightly bulk says 17,947,311, 2.08% higher.

So the history endpoint counts the regular round-lot session, exactly as Shioaji
does, and it is the nightly OpenAPI feed that includes something extra — odd-lot
(零股) trading, which is under 1,000 shares per trade and would break the
multiple-of-1,000 property.

**Decided: store it in the existing `volume` column.** Both figures are in shares
and this one agrees with the rest of the pipeline. Backfilled TPEx days will
therefore _agree more closely_ with the data-quality report's official-vs-Shioaji
cross-check than nightly-fetched days do, not less — the opposite of what a
"different source" usually implies.

Consequences worth writing down rather than discovering:

- `official_daily_price` becomes a table whose TPEx volume has two provenances,
  differing by the odd-lot component: about 2% on the symbols measured. A
  consumer comparing volume across a backfilled and a nightly day should expect
  that step, and should know the backfilled side is the one that matches Shioaji.
- Rejected alternative: adding a provenance column. It would make the difference
  self-describing, but it changes the artifact's schema for every reader in order
  to annotate a difference that is documented, bounded, and in the direction of
  agreement rather than error.
- Not decided here: whether the nightly feed should be switched to this endpoint
  for consistency. That would trade a same-day fetch for a next-day one, since
  this endpoint publishes later, and belongs in its own change.

## Decision 2: candidate dates come from days TWSE already proves were trading days

TPEx answers a holiday and a date before its data floor identically — `stat: "ok"`
with no rows and no distinguishing message — so a crawler working from a calendar
range cannot tell "nothing traded" from "nothing published", and would either
re-request every weekend forever or record holidays as permanently empty.

**Decided: select candidate dates from `official_daily_price` itself** — dates
that have TWSE rows (proof the market was open) and no TPEx rows (proof of a
gap). Nothing else is requested.

This makes the empty-response problem disappear rather than papering over it: a
proven trading day that returns no TPEx rows is a genuine anomaly, recorded as
`empty` and not retried automatically, and `--force` re-attempts it. It also
bounds the work exactly to real gaps instead of a date range.

**Scope consequence, stated because it narrows what the proposal promises:** TPEx
coverage becomes limited to dates where TWSE history already exists — 2013-09-02
onward — not the endpoint's 2007-07-02 floor. Earlier TPEx history stays reachable
by explicit date arguments, but the no-argument auto mode will not go looking for
it.

The gap this targets today is 3,152 days: the artifact holds 3,163 TWSE trading days
and 12 TPEx days.

## Decision 3: a whole-market unit is `code = ''` in a renamed `period` column

The checkpoint table keys units on `(market, code, year_month)`. A TPEx unit is a
whole market-day: it has no code, and its period is a day rather than a month.

**Decided: rename the column `year_month` to `period` and use `code = ''` for a
whole-market unit.** `period` then holds `YYYY-MM` for a TWSE symbol-month and
`YYYY-MM-DD` for a TPEx market-day, the primary key keeps working unchanged, and
`''` cannot collide with a real code.

The rename is `ALTER TABLE ... RENAME COLUMN`, a metadata-only operation, so the
1.4 GB database is not rewritten. It runs in the same init path that already
creates the table, under the existing busy timeout, so it does not need the
backfill to be stopped.

Rejected alternatives: keeping the name `year_month` while storing dates in it
(the misnomer this repository is already cleaning up elsewhere); a second table
(two shapes to keep consistent for one extra unit type); a `unit_kind` column
(redundant — `code = ''` already says it).
