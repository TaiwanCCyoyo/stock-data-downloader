# Corporate Actions Index

## Purpose

Define the baseline behavior for fetching and indexing TWSE corporate-action events (ex-dividend, ex-right, ETF splits, capital reductions) into a local, queryable store.

## Requirements

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

### Requirement: Event classification and price-adjustment factors

The system MUST classify each corporate-action row into an event type (cash dividend, ex-right, ex-right-and-dividend, ETF split/reverse-split, capital reduction variants) and MUST compute a price-adjustment factor from the previous close and reference price when both are available.

#### Scenario: Compute a price factor

- **WHEN** a corporate-action row has a positive previous close and a reference price
- **THEN** the system MUST compute `price_factor = reference_price / previous_close`
- **AND** it MUST record the factor method used and an evidence status describing how complete the underlying data is.

### Requirement: Local coverage inspection

The system MUST provide a way to inspect local corporate-action coverage without a live network fetch.

#### Scenario: Query status

- **WHEN** `fetch_corporate_actions.py status` runs, optionally filtered by code or event type
- **THEN** it MUST report row counts, distinct codes, date range, and per-year coverage from the local database only.

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
