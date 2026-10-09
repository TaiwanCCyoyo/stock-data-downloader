# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-29-add-tpex-corporate-actions/specs/corporate-actions-index/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-29-add-tpex-corporate-actions/specs/corporate-actions-index/spec.md).
No production action or acquisition is authorized by this record.

## MODIFIED Requirements

### Requirement: Local corporate-action index

The system MUST fetch corporate-action source data from both Taiwanese markets and index it into
`data/corporate_actions.sqlite`, keyed by source, market, code, and ex-date:

- TWSE listed instruments from the TWSE sources `twt49u`, `twtcau`, `twtauu`.
- TPEx OTC instruments from the TPEx bulletins `exDailyQ` (ex-rights / ex-dividend calculation
  results) and `revivt` (capital reduction, resumed-trading reference price), plus
  `etfSplitRslt` and `etfRvsRslt` for ETF splits and reverse splits.

Rows from both markets MUST share one table, one key, and one event-type vocabulary, so a
consumer reads OTC and listed events the same way and can tell them apart only by `market`.

#### Scenario: Fetch a date range

- **WHEN** `fetch_corporate_actions.py fetch` runs for a source and date range
- **THEN** it MUST request the range from that source's endpoint
- **AND** it MUST persist the raw JSON response under `data/raw/<market>/<source>/` with a
  SHA-256 checksum
- **AND** it MUST upsert normalized rows into the `corporate_actions` table, keyed on
  `(source, market, code, ex_date)`.

#### Scenario: Re-fetch a previously cached range

- **WHEN** a requested range is already recorded as fetched and `--force` is not set
- **THEN** the system MUST skip the network request for that range.

## ADDED Requirements

### Requirement: A source with no rows is not evidence about its schema

The system MUST NOT derive a field mapping, a schema era, or a conclusion about a source's shape
from a response containing no rows, because an endpoint may return a different field list when it
has no rows than when it has rows.

#### Scenario: An empty range response

- **WHEN** a corporate-action range query returns a successful response containing no rows
- **THEN** the system MUST record the range as fetched-and-empty
- **AND** it MUST NOT update or select any field mapping based on that response's field list.

### Requirement: An unobserved event type is recorded as unproven, not as absent

The system MUST NOT treat a registered source having never returned a row as evidence that its
event type does not occur. Such a source MUST remain registered and queried.

#### Scenario: A registered source has never produced a row

- **WHEN** a source has returned zero rows across every range fetched from it
- **THEN** the system MUST continue to query it on subsequent runs
- **AND** local coverage inspection MUST be able to report it as registered-but-unobserved
  rather than silently indistinguishable from a source that was never configured.
