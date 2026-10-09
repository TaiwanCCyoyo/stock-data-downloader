# Technical components: reusable local artifact

[Pipeline index](pipeline.md) · [Data safety](operations.md)

`technical-components.v1` provides common trailing price/volume calculations, not
buy/sell rules, rankings, future winners, total-return accounting or PIT membership.
The producer is `scripts/build_technical_cache.py` / `shp_utils.technical_cache`;
`scripts/build_price_parquet.py` is its integrated caller. The price-processing
module maintains the contract. No external research consumer is automatically
migrated or certified by this change.

## Build, find and read

The consolidated price build retains its previous columns and can generate
sibling technical components with `--technical-features`. It is opt-in: daily
updates do not automatically retain a full historical feature generation each day.
To generate only those components from an existing
stable cache, without downloading or replacing any prices:

```powershell
uv run scripts/build_technical_cache.py
uv run scripts/build_price_parquet.py --technical-features
uv run scripts/build_technical_cache.py --input D:/snapshots/prices.parquet --price-basis my-normalized-basis-v1 --output-dir D:/snapshots/features
uv run scripts/build_technical_cache.py --verify D:/snapshots/features/manifests/DIGEST.json
```

The log reports the exact immutable manifest path, computed/reused symbols and
bytes written. Pin that path and its digest, rather than selecting a file by
modification time. Defaults resolve through `config.DATA_DIR`, never the current
working directory. A custom input requires an explicit basis declaration;
`--price-basis` without `--input` is rejected so the built-in raw source cannot be
relabeled as adjusted.

```python
from pathlib import Path
from shp_utils.technical_cache import load_features, verify_manifest

manifest = Path("D:/snapshots/features/manifests/DIGEST.json")
metadata = verify_manifest(manifest)
features = load_features(manifest)
```

Both readers verify stored evidence. Join with prices by `(Code, Date)` and retain
unknown feature values. Check price basis, window/gap policy, warm-up and units
before reusing a feature in another experiment. A familiar name such as SMA20 does
not establish equivalence.

`verify_manifest` verifies one symbol object at a time and accumulates coverage,
without holding or concatenating the full generation. `load_features` explicitly
loads all feature rows into memory; budget for the complete table when using it.

## Input and output semantics

Input columns are `Code`, `Date`, `Open`, `High`, `Low`, `Close`, `Volume`, `Source`.
Code and source are nonempty strings; dates are timezone-naive local calendar
midnights with one unique row per code/date. Prices are TWD for the built-in raw
basis, and volume is lots. Custom basis labels are caller declarations, not proof
that event adjustments or historical availability are correct.

Output carries Code/Date, `price_valid` and the components below. Calculations use
unrounded float64. Invalid OHLC rows remain in the output with unknown price
components and reset subsequent price warm-up. Nonfinite/negative volume is
unknown only for volume calculations. Zero volume is a real value; a zero ratio
denominator is unknown. Numeric NaN is not a negative signal or a losing outcome.

| Columns                         | Definition / first possible value in a valid segment                                   |
| ------------------------------- | -------------------------------------------------------------------------------------- |
| SMA5/10/20/60/120               | Full-window arithmetic mean of Close, at row N                                         |
| EMA12/26                        | Initial N-row Close mean; then alpha `2/(N+1)`                                         |
| MACD12_26                       | EMA12 minus EMA26, at row 26                                                           |
| MACDSignal9, MACDHistogram      | Nine-MACD-value SMA-seeded EMA; histogram is MACD minus signal, at row 34              |
| Return5/20/60/120               | Close / Close N rows ago minus 1, at row N+1; fractions                                |
| PriorHigh20/60, PriorLow20/60   | Previous N rows' High maximum / Low minimum, excluding current row                     |
| BBMean20, BBStd20               | 20-row Close mean and population standard deviation (`ddof=0`)                         |
| BBUpper20, BBLower20            | Mean plus/minus twice standard deviation                                               |
| ATR14                           | Wilder average, initial 14 true-range mean then alpha `1/14`; first row TR is High-Low |
| RSI14                           | Wilder-smoothed 14 Close changes; at row 15; flat=50, gains-only=100, losses-only=0    |
| VolumeMean5/20, VolumeRatio5_20 | Full-window volume means and their ratio; missing volume invalidates a window          |

Windows count supplied observed rows, **not guaranteed consecutive exchange
sessions**. The builder does not infer whether an absent date is a holiday,
suspension or acquisition gap. A consumer requiring an explicit calendar must
prepare that input, including invalid rows for known gaps, before building.
Changing those rows changes the cache identity.

The default basis is `raw-unadjusted`: there is no dividend, split or rights
adjustment here. Legacy `price_daily.parquet` SMA/EMA values remain rounded to two
decimals; its EMA uses pandas adjusted weighting from the first observation. These
legacy values and the new components deliberately have different definitions.
Existing chart-adjusted CSVs also do not establish a historical PIT price basis.

## Identity, coverage and preservation

```text
data/technical_features/
  objects/DIGEST/input.parquet
  objects/DIGEST/features.parquet
  objects/DIGEST/object.json
  manifests/DIGEST.json
```

Object identity covers normalized relevant input rows, source labels, declared
basis, contract, implementation hashes and Python/pandas/numpy/pyarrow versions.
Manifests record coverage, sources, definitions, columns and exact object hashes.
Dates and symbols come from the input; no hidden universe/date filter is applied.
Observed source labels are not source-release timestamps or a completeness audit.

A historical correction yields a new object for the affected symbol. Unchanged
symbols can be shared by generations. Every retained object includes the normalized
input actually used, so exact feature replay need not read a subsequently changed
price cache. This is not a copy of upstream raw HTTP responses, credentials,
corporate-action evidence or the complete acquisition history; retain those using
their existing contracts. Code and specs remain tracked in the producing Git
commit, while generated data is ignored. **A fresh clone does not contain it.**

Read a saved generation using its manifest. For recovery, copy the selected
manifest and every referenced object, retaining directory layout, then run
`--verify`. Copying only the features or only a rebuild command is insufficient.
Record actual backup location/verification in the producer's operational log;
neither a second local copy nor Git alone is an offsite backup.

No automatic pruning, replacement of old generations, or overwrite-based
corruption repair is implemented. Before any later cleanup, inventory manifests
pinned by consumers and preserve their inputs and code identities. A corrupt
object fails explicitly, including an `object.json` reserialized away from its
canonical producer bytes; a rebuild cannot bless that change as reuse. Incomplete
staging is not a completed generation. An
existing writer lock requires inspection of the owning process before any manual
recovery; the builder never expires another writer's lock by age.

## Performance and cost boundary

Reuse avoids indicator calculation for unchanged symbols, not reading and hashing
their input or verifying saved artifacts. A changed symbol is fully recomputed;
this safely handles old-price corrections and recursive EMA state. The initial
version does not optimize a one-row append using hidden rolling state.

New observations for most symbols produce new full-symbol objects. Additive
retention therefore grows disk use; content addressing is not daily compression.
Use the reported bytes and measured cold/warm times to judge value. Retention or
incremental-state optimization is a separate change, not permission to delete
old research evidence. No guaranteed speedup is claimed before measurement.

## Production rollout

Merge the reviewed PR first. Coordinate with download/backfill owners and inspect
actual schedulers and writers. Preserve their enabled states and existing backups;
do not start/stop unrelated processes or reactivate paused schedules. Update only
clean production code via fast-forward. Against a stable existing Parquet, run the
standalone builder and verify its returned manifest, counts, input hashes and
storage cost. Do not rebuild raw prices or download merely to seed this artifact.
