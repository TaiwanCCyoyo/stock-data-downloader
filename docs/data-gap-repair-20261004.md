> Historical source report retained for review; measurements are not current coverage guarantees. Publication attribution/terms classification is pending, not a finding of infringement.

# Official Data Repair, 2026-10-04 to 2026-10-05

The repair writes the primary checkout's `data/` files. The repository owner
directly authorized the fixes and missing-data downloads in this Codex chat on
2026-10-04 at 21:39:17 Asia/Taipei, then required the final data to reside in the
primary checkout at 21:42:43. Those direct instructions superseded the earlier
handoff's recommendation to defer the production sweep until after merge.
The additive repair ran from the reserved worktree with explicit primary data
paths, before its source changes were merged. Code integration and scheduled
task changes have separate authorization; their rollout evidence is recorded
in [PR #6](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/pull/6) and its
preserved task receipts.

## Deployment ordering

Deploy the restricted symbol-month universe before scheduled backfill resumes
against the expanded history. Older code selects every TWSE code in the price
table; the 182 newly discovered historical codes would therefore generate
redundant STOCK_DAY month requests for history already acquired through MI_INDEX.
The repaired planner uses existing symbol checkpoints plus the newest TWSE date.

When data acquisition precedes code integration, keep the old scheduled backfill
paused under owner authorization until the reviewed release is in production.
Preserve task definitions, prevent new starts and verify writers have exited
before updating production files; then verify the primary checkout's existing
launchers before authorized resumption. Data-repair completion alone does not
establish that this production handoff is complete.

## Source and preservation evidence

Before acquisition, SQLite's online backup API produced consistent snapshots
of `official_daily.sqlite`, `institutional.sqlite`, and `monthly_revenue.sqlite`;
each snapshot passed `quick_check`. The snapshots, original handoff, raw probes,
and audit reports remain under the worktree's `.tmp/` directory.

The TWSE parser and numeric comparisons are recorded in
[dated whole-market source evidence](twse-market-day-source.md). The production
repair inserts absent `(market, code, date)` keys and keeps existing values,
raw JSON and observation timestamps.

## Official daily prices

All 4,105 TWSE trading-day units from 2010-01-04 through 2026-10-02 completed,
adding 260,028 rows. Both markets match the independent FMTQIK date set with
zero missing or extra stored dates in the verified months:

| Market | Before rows | After rows | Before codes | After codes | After dates |
| ------ | ----------: | ---------: | -----------: | ----------: | ----------: |
| TWSE   |   4,014,336 |  4,274,364 |        1,384 |       1,566 |       4,105 |
| TPEx   |  23,574,040 | 23,574,040 |       49,844 |      49,844 |       4,105 |

All 27,588,376 original price rows match across every stored column, including
NULLs, names, quantities, raw fields and timestamps. All 278,184 original
symbol-month checkpoints are unchanged. The final snapshot audit first proves
full-column equality by rowid, with primary-key fallback for changed rowids;
the total-row count equality then proves the appended-row enumeration is
complete. Functional cases cover changed ordering, deletion/reuse, low-rowid
insertion, missing values and WITHOUT ROWID tables.

The sweep retains 4,105 `ok` TWSE day checkpoints, zero `error`, one `empty`
for 2026-09-25 and one retryable `partial` for 2026-09-28. These two empty
responses are outside the verified trading calendar. Settlement used the
original 2026-10-04 observation-date bound throughout the resumed run.

The provider intermittently returned HTTP 200 HTML titled "Website maintenance"
after thousands of successful requests. Raw response evidence was retained;
failed dates were never relabeled empty. Bounded recovery completed at
2026-10-05 02:04:29 Asia/Taipei; its final 203-date batch had zero failed units.
The optional consecutive-failure stop commits errors before stopping, and
unfinished day checkpoints remain automatic retry candidates beyond 45 days.

The new data includes 182 previously absent TWSE codes whose last observed date
is before 2026-08. These are historical observations, not an automatic claim
of delisting or instrument continuity.

## Monthly revenue

The existing archive backfiller fetched all 324 market-month units for
2013-01 through 2026-06, adding 282,544 observations. Both markets now have 164
continuous months through the already-observed 2026-08:

| Market | First month | Last month | Months | Current observations |
| ------ | ----------- | ---------- | -----: | -------------------: |
| TWSE   | 2013-01     | 2026-08    |    164 |              158,815 |
| TPEx   | 2013-01     | 2026-08    |    164 |              127,682 |

All 3,953 existing current observations, 3,953 revisions, and 34 original raw
snapshot records are unchanged. The new historical observations retain NULL
`available_at` and `announcement_at`, with
`availability_kind=HISTORICAL_NO_EXACT_TIME`. Historical values do not prove
when they were available to a trader. The original 3,953 `FIRST_OBSERVED`
records retain their actual observation timing.

The 34 original and all 358 final raw response files match their recorded
SHA-256 values. A transient HTTP 502 retry succeeded; no archive unit failed.

Both markets returned HTTP 404 for the tested 2010-01 and 2012-12 CSV URLs;
2013-01, 2015-01, and 2026-06 returned the observed 14-field schema. Continuous
successful acquisition establishes the supported range above, without claiming
an exhaustive proof that every earlier archive or alternative source is absent.

## Institutional reports

The existing non-force backfiller repaired 640 missing trading days per market
in 2024-01-01 through 2026-09-30: 1,280 successful units, 1,343,140 added rows,
zero failed units, and zero refused erasures. All 2,179,852 original observations
and 2,492 original fetch-log records are unchanged across every stored column;
all 2,492 original raw references still match their recorded byte SHA-256.

| Market | First date | Last date  | Trading dates | Stored observations |
| ------ | ---------- | ---------- | ------------: | ------------------: |
| TWSE   | 2019-01-02 | 2026-10-02 |         1,882 |           2,128,964 |
| TPEx   | 2019-01-02 | 2026-10-02 |         1,882 |           1,394,028 |

Each market has an `ok` checkpoint for every one of the 664 trading days in the
target window. Comparison against FMTQIK finds no missing or extra stored date
in the full 1,882-day window. Four original `partial` checkpoints per market
remain on 2026-08-22, 2026-08-23, 2026-08-29, and 2026-08-30, outside the
validated trading calendar. The audit flags these preserved non-trading units;
it does not silently relabel them as completed. There are no new partial/error
units or target trading days without successful checkpoints.

The recorded current acquisition time is retained. Backfilled report dates do
not establish an exact historical publication time.

## Independent calendar and corporate actions

TWSE FMTQIK returned valid nonempty data for all 202 requested months,
2010-01 through 2026-10, yielding 4,105 trading dates through 2026-10-02.
The calendar includes 15 historical Saturday sessions; weekday-only inference
would incorrectly omit them. FMTQIK is independent of the individual-price
table, but this audit has not separately validated a TPEx-specific calendar.
Date presence alone does not establish complete per-security coverage.

The read-only corporate-action audit checked query ranges, raw-response hashes,
and response status for all seven configured sources. Their successful observed
ranges run through 2026-10-02. The TWSE `twtauu` source has eight explicit
pre-2011 query rejections despite its configured 2003 query floor; its observed
supported floor is 2011. This limitation is retained rather than described as
complete pre-2011 coverage. The two ETF sources have zero stored events, which
does not establish that no relevant event exists outside the observed responses.

## Artifact and timing boundaries

The stable `price_daily.parquet` has 6,576,397 rows, 2,075 codes, and baseline
SHA-256 `67ee9e48260d0ffe73180d0628d6a7f60238a3c576afce30b1ff6537c6ac8003`.
This repair does not rebuild it or broaden its CSV-backed universe. New SQLite
history can affect a future parquet vintage; overlapping closes, venue transfers,
name changes, and reused codes need evaluation before comparing vintages.

Among the 50 stable-parquet codes served entirely by Shioaji, 38 now have new
TWSE observations. Every affected code has valid overlapping closes from
2018-12 onward, and every comparison has zero median relative gap. Two
observations have differences above floating-point noise:

| Code | Date       | Official close | CSV close | Relative gap |
| ---- | ---------- | -------------: | --------: | -----------: |
| 1724 | 2020-02-18 |          12.90 |     12.85 |      0.3891% |
| 2888 | 2020-03-06 |           9.12 |      9.11 |      0.1098% |

Neither observation is overwritten. The audit retains source names and metadata;
numeric/name agreement alone does not resolve instrument identity.

The 100,320 valid newly inserted official rows belonging to 1,124 stable-parquet
codes have two different potential effects in a future generation:

- 67,360 dates are absent from the stable parquet, all within 40 codes. Admitting
  this history would extend those codes' bar counts.
- The remaining 32,960 dates already have a Shioaji parquet bar, across 1,123
  codes. Their effect is a source/value replacement on an existing bar.

These groups overlap in 39 codes; their union is the 1,124 affected codes.

Two other existing CSV codes (`2002A`, `9941A`) are outside the stable parquet
and account for two further potential bars. The CSV eligibility filter is
unchanged; these candidates are reported, not added to the stable universe.
Venue transfers including `2341`, `3454`, `4733`, and `6423` require instrument
identity review: combining history under `Code` alone can splice observations
from different venues even when their `(code, date)` pairs do not overlap.

The retained quality report is dated 2026-10-02T10:26:00+00:00 and reports zero
`unexplained_fallback` findings. It was not recalculated against the expanded
SQLite coverage and is not presented as a new post-repair quality evaluation.

Sunday probes establish historical endpoint availability. They do not measure
the publication time on a trading day. The optional daily TWSE supplement is
opportunistic, and publication timing remains unmeasured. Research missions,
historical results, and their input snapshots are preserved.

## Backup evidence

The primary checkout's additive mirror completed after writers had exited,
copying 7,836 changed files and verifying presence and byte count for 14,865
source files under `D:\StockDataBackup\data`. The backup parquet SHA-256 equals
the stable baseline above. All three repaired SQLite databases have matching
source/backup row counts and zero-byte WAL files at verification time.
Destination files, before snapshots and source evidence are preserved.

Run-specific commits, test and review results, scheduler states and the separate
technical-cache rollout receipts remain in the PR description and preserved
`.tmp/` evidence rather than defining this source-coverage record.
