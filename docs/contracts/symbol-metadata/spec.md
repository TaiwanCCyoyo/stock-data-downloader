# Symbol Metadata

## Purpose

Define the baseline behavior for fetching and indexing TWSE/TPEx symbol classification (industry category, ETF flag, listing date) into a local store.

## Requirements

### Requirement: Local symbol classification index

The system MUST fetch the TWSE/TPEx ISIN public listing pages and index tradable stock and ETF symbols into `data/symbol_meta.sqlite`, keyed by code.

#### Scenario: Fetch classification data

- **WHEN** `fetch_symbol_meta.py fetch` runs for one or more markets (twse, tpex)
- **THEN** it MUST parse the ISIN listing table for that market
- **AND** it MUST keep only stock and ETF segments, excluding warrants, bonds, TDRs, and REIT beneficiary certificates
- **AND** it MUST upsert each row into `symbol_meta`, keyed on `code`.

#### Scenario: Persist raw source and fetch log

- **WHEN** a fetch completes for a market
- **THEN** the system MUST persist the raw HTML under `data/raw/isin/`
- **AND** it MUST record a fetch log entry with market, URL, row count, and a SHA-256 checksum of the raw content.

### Requirement: Local classification lookup

The system MUST support looking up a single symbol's classification and reporting aggregate local coverage, both without a live network fetch.

#### Scenario: Look up a symbol

- **WHEN** `fetch_symbol_meta.py lookup --code <code>` runs
- **THEN** it MUST return the symbol's market, industry category, ETF flag, and listed date from the local database
- **AND** it MUST report no data found when the code is absent, without raising an error.
