# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-dated-tpex-roster-evidence/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-dated-tpex-roster-evidence/design.md).
No production action or acquisition is authorized by this record.

## Context

Saved endpoint responses and its official type selector were inspected before parsing.
The response contains a Gregorian date, a ROC table date, category, fields and data.

## Goals / Non-Goals

Validate dates, named fields, counts and code uniqueness. Preserve exact printed values
and raw hash. Do not classify ordinary shares, infer public-availability timestamps,
fill monthly gaps or change price caches.

## Decisions

Use an offline pure parser in shp_utils; explicit batch orchestration owns HTTP budgets
and output paths. Unlike positional column-count detection, normalized field names
identify the required code/name columns. Empty data stays observed_empty, never proof
of historical absence. Category labels are retained rather than treated as stable taxonomy.

## Risks / Trade-offs

Retrospective endpoint labels can be back-applied; emit unknown PIT status. Monthly
observations cannot prove daily membership. Network failure leaves durable raw/attempt
records and does not settle missing evidence.
