# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-mops-dividend-evidence/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-mops-dividend-evidence/design.md).
No production action or acquisition is authorized by this record.

## Context

Live MOPS samples for 6509/113 and 5511/105 returned list and detail JSON. Each
selected list row has a displayed announcement date different from its supplied
detail key. The revision-date column is `無` even though payment dates occur in
the detailed announcement text. Existing GET retry helpers do not implement this
POST transport or preserve failed response bodies.

## Goals / Non-Goals

Goals: archive bounded official evidence with reproducible requests and honest
completion states; retain revisions and raw date meanings for later normalization.
Non-goals: generic HTML/LLM parsing, normalized cash events, automatic whole-universe
discovery, schedules, credentials, changing existing caches or claiming PIT coverage.

## Decisions

- Require one numeric company ID and ROC year, with 1..50 detail requests (default
  10). One list request, no retries; stop on first failure. A truncated list is
  partial, never complete. Retain list order, including duplicate/revised rows.
- POST only to fixed HTTPS MOPS list/detail routes. Disable redirects, enforce
  final URL equality, retain normal TLS validation and the existing truststore.
  Validate every selected detail link before requesting it: exact route and field
  set, same company, valid compact ROC key, positive integer sequence, empty ETN
  and ordinary-announcement kind A. Unknown kinds remain failed/unsupported evidence.
- Archive raw bounded bodies before JSON/schema validation. Record status, body
  hash, request JSON, UTC start/end and outcome in `manifest.json` schema
  `mops-dividend-evidence.v1`. Oversize/network failures are errors, not complete
  bodies; do not label a truncated body as raw full evidence. Limit each response
  to 2 MiB and each HTTP connect/read wait to 20 seconds.
- Validate title/data alignment by title, not column count, and detail company
  identity. Preserve list row text and all detail bytes. Do not fabricate a
  normalized payment date or public-availability timestamp. Only this bounded
  list capture can be complete; history/revision/PIT completeness stay unverified.
- Create a fresh run directory exclusively, with locally generated filenames.
  Default root derives from config.DATA_DIR; explicit output directory enables
  isolated probes. Existing targets are refused before network access. Write raw
  files and final manifest with exclusive create, never overwrite an earlier run.
- CLI logs only bounded status messages; no raw responses/headers/credentials.
  `complete` and `source_empty` exit 0, `partial`/`error` exit nonzero. Empty means
  the observed response had no announcements, not that history has none.

## Risks / Trade-offs

- Current-source revisions can differ from historical knowledge → preserve fields
  and explicitly mark PIT/revision completeness unverified.
- Detail response supports other securities → reject unobserved kinds rather
  than substituting ordinary shares; store the list for later investigation.
- Process termination can precede final manifest → retain raw files; absent final
  manifest means incomplete. No destructive cleanup or automatic resume.
- Writing into scheduled data area can conflict → operator must check existing
  schedule rules before live default-output use. Development probes use fresh
  external output directories and no daily integration is installed.
- Two TPEx samples do not cover TWSE/revised-payment cases → preserve raw schema;
  new normalizer/coverage claims require additional measured samples.
