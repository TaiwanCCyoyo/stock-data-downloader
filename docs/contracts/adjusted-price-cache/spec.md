# Adjusted Price Cache

## Purpose

Define the baseline behavior for building a chart-scale, corporate-action-adjusted daily price cache alongside the raw (unadjusted) daily prices.

## Requirements

### Requirement: Raw prices remain unchanged

The system MUST NOT modify `{code}_day.csv` raw daily price files when building adjusted prices; adjustment output MUST be written to a separate location.

#### Scenario: Build adjusted prices for a symbol

- **WHEN** `build_adjusted_prices.py` builds adjusted prices for a code
- **THEN** it MUST read the corresponding `{code}_day.csv` as input
- **AND** it MUST write output to `data/adjusted_prices/daily/{code}_day.csv`
- **AND** it MUST leave the source `{code}_day.csv` untouched.

### Requirement: Chart-scale adjustment factor applied cumulatively

The system MUST apply corporate-action price factors cumulatively to all rows before each event's ex-date, using only events classified as chart-scale-relevant (dividend, ex-right, ETF split/reverse-split, capital reduction).

#### Scenario: Apply a single event

- **WHEN** a symbol has one qualifying corporate-action event with a known price factor
- **THEN** every row dated before that event's ex-date MUST have its Open/High/Low/Close multiplied by that factor
- **AND** rows on or after the ex-date MUST remain at the unadjusted scale for that event.

### Requirement: Adjusted output preserves raw values

The adjusted output MUST retain the original unadjusted OHLC values alongside the adjusted ones, so downstream consumers can distinguish chart-scale prices from accounting-scale prices.

#### Scenario: Inspect adjusted output columns

- **WHEN** an adjusted `{code}_day.csv` is produced
- **THEN** it MUST include `RawOpen`, `RawHigh`, `RawLow`, `RawClose` columns holding the pre-adjustment values
- **AND** it MUST include an `Adj_Factor` column and a count/list of corporate-action events applied per row's date.
