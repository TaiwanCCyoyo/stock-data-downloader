> Historical source report retained for review; measurements are not current coverage guarantees. Publication attribution/terms classification is pending, not a finding of infringement.

# TWSE Dated Whole-Market Prices

MI_INDEX publishes dated whole-market closing quotes from the official TWSE
site. The query uses `date=YYYYMMDD`, `type=ALLBUT0999`, and `response=json`:
[official dated endpoint](https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20261002&type=ALLBUT0999&response=json).

## Observed schema and quantities

On 2026-10-04 the price table had the same 16 field names for all tested eras.
The parser selects its exact field-name signature, rather than the observed
table index. Other summary tables are not price observations. The response date
must match the requested date; unknown nonempty schemas fail before writing.

Additional live probes compared every common code with stored STOCK_DAY rows:

| Date       | MI_INDEX codes | Stored TWSE codes | Additional codes | OHLC, shares, value, transaction mismatches |
| ---------- | -------------: | ----------------: | ---------------: | ------------------------------------------- |
| 2011-06-15 |            819 |               724 |               95 | 0 in every field                            |
| 2015-06-15 |            922 |               837 |               85 | 0 in every field                            |
| 2017-06-15 |          1,007 |               934 |               73 | 0 in every field                            |
| 2018-06-15 |          1,057 |               984 |               73 | 0 in every field                            |
| 2022-06-15 |          1,166 |             1,130 |               36 | 0 in every field                            |
| 2024-06-14 |          1,244 |             1,224 |               20 | 0 in every field                            |

The original audit additionally tested 2010-01-04, 2013-03-05, 2020-06-15,
2026-09-30, and 2026-10-01. Common rows had identical OHLC and traded
quantities to STOCK_DAY. Six no-trade bulk rows on 2026-10-01 carried NULL
quantities where MI_INDEX carried zero; missing and zero are retained as
observed, rather than conflated to manufacture agreement. Prices use the same
TWSE all-trade quantity definition as STOCK_DAY. This does not alter TPEx's
distinct historical round-lot definition.

No-trade prices can be `--` even with a small odd-lot volume. A missing close
or zero volume yields `close_missing=true` and a NULL close. Security names
are preserved so venue transfers or reused codes can be investigated. An
additional historical code is not automatically a delisted common stock.

## Availability and preservation

A no-data response does not distinguish a holiday from an unpublished day.
Recent empties and empties contradicting existing TWSE rows remain retryable.
The same-day supplement is opportunistic and never depends on an assumed
publication time. Sunday probes establish historical availability, not what
time a trading day's data is published; that time remains unmeasured.

Day repairs insert missing primary keys only. They never replace existing
raw evidence, timestamps or values. Subsequent bulk and STOCK_DAY writes keep
their prior precedence. Historical rows from MI_INDEX do not expand the
STOCK_DAY symbol-month universe by themselves.

The supported repair floor remains 2010-01-04 even though the audit obtained a
2009-12-31 response. Earlier history and a broader parquet symbol universe need
separate decisions. CSV-backed symbols can gain longer official histories on a
later parquet rebuild; compare overlapping closes and instrument identity and
record the coverage change before comparing research vintages.
