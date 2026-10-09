# Annual cash evidence

`extract_annual_cash(body, source)` is a pure in-memory API for held annual JSON bytes from `twse/twt49u` or `tpex/exdailyq`.

It returns `annual-cash-evidence.v1` with raw SHA256 and literal per-row evidence. It performs no network or filesystem work and makes no PIT, payment, entitlement, eligibility, or net-credit claim.

TWSE `權值+息值` is cash evidence only for kind `息`; `權` is `cash_not_indicated` and `權息` is `requires_detail`. TPEx uses explicit `現金股利` for all supported kinds. Missing and invalid numeric text stay distinct null statuses; zero and decimal precision are preserved. Empty valid responses are `unobserved_empty`, not event-free coverage.
