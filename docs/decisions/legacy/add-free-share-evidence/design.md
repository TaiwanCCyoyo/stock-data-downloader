# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-free-share-evidence/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-free-share-evidence/design.md).
No production action or acquisition is authorized by this record.

## Context

TPEx annual exdailyq has an explicit free-shares-per-thousand column. TWSE annual prices do not provide that ratio; TWT49U detail has a separately named free-share column. Both sources also describe paid subscriptions, which are distinct rights.

## Goals / Non-Goals

**Goals:** Exact ratios, source identity and truthful unknown states from already-held response bytes.

**Non-Goals:** Downloads, new daily steps, cash subscription modeling, inferred delivery, fractional settlement prices, historical availability certification or changes to persisted market data.

## Decisions

- Use named columns, not column count. TPEx reuses the existing validated annual-cash reader for event identity and row schema; independently require its explicit free-share column.
- TWSE requires one detail row and matching security code, plus caller-supplied effective date and annual-row provenance. The response does not itself certify the effective date: retain that limitation explicitly.
- Parse source-specific units (TPEx numeric cell; TWSE `股` suffix), Decimal to Fraction divided by 1000. No binary floats, context-limited division or price-factor inference.
- Return JSON-safe integer numerator/denominator, literal, status, body hash, row locator and null timing fields. Missing and invalid cells have null ratios; explicit zero has 0/1.
- Unknown schemas, duplicate fields, malformed identities and duplicate events raise rather than producing partial records. Do not infer an empty response's economic coverage.

## Risks / Trade-offs

- Source detail contains no effective date → caller must validate annual-row and request binding before use; output records this as caller-bound, not response-observed.
- Ratio evidence alone cannot establish executable shares → never label records executable; delivery and historical availability remain null.
- Paid-only rights remain economically relevant → retain other literal fields; zero free shares does not imply zero total corporate-action effect.
