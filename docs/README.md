# Documentation Index

[Project quick start](../README.md) · [繁體中文](zh-TW/README.md)

## Operating guides

| Guide                                         | Responsibility                                                                 |
| --------------------------------------------- | ------------------------------------------------------------------------------ |
| [Git workflow](en/git-workflow.md)            | Workspace isolation, branch/PR delivery and review boundaries                  |
| [Pipeline and artifacts](pipeline.md)         | Daily order, standalone CLIs, output contracts and legacy compatibility        |
| [Operations and data safety](operations.md)   | Scheduler entry points, production/worktree boundaries and backup limitations  |
| [Data progress](data-progress.md)             | Read-only coverage and checkpoint inspection, including older snapshots        |
| [Technical components](technical-features.md) | Versioned trailing features, input basis, reuse and retained-generation access |

## Behavior contracts

Current contracts live in `docs/contracts/`. Use ordinary design notes for
substantive changes. Records under `docs/decisions/legacy/` preserve past proposals
and decisions; their implementation status is unverified, and they are not current contracts.

| Capability                                | Baseline                                                                     |
| ----------------------------------------- | ---------------------------------------------------------------------------- |
| Price acquisition, conversion and parquet | [price-data-pipeline](contracts/price-data-pipeline/spec.md)                 |
| Official daily and historical prices      | [official-price-source](contracts/official-price-source/spec.md)             |
| Daily orchestration                       | [daily-update-orchestration](contracts/daily-update-orchestration/spec.md)   |
| TWSE/TPEx corporate actions               | [corporate-actions-index](contracts/corporate-actions-index/spec.md)         |
| Adjusted daily prices                     | [adjusted-price-cache](contracts/adjusted-price-cache/spec.md)               |
| Symbol metadata                           | [symbol-metadata](contracts/symbol-metadata/spec.md)                         |
| Monthly revenue                           | [monthly-revenue-pipeline](contracts/monthly-revenue-pipeline/spec.md)       |
| Institutional flows                       | [institutional-data-pipeline](contracts/institutional-data-pipeline/spec.md) |
| Value-chain observations                  | [value-chain-classification](contracts/value-chain-classification/spec.md)   |
| Integrity report                          | [data-quality-report](contracts/data-quality-report/spec.md)                 |
| Local artifact status                     | [data-status](contracts/data-status.md)                                      |
| Additive backup                           | [data-backup](contracts/data-backup/spec.md)                                 |

## Evidence and source guides

These guides retain their existing paths and source-specific details. Related
Historical records remain under `docs/decisions/legacy/`; their presence does not
authorize a new capture or production refresh.

| Subject                                | Guide                                                           |
| -------------------------------------- | --------------------------------------------------------------- |
| Institutional reports and availability | [institutional-sources](institutional-sources.md)               |
| TPEx history and roster definitions    | [tpex-history-sources](tpex-history-sources.md)                 |
| TWSE dated whole-market quotes         | [twse-market-day-source](twse-market-day-source.md)             |
| 2026-10-04 official data repair        | [data-gap-repair-20261004](data-gap-repair-20261004.md)         |
| Explicit official price exports        | [official-price-slices](official-price-slices.md)               |
| Corporate-detail selection and capture | [corporate-detail-capture](corporate-detail-capture.md)         |
| MOPS dividend raw evidence             | [mops-dividend-evidence](mops-dividend-evidence.md)             |
| Dividend statement normalization       | [mops-dividend-statements](mops-dividend-statements.md)         |
| MOPS material announcements            | [mops-material-evidence](mops-material-evidence.md)             |
| Rights subscriptions                   | [mops-rights-evidence](mops-rights-evidence.md)                 |
| Annual cash distributions              | [annual-cash-evidence](annual-cash-evidence.md)                 |
| Free-share observations                | [free-share-evidence](free-share-evidence.md)                   |
| Par-value observations                 | [par-value-evidence](par-value-evidence.md)                     |
| Supplement batches → terms, halts      | [corporate-action-supplements](corporate-action-supplements.md) |
