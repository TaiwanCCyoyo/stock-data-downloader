# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-mops-capture-continuation/proposal.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-mops-capture-continuation/proposal.md).
No production action or acquisition is authorized by this record.

## Why

The bounded MOPS capture stops at the first failed detail. When the failure is the
source stating that it no longer serves an issuer's disclosures, every later,
independent announcement in the list is left unasked and indistinguishable from a
row nobody tried. An event-targeted batch needs to record that refusal, move on,
and count what it did not ask separately from what it could not get.

## What Changes

- Add three opt-in hooks to the shared capture engine: a business-refusal
  classifier, a row selector and a stop-after-target callback. With all three
  unset, behaviour and manifests are unchanged.
- Recognise only the refusal body shape actually observed (46 of 46 refused detail
  requests on 2026-09-11): `code == 500`, `result is null`, exactly four keys, and
  a message naming the requested company. Anything else keeps stopping the capture.
- Add per-row states `unavailable`, `not_selected`, `not_attempted_issuer_refused`
  and `not_attempted_target_found`, a `row_accounting` partition, and a unit status
  `complete_with_unavailable`, only on manifests produced with a hook.
- Non-goals: retries, alternate endpoints, CLI changes, scheduling, cash modelling.

## Capabilities

### Modified Capabilities

- `mops-dividend-evidence`: optional continuation past proven business refusals.

## Impact

`shp_utils/mops_raw_evidence.py` and `shp_utils/mops_dividend_evidence.py` gain
optional keyword arguments. The material-evidence collector shares the engine and
does not pass the hooks, so its behaviour is unchanged. New offline tests only.
