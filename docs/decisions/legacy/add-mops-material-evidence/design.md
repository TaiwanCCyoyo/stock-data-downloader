# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-mops-material-evidence/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-mops-material-evidence/design.md).
No production action or acquisition is authorized by this record.

## Context

The observed official frontend names `t05st01` for historical material disclosures. A 6465/115/7 response has nine announcements; its first detail states both an original payment date and a regional typhoon exception. Detail subjects contain trailing padding. Lookup `enterDate` can differ from displayed publication date. Only `marketKind=otc` has been observed for this source.

## Goals / Non-Goals

**Goals:** reproducible raw capture, identity checking, truthful partial/error status, no duplicate capture engine.

**Non-Goals:** automatic dividend interpretation, cash event creation, TWSE material-source certification, historical completeness, scheduling, generalized crawling or changes to existing caches.

## Decisions

- Extract a small source-neutral capture helper from the dividend implementation; source-specific wrappers retain fixed route whitelists, parsing and default paths. Preserve existing dividend public API, schema, error semantics and tested injection seams. Prefer explicit callbacks/configuration over duplicated network/archive loops; do not add a plugin registry or dependencies.
- Require company ID (4–6 ASCII digits), ROC year (1–999), month (1–12), maximum details (1–50, default 10). List POST has companyId/year/month/firstDay/lastDay; no dataType. Capture the first N rows in returned order without keyword filtering. The budget is one list plus N details, with no retry and stop on first error.
- Map list titles by name: 公司代號/公司名稱/發言日期/發言時間/主旨/詳細資料. Preserve the full raw row and mapped displayed date/time/subject; validate row company. Follow only apiName=t05st01_detail and exact parameters serialNumber/enterDate/marketKind/companyId. Require positive ASCII-digit string serial, valid seven-digit ROC date key, matching company, observed marketKind=otc. Unsupported markets fail explicitly, not silently skip.
- Detail must have one aligned row with 序號/發言日期/發言時間/主旨/說明. Match string serial, displayed date/time and subject after trailing whitespace removal only. Preserve original text and the entire 說明 in the saved response; include explanation_raw in the announcement after validation. Never equate enterDate with publication time or parse payment dates into cash events.
- Keep the existing 2 MiB response cap, fixed HTTPS destinations, no redirects, (20,20) connect/read-idle timeouts, exclusive writes and fresh output directory requirement. Save complete error bodies before interpretation; filesystem failures raise without a misleading completed manifest. Injected transport is explicitly labelled, not live evidence.

## Risks / Trade-offs

- Raw conditional prose is not a payment instruction → no normalized cash field; users must interpret separate evidence later.
- Shared-helper refactor can alter dividend behavior → retain and rerun existing dividend/CLI tests plus data-path tests.
- Historical source may omit/revise rows → manifest keeps coverage, historical public availability and revision completeness unverified even when all returned rows were captured.
- New/unsupported schema or market → preserve available raw evidence and stop; do not guess field mappings or route values.
- Request timeouts are idle limits, not a total wall-clock guarantee → document the distinction.

## Migration Plan

Additive opt-in CLI only; no run_daily integration or data migration. Existing dividend artifacts remain readable. Verify an offline replay of the recorded real pair against its byte hashes and condition text; label replay distinctly. Submodule commit and parent gitlink update remain pending explicit owner agreement.

## Open Questions

TWSE material-detail market value, other historical schemas, complete revision chains and actual investor bank credit remain unverified; none is inferred from this bounded implementation.
