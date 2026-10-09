# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-versioned-technical-cache/verification.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-versioned-technical-cache/verification.md).
No production action or acquisition is authorized by this record.

# Verification and rollout evidence

## Local synthetic validation — 2026-10-04

Focused command:

```powershell
uv run python -m pytest tests/test_technical_components.py tests/test_technical_cache.py tests/test_build_technical_cache.py tests/test_build_price_parquet.py tests/test_run_daily.py -q
```

Result: 121 passed, 1 skipped in 7.94 seconds. The skip is Windows symlink-creation
permission; ordinary path traversal and malformed evidence tests passed. One
existing Shioaji/Pydantic deprecation warning remains. `openspec validate
add-versioned-technical-cache --strict` passed. Normal commit hooks provide
formatting, typing and Ruff evidence; their final state belongs to the commit/PR.

A 50-symbol x 1,000-row synthetic probe manually inspected retained input columns,
all 27 component columns, Code/Date alignment, warm-up nulls and a known constant
true range (ATR14=2). Cold build took 6.277 seconds and wrote 13,827,399 bytes;
unchanged-input reuse took 3.078 seconds, computed zero symbols, reused all 50,
wrote zero bytes and returned the same manifest. Complete verification passed.
This measures synthetic file/compute cost only, not real-market research speed.
The probe was before final formatting; numerical definitions are unchanged.

The probe artifacts are local ignored evidence under this worktree's
`.tmp/technical-probe-9c3270f0144c4465a3f7f6f1c53d1de1/`. They contain synthetic
data only. The immutable manifest identifies its exact pre-format implementation.
Readers can still verify it after producer code formatting changes; a new build
correctly receives a different implementation identity.

## Scope adjustment before delivery

Full-symbol immutable snapshots preserve reproducibility but grow when new daily
rows arrive. Therefore integrated generation is explicitly opt-in via
`build_price_parquet.py --technical-features`, not silently added to daily defaults.
The standalone builder can seed a reusable snapshot without re-downloading or
rebuilding price data. This adjustment changes storage behavior only, not formulas.

## PR #5 review corrections — 2026-10-04

The initial review identified excessive verification memory retention, reuse of
reserialized object metadata, and a CLI basis override for the implicit raw input.
The verifier now checks one object at a time and accumulates coverage; only the
explicit full-table loader collects feature frames. Weak-reference regressions
prove previous frames are released during both standalone verification and the
builder's final verification, while concatenation is forbidden in that path.

Reuse rejects noncanonical object metadata without replacing it or publishing a
new manifest, including valid JSON with whitespace, indentation or reordered keys.
CLI validation rejects basis overrides without an explicit input before any input
read, output write or verification; explicit custom inputs still require a basis.

The focused command above passed **133 tests, with 1 skipped**, in 10.27 seconds.
The skip and existing deprecation warning are unchanged. These regressions address
the review findings; they do not constitute a full-market capacity benchmark.
No GitHub Actions workflows were present at the review check, so local test and
hook results must not be described as passing hosted CI. Current-head review and
production rollout remain separate pending delivery steps.

The previously retained 50,000-row synthetic generation also passed the revised
CLI verifier without changing its saved artifacts or requiring current-producer
implementation hashes.

## Production status

Not deployed or generated on production before PR merge. Another data-acquisition
session owns active backfill work and does not modify the cache-builder files.
Post-merge generation requires renewed writer/scheduler/backup checks and a quiet
window. No main update, schedule activation, source deletion, price replacement,
raw-cache schema migration or research-result rerun was performed by this change.
