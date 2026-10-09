# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/archive/2026-08-24-add-value-chain-classification-snapshots/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/archive/2026-08-24-add-value-chain-classification-snapshots/design.md).
No production action or acquisition is authorized by this record.

## Context

`symbol_meta.sqlite` stores official exchange-level industry categories, but those categories
are intentionally broad. The Taipei Exchange Industry Value Chain Information Platform
publishes a finer hierarchy: each value chain has stable codes, upstream/midstream/downstream
nodes, and domestic company links carrying security codes. The pages are current-state HTML,
not a historical API, so storing the first observed state is necessary before the data can be
used without look-ahead in future research.

The acquisition belongs in this standalone market-data repository. The existing daily job is
already the unattended entry point and already treats independent metadata failures as
non-cache failures.

## Goals / Non-Goals

**Goals:**

- Capture the complete official value-chain taxonomy and domestic security membership.
- Produce a stable, versioned JSON contract with both chain-to-symbol and symbol-to-chain
  access paths.
- Preserve append-only observations and refresh no more often than once every seven days by
  default.
- Keep a failed or malformed fetch from replacing the last known-good artifact.
- Make the behavior testable from local HTML fixtures without requiring network access.

**Non-Goals:**

- Curate informal market concepts or ask an AI model to classify issuers.
- Assert that the current taxonomy existed before the first locally stored observation.
- Backfill historical taxonomy, modify price data, or place broker orders.
- Store foreign-company links as Taiwan securities.

## Decisions

### Use the official TPEx value-chain taxonomy as the acquisition source

The fetcher discovers chain codes from the platform's own selector, then requests each
`introduce.php?ic=<code>` page. It records each page URL and SHA-256 digest and accepts only
domestic company links whose relative URL contains `company_basic.php?stk_code=<code>`.
Listing-segment labels are retained exactly as published.

This is preferred over AI or news-site classification because the source is authoritative,
structured, repeatable, and provides finer nodes such as memory IC and glass-fiber cloth.
Informal concepts such as "old AI" remain queries over official server/AI nodes or a separate
consumer-owned interpretation, not canonical acquisition data.

### Parse with the standard library and validate structural invariants

A small `html.parser.HTMLParser` implementation extracts the selector, chain title, stage
nodes, and company lists. No new dependency is needed. A refresh is valid only when it yields
at least one chain, node, and domestic symbol; duplicate chain/node identifiers or conflicting
names are errors. Fixture tests pin the relevant HTML structure.

Adding Beautiful Soup was considered, but the page structure needed here is narrow and the
project does not otherwise require it. A regular-expression-only parser was rejected because
HTML nesting and entity decoding are easier to validate with an HTML parser.

### Write one latest artifact and immutable timestamped history

The version-1 JSON object contains `schema_version`, `source`, `fetched_at`, `snapshot_id`,
`chains`, and `symbols`. Each chain contains its code, name, source URL/digest, ordered nodes,
and each node's code, name, optional parent code, nullable published stage, and ordered
members. Parent codes preserve official sub-classifications such as the memory-IC children
under IC design. Some non-linear official diagrams have no upstream/midstream/downstream
label, so `stage` is `null` rather than inferred. `symbols` is an inverse map keyed by
security code with the published name, listing segments, and chain/node memberships.

Each successful refresh first writes
`data/value_chain_classification_history/YYYY-MM-DDTHHMMSSZ.json` with exclusive creation,
then atomically replaces `data/value_chain_classification.json` with identical bytes. A
timestamped filename avoids overwriting a same-day forced refresh. Stable ordering and UTF-8
JSON make diffs and checksums deterministic.

Storing only a latest file was rejected because it would make past research irreproducible.
A SQLite history table was considered but rejected for this small, naturally hierarchical,
append-only artifact.

### Refresh opportunistically from the daily job on a seven-day freshness interval

The standalone CLI supports `--force`, while the daily orchestration calls the refresh helper
without forcing. A valid latest artifact younger than seven days causes a no-network skip. A
missing, corrupt, or stale latest artifact triggers a refresh.

Classification refresh is an independent metadata step. Failure is logged and contributes to
the daily job's non-zero result, but it does not stop later steps and does not block the data
backup, because it cannot corrupt price-cache files.

## Risks / Trade-offs

- [Risk] TPEx changes its HTML structure or blocks automated requests. -> Validate invariants,
  use bounded retries/timeouts and an explicit user agent, keep the last good snapshot, and
  fail visibly.
- [Risk] A current snapshot is used to label an earlier backtest. -> Preserve `fetched_at` and
  immutable history and document that a snapshot is valid only from its observation time
  forward.
- [Risk] Weekly snapshots miss a classification that appears and disappears between runs. ->
  The cadence is an intentional load/reproducibility trade-off; operators may force a capture
  when needed.
- [Risk] The official taxonomy is still broader than an informal market narrative. -> Store
  the source hierarchy faithfully and keep consumer interpretation separate.
- [Risk] A process stops between history and latest writes. -> History is authoritative and
  additive; the next run can safely recreate latest from a new validated fetch.

## Migration Plan

1. Add the fetcher, parser, artifact writer, and fixture-based tests.
2. Add the weekly-gated step to `run_daily.py` and update its tests.
3. Run a forced live fetch and inspect coverage for representative value-chain nodes.
4. Commit and merge the isolated branch. Existing installations create the new artifacts on
   the next eligible daily run; no existing data migration is required.

Rollback removes the orchestration step and fetcher. Existing snapshots are additive and may
remain readable; rollback does not delete generated data.

## Open Questions

None. Informal aliases and foreign-market lead data are explicitly separate future work.
