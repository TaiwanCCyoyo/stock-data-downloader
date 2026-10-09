# TPEx dated-history source contracts

This method summary replaces observation excerpts in the legacy sourcing report.
No captured price row, measured price comparison or volume is distributed here.
The original report remains in the private legacy source commit; see its hash in
[the migration manifest](migration/source-files.csv).

## Endpoint discovery and dates

Discover actions from table-page initialization and form field names. The website
uses `/www/zh-tw/<action>` with `response=json`; do not infer a historical endpoint
from OpenAPI coverage alone. Verify that changing dates changes nonempty content.
An HTTP 200 response is insufficient. Requests use Gregorian dates; response dates
may use the ROC calendar. Observed data floors are evidence, not guaranteed coverage.

| Action                                         | Parameters             | Contract                                                                   |
| ---------------------------------------------- | ---------------------- | -------------------------------------------------------------------------- |
| `afterTrading/otc`                             | `date`, `type`         | Whole-market daily quotes; explicitly choose the instrument universe       |
| `afterTrading/tradingStock`                    | `code`, `date`         | Per-symbol month; volume/value units differ from the whole-market endpoint |
| `bulletin/exDailyQ`                            | `startDate`, `endDate` | Ex-rights/dividend calculations; inspect populated response fields         |
| `bulletin/revivt`                              | `startDate`, `endDate` | Capital-reduction resumed-trading prices and subtype evidence              |
| `bulletin/etfSplitRslt`, `bulletin/etfRvsRslt` | `startDate`, `endDate` | Empty observed responses do not establish an absence of events             |

## Parsing and units

Match normalized field-name signatures, not column counts or assumed date eras.
Normalize markup and whitespace. Unknown nonempty schemas must fail visibly and
remain retryable rather than silently mapping positions. Empty responses can have
an older field list; they cannot identify a populated schema.

The whole-market endpoint reports shares, while per-symbol month fields named
`成交仟股` and `成交仟元` require scaling by 1,000 before storing shares and NTD.
Round-lot and bulk feeds may count different trading components; source agreement
must be evaluated explicitly, and provenance preserved. No numerical comparison
from the old private dataset is included here.

Reference-price factors are ratios of the named reference price to previous close;
capital-reduction reasons distinguish cash from loss-offset cases. Parse detail
HTML using named columns. Empty holidays and before-floor responses may look alike;
use an independent calendar and retain uncertainty.

Forward-looking announcement boards and latest-only OpenAPI snapshots do not prove
historical coverage. Rate-limit ceilings and publication timing remain unmeasured.
See [official source contract](contracts/official-price-source/spec.md) and
[institutional sources](institutional-sources.md).
