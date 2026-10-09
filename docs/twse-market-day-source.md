# TWSE dated whole-market source method

Use the dated `MI_INDEX` endpoint with `type=ALLBUT0999` and `response=json`.
Validate the requested date, response status and named field signature before
normalizing nonempty rows. Preserve raw field provenance and actual fetch time.

Parse shares, NTD value and transaction counts independently. A zero-trade or
missing-close row retains NULL close and an explicit missing flag rather than a
literal zero price. A valid empty response differs from malformed/non-JSON error
content, which must remain failed/retryable.

Independent calendar evidence can establish date coverage, including historical
Saturday sessions; it does not prove per-security completeness or publication
availability. Do not silently splice venue transfers or reused codes.

The legacy report's captured price examples remain in its private original commit.
Their hashes are indexed in [migration provenance](migration/source-files.csv).
The parser tests in this candidate use invented values, names and test identifiers,
not retained observed market rows. See [official source contract](contracts/official-price-source/spec.md).
