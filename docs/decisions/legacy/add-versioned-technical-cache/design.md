# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/add-versioned-technical-cache/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/add-versioned-technical-cache/design.md).
No production action or acquisition is authorized by this record.

## Context

Existing daily CSV and consolidated Parquet SMA/EMA columns use unadjusted Close, two-decimal rounding and pandas adjusted-weight EMA. Replacing them would silently change historical consumers. Chart-adjusted CSVs are another basis, not a universal substitute for a caller's event-aware signal prices. Generic precomputation must not imply historical PIT membership or total-return accounting.

## Goals / Non-Goals

**Goals:** compute common components once per unchanged symbol/input/definition; expose verifiable, discoverable artifacts; retain old inputs and outputs so a fixed manifest remains usable after prices are corrected.

**Non-Goals:** no new data source, adjustment engine, inferred trading calendar, rankings, pattern thresholds, forward outcomes, strategy decisions, or automatic adoption into a historical experiment. No pruning or production writes before merge.

## Decisions

1. A separate `technical-components.v1` contract consumes Code (string), Date (timezone-naive midnight), Open/High/Low/Close, Volume (lots), Source. Validate unique keys and finite OHLC consistency. Invalid OHLC rows remain present with `price_valid=false` and unknown price features; invalid/negative volume becomes unknown only for volume features. Do not fill absent dates. Windows count observed rows; an explicitly supplied invalid row resets price warm-up. Record this gap policy prominently, not as a true exchange calendar.
2. Unrounded float64 output: SMA5/10/20/60/120; SMA-seeded EMA12/26, MACD12_26 and SMA-seeded MACDSignal9/Histogram; Return5/20/60/120 (fractions); PriorHigh20/60 and PriorLow20/60 (previous rows only); BBMean20, BBStd20 (population ddof=0), BBUpper20/Lower20 (+/-2 std); Wilder ATR14 and RSI14 (14 changes, flat=50, positive-only=100); VolumeMean5/20 and VolumeRatio5_20 (zero denominator unknown). Each contiguous valid price segment is computed independently; short segments skip components that cannot yield a value. No input-derived percent ranks or strategy labels.
3. A content-addressed object holds `input.parquet`, `features.parquet`, `object.json`. Its identity includes canonical sorted relevant input, contract, caller-declared price_basis, implementation hashes and Python/pandas/numpy/pyarrow versions. Preserve source columns including unknown OHLC/volume. A changed symbol is fully recomputed, including EMA history; unchanged symbols reuse verified objects. This avoids unsafe tail-only EMA state and historical-correction bugs. The first iteration does not promise faster calculation when every symbol receives a new bar.
4. Publish objects using unique staging directories and atomic rename, manifest last. Never overwrite existing objects or repair corruption silently. Root build lock excludes simultaneous builders; an abandoned lock is an explicit error requiring inspection, not an automatically expired lock. No caller filenames in object paths: paths derive from digest, not symbol text. Each immutable generation manifest lists exact objects, hashes, date/source coverage and definitions; its digest-derived path is returned. No mutable latest pointer that could change an experiment's inputs. Failed staging is retained as incomplete evidence, never indexed as complete.
5. `build_technical_cache.py --input ... --price-basis ... --output-dir ...` performs only local reads and additive writes. Default input is config.DATA_DIR/price_daily.parquet, raw-unadjusted basis only for that default; a custom input requires explicit basis. The integrated Parquet builder's explicit `--technical-features` option invokes the same function after its successful existing write, with raw-unadjusted basis and a sibling technical_features directory; no change to old columns, official-first precedence or conversion ordering. Daily defaults remain unchanged to avoid automatic full-history storage growth. When requested, a cache failure fails the step rather than declaring current features available.
6. Store normalized input rows with each object: original files and raw response availability are still separate provenance limitations, but feature replay no longer depends on a mutable price_daily.parquet. Manifest records caller declaration as a declaration, not verified PIT provenance. Consumers pin a generation, verify all referenced hashes and join by Code/Date. Document producer, reader, storage cost and no-clean-clone availability. Existing research results remain immutable; any adoption requires equivalence of price basis, calendar, warm-up and precision.

## Risks / Trade-offs

- Additive daily generations grow storage when all symbols change; report bytes written/reused, never claim content addressing eliminates daily growth. Retention remains an explicit future owner decision after consumer inventory.
- Raw indicators and event-adjusted indicators differ; basis identity and a strong compatibility warning prevent silent substitution.
- Missing rows cannot distinguish suspensions from acquisition gaps without a calendar; do not infer or advertise gap-safe exchange-session features.
- Hash verification and file I/O can outweigh small indicator computation; measure cold/warm synthetic runs and report actual results, not promised speedups.
- No offsite backup guarantee is added. Existing additive backup covers files when enabled, but copy location and recoverability must be reported separately.

## Migration Plan

Ship code/spec/tests via independent PR. After owner merge, recheck production Git state, schedulers, writers and backup availability; coordinate a quiet window, fast-forward main, and run the standalone builder only against a stable existing daily Parquet. Do not rerun downloads or overwrite raw/consolidated prices just to seed the cache. Verify the new manifest and record counts, hashes, cold/warm times and physical paths. Rollback means consumers retain their pinned old artifacts and stop invoking the new builder; no source restoration is needed.

## Open Questions

No blocking product decisions. Whether any particular research definition is identical remains a consumer-side verification, not an assumption of this change.
