# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-mops-rights-evidence/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-mops-rights-evidence/design.md).
No production action or acquisition is authorized by this record.

## Context

The validated dividend projector already binds raw bytes, issuer, announcement, and ordinary-share cash/ex-date blocks. Held rights lines explicitly give earnings and reserve free shares per thousand, then paid subscription price, share class and quantity. Some preferred-share notices contain blank ordinary-share templates.

## Goals / Non-Goals

**Goals:** Reuse validated context and expose exact rights literals without reinterpreting cash or selecting a revision.

**Non-Goals:** Delivery dates, cash settlement, subscription execution, downloads, daily automation, original-publication certification or forced reconciliation of competing notices.

## Decisions

- New `project_rights_statement` calls the unchanged dividend `project_statement` on original bytes. Output retains its source context and adds `rights_fields`, using a distinct schema version.
- Parse only one explicitly labeled `※除權--普通股：` line in the named rights-distribution block. Missing, duplicate, blank and unsupported formats remain explicit unknowns. Never use the preferred-share line or headline as a fallback.
- Preserve separately the stock-dividend money literal (not a share ratio), earnings free shares, reserve free shares, subscription price, subscription shares and subscription class. Combine two stated free-share quantities with exact rational arithmetic, divided by 1000; never divide the money amount by an assumed par value.
- Match the observed complete line shape with unit labels and anchored boundaries. Keep unsupported raw lines so a future extension can be evidence-driven. Reject silent partial parsing.
- Source-inspection amendment: the 31 initially unsupported held 2019 statements append the same exact empty preferred-share template. Recognize only this literal suffix before the anchored ordinary-line match; retain the entire raw line and keep blank ordinary fields unresolved. Nonempty, repeated and arbitrary suffixes remain unsupported. Preserve initial output and produce a new revision.
- No automatic version resolution or historical-time inference. Paid subscription share class is retained; an ordinary shareholder subscribing to preferred shares is not an ordinary free-share delivery.

## Risks / Trade-offs

- Blank templates could look like zero → emit null with blank/missing/unsupported/ambiguous state.
- Different revisions can disagree → retain all source-qualified alternatives and report differences without a selected value.
- Historical issuer text can change → raw hash, announcement identity and unknown revision completeness remain attached.
