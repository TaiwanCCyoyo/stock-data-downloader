# Free-share evidence

`shp_utils.free_share_evidence` is a pure, offline reader for already-held
corporate-action response bytes. It does not download data, infer entitlement
delivery, certify historical availability, or model paid subscriptions.

`extract_tpex_free_shares(body)` validates the TPEx annual response through
the annual-cash evidence reader and reads the named `每仟股無償配股` literal.
`extract_twse_free_shares(...)` validates one TWT49U detail row and requires
caller-bound annual-row identity because the detail does not establish its own
effective date.

Both functions return `free-share-evidence.v1`. A stated per-thousand literal
is converted exactly with `Fraction(Decimal(literal)) / 1000` and emitted as
integer numerator and denominator. Zero, missing, and invalid literals remain
distinct. `delivery_at`, `fractional_settlement_at`, and
`historical_available_at` are always null.

The preserved `literal_fields` include paid-subscription columns, but those
values are not interpreted as free shares or financial policy.
