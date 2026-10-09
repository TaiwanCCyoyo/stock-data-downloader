# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-versioned-technical-cache/specs/technical-feature-cache/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-versioned-technical-cache/specs/technical-feature-cache/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: Versioned local technical components

The system SHALL emit technical-components.v1 feature Parquet with Code/Date keys, price_valid, unrounded SMA5/10/20/60/120, SMA-seeded EMA12/26, MACD12_26/MACDSignal9/MACDHistogram, Return5/20/60/120, PriorHigh20/60, PriorLow20/60, BBMean20/BBStd20/BBUpper20/BBLower20, Wilder ATR14/RSI14, VolumeMean5/20 and VolumeRatio5_20. Returns SHALL be fractions and volume SHALL use lots. Bollinger standard deviation SHALL use ddof=0 and bands two standard deviations. Wilder RSI SHALL use 14 price changes, with flat sequences 50 and positive-only changes 100. No future outcome or strategy decision SHALL be included.

#### Scenario: Warm-up and invalid observations

- **WHEN** history is shorter than a component's required window or a price row is invalid
- **THEN** unavailable values SHALL remain unknown, not zero
- **AND** an invalid OHLC row SHALL reset price-component warm-up, without bridging it
- **AND** missing dates SHALL not be manufactured or treated as known exchange closures; windows count observed rows, not guaranteed exchange sessions.

#### Scenario: Distinct input basis

- **WHEN** an explicitly supplied normalized price input is labelled by its caller
- **THEN** its declared price basis and gap/seed/precision definitions SHALL be recorded and included in cache identity
- **AND** the builder SHALL not claim that the label verifies PIT safety or corporate-action correctness.

### Requirement: Exact reusable identities and retained generations

The system SHALL retain additive objects under data/technical_features/objects/ and immutable generation manifests under data/technical_features/manifests/, or a caller-selected output directory. Objects SHALL include normalized input rows, features and SHA-256 evidence. Identity SHALL cover relevant row values, keys, source, basis, contract, implementation and dependency versions. A generation SHALL list exact object paths/hashes, input and output columns, coverage and sources. Consumers SHALL be able to verify a pinned generation and read the exact feature rows after newer generations exist.

#### Scenario: Reuse or historical correction

- **WHEN** the same symbol input and definition are requested again
- **THEN** verified saved features SHALL be reused without recalculating that symbol
- **AND** a changed old price, source, date, volume, basis or implementation SHALL yield a distinct identity and recomputation, preserving older objects.

#### Scenario: Corruption and interruption

- **WHEN** an existing object's evidence is missing, malformed or hash-inconsistent
- **THEN** the builder and reader SHALL fail explicitly, without silently overwriting or accepting it
- **AND** incomplete staging SHALL not be published as a completed generation
- **AND** concurrent builders for the same output root SHALL be excluded by an explicit writer lock.

### Requirement: Backward-compatible pipeline integration

The consolidated Parquet build SHALL support an explicit `--technical-features` option to generate sibling technical features after completing its existing official-first price build. Its daily default SHALL NOT accumulate these historical generations automatically. It SHALL preserve all existing CSV/Parquet fields, values and source priorities. A standalone local-only CLI SHALL generate features without downloading or modifying prices. Data defaults SHALL be anchored to config.DATA_DIR rather than CWD, and an explicitly selected input SHALL require a caller-declared price basis.

#### Scenario: Existing consumers remain compatible

- **WHEN** the consolidated build runs on existing source fixtures with technical features explicitly requested
- **THEN** its previous price columns and values SHALL remain equal
- **AND** an independently verifiable technical manifest SHALL be available for that input
- **AND** a feature-cache failure SHALL be surfaced as a failed build step, not successful fresh features.

### Requirement: Discoverability and preservation contract

Documentation SHALL state definitions, producer/reader commands, keys, missing semantics, ownership, reuse compatibility, retained-input scope, storage growth and acquisition limits. It SHALL distinguish local ignored generated artifacts from tracked code/specs and SHALL not describe a Git clone or an unverified backup as carrying the data. Generation logs SHALL report computed/reused symbols and bytes written. There SHALL be no automatic deletion or migration of historical research artifacts.

#### Scenario: A new consumer locates data

- **WHEN** a new consumer follows the documented contract
- **THEN** it SHALL be able to find and verify a pinned generation, identify its input basis and coverage, and decide whether its own definitions match without relying on a prior chat.
