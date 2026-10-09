# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-24-add-value-chain-classification-snapshots/specs/value-chain-classification/spec.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-24-add-value-chain-classification-snapshots/specs/value-chain-classification/spec.md).
No production action or acquisition is authorized by this record.

## ADDED Requirements

### Requirement: Official value-chain classification is fetched

The system MUST fetch the complete current taxonomy published by the Taipei Exchange Industry
Value Chain Information Platform and MUST retain each chain's ordered nodes and domestic
security membership.

#### Scenario: Fetch current classification

- **WHEN** the classification fetch runs against valid source pages
- **THEN** it MUST discover the published value-chain codes
- **AND** it MUST record each chain code, chain name, node code, node name, optional parent
  code, and published stage
  when the source provides one
- **AND** it MUST record a null stage rather than infer one for a non-linear source diagram
- **AND** it MUST record each domestic member's security code, name, and listing segment
- **AND** it MUST exclude external companies that do not have a Taiwan security-code link.

#### Scenario: Source structure is incomplete

- **WHEN** fetched pages yield no chains, no classification nodes, or no domestic securities
- **THEN** the refresh MUST fail with a clear validation error
- **AND** it MUST NOT replace the last known-good artifacts.

### Requirement: Classification has a stable JSON contract

The system MUST write UTF-8 JSON with stable ordering to
`data/value_chain_classification.json`. The root object MUST contain `schema_version`,
`source`, `fetched_at`, `snapshot_id`, `chains`, and `symbols`. `chains` MUST preserve the
published hierarchy and `symbols` MUST provide inverse symbol-to-node memberships.

#### Scenario: Read the latest artifact

- **WHEN** a caller reads `data/value_chain_classification.json`
- **THEN** `schema_version` MUST identify the contract version
- **AND** each chain MUST include its code, name, source URL, source-content digest, and nodes
- **AND** each node MUST include its code, name, optional parent code, nullable stage, and
  members
- **AND** each symbol entry MUST include its published name, listing segments, and chain/node
  memberships.

#### Scenario: Non-ASCII names are persisted

- **WHEN** a source chain, node, company, or listing segment contains Chinese text
- **THEN** the artifact MUST store readable UTF-8 text rather than `\uXXXX` escapes.

### Requirement: Observations are append-only and time-bounded

Every successful refresh MUST store identical snapshot bytes under
`data/value_chain_classification_history/YYYY-MM-DDTHHMMSSZ.json` before updating the latest
artifact. A stored classification MUST be treated as observed no earlier than its `fetched_at`
timestamp.

#### Scenario: Successful refresh

- **WHEN** a validated classification refresh completes
- **THEN** it MUST exclusively create a timestamped history snapshot
- **AND** it MUST atomically update the latest artifact with identical content
- **AND** it MUST NOT overwrite an earlier history snapshot.

#### Scenario: Historical use

- **WHEN** a caller needs classification as of a past time
- **THEN** the history directory MUST allow selecting only a snapshot whose `fetched_at` is not
  later than that time
- **AND** the system MUST NOT present today's classification as reconstructed historical data.

### Requirement: Refresh cadence is bounded

The classification refresher MUST skip network access when the latest valid artifact is less
than seven days old unless an operator explicitly forces a refresh.

#### Scenario: Latest snapshot is fresh

- **WHEN** the non-forced refresher runs less than seven days after `fetched_at`
- **THEN** it MUST leave the latest and history artifacts unchanged
- **AND** it MUST make no classification-source requests.

#### Scenario: Latest snapshot is missing, corrupt, or stale

- **WHEN** the latest artifact is absent, cannot be validated, or is at least seven days old
- **THEN** the refresher MUST attempt a complete current snapshot.

#### Scenario: Operator forces a refresh

- **WHEN** the standalone CLI runs with `--force`
- **THEN** it MUST attempt a complete snapshot regardless of latest-artifact age.

### Requirement: Classification history is included in metadata backup

The existing additive mirror MUST copy the latest and historical classification JSON, and
quarterly `data_meta` archives MUST retain their relative paths alongside other small
metadata artifacts.

#### Scenario: Build a quarterly metadata archive

- **WHEN** a verified mirror contains the latest classification and timestamped history
- **THEN** `data_meta_<YYYYQn>.tar.gz` MUST contain
  `value_chain_classification.json`
- **AND** it MUST contain each
  `value_chain_classification_history/YYYY-MM-DDTHHMMSSZ.json` path without flattening it.
