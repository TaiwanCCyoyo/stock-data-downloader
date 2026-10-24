"""
Build Price Parquet Module

Consolidates the official daily-price artifact and the Shioaji-derived
{code}_day.csv files into data/price_daily.parquet, a read-optimized cache for
downstream backtest consumers (much faster than scanning per-symbol CSVs). This
is one step of the run_daily.py one-click flow, but can also be run standalone.

The official artifact is the source of record; the Shioaji-derived files fill in
what it does not cover. That is what lets the cache reach 2010 instead of
2018-12-07, and it is why every row carries a `Source` column saying where it
came from.
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
import sqlite3
from pathlib import Path

import pandas as pd

from config import config
from shp_utils.technical_cache import build_cache

logger = logging.getLogger(__name__)
DEFAULT_DATA_PATH = config.DATA_DIR

SOURCE_OFFICIAL = "official"
SOURCE_SHIOAJI = "shioaji"

# A Taiwanese lot is 1000 shares. The official artifact publishes shares; this
# cache has always published lots and is documented that way, so the conversion
# happens here rather than at every consumer. It produces a float on purpose:
# TWSE's figures include odd-lot trading and do not divide evenly by 1000.
SHARES_PER_LOT = 1000.0

MOVING_AVERAGE_WINDOWS = (5, 10, 20, 60)

# The column set and order downstream consumers already read. `ts` is the raw
# date string the day CSVs carry and is kept for compatibility even though
# `Date` duplicates it as a timestamp.
OUTPUT_COLUMNS = [
    "ts",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    *[f"SMA{w}" for w in MOVING_AVERAGE_WINDOWS],
    *[f"EMA{w}" for w in MOVING_AVERAGE_WINDOWS],
    "Date",
    "Code",
    "Source",
]

# SQLite's parameter limit is well above this on modern builds, but a code list
# is unbounded in principle and batching costs nothing.
CODE_BATCH_SIZE = 500


def _read_day_csv(filename: str) -> pd.DataFrame | None:
    df = pd.read_csv(filename)
    df.columns = [column.strip() for column in df.columns]
    if "Date" not in df.columns and "ts" in df.columns:
        df["Date"] = pd.to_datetime(df["ts"])
    elif "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"])
    else:
        return None
    df["Code"] = os.path.basename(filename).split("_")[0]
    return df


def load_shioaji_frames(data_dir: Path) -> list[pd.DataFrame]:
    csv_files = sorted(glob.glob(os.path.join(str(data_dir), "*_day.csv")))
    logger.info("Found %s day CSV files under %s", len(csv_files), data_dir)
    frames = []
    for filename in csv_files:
        df = _read_day_csv(filename)
        if df is None:
            logger.warning("Skipping %s: no Date or ts column", filename)
            continue
        if not df.empty:
            frames.append(df)
    return frames


def load_official_daily(db_path: Path, codes: list[str]) -> pd.DataFrame:
    """Official rows for `codes`, with volume converted from shares to lots.

    Restricted to codes this pipeline already tracks. The official artifact holds
    49,733 TPEx codes, almost all of them warrants, and widening the cache's
    universe is a separate decision from changing where its values come from.

    Rows with no close are skipped. Those are days a listed symbol did not trade
    -- real days, but ones this cache has never carried, and admitting them would
    change every symbol's bar count on top of everything else this change moves.
    """
    if not db_path.is_file():
        logger.warning("Official artifact not found at %s; building from Shioaji data alone.", db_path)
        return pd.DataFrame()

    frames = []
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        for start in range(0, len(codes), CODE_BATCH_SIZE):
            batch = codes[start : start + CODE_BATCH_SIZE]
            placeholders = ",".join("?" * len(batch))
            frames.append(
                pd.read_sql(
                    f"""
                    SELECT code AS Code, date AS ts, open AS Open, high AS High,
                           low AS Low, close AS Close, volume AS shares
                    FROM official_daily_price
                    WHERE code IN ({placeholders})
                      AND close IS NOT NULL
                      AND close_missing = 0
                    """,
                    conn,
                    params=batch,
                )
            )
    if not frames:
        return pd.DataFrame()

    official = pd.concat(frames, ignore_index=True)
    official["Date"] = pd.to_datetime(official["ts"])
    official["Volume"] = official["shares"] / SHARES_PER_LOT
    return official.drop(columns=["shares"])


def attach_moving_averages(df: pd.DataFrame) -> pd.DataFrame:
    """Compute SMA/EMA per symbol over the merged series.

    They cannot be carried over from the day CSVs any more: those were computed
    over the Shioaji-only history, so every window near 2018-12-07 was either NaN
    or computed from a shorter series than the merged one has. The formulas match
    `convert_stock_price.process_min_file` exactly, including the rounding, so a
    symbol with no official coverage produces byte-identical values.
    """
    df = df.sort_values(["Code", "Date"], kind="stable").reset_index(drop=True)
    grouped = df.groupby("Code", sort=False)["Close"]
    for window in MOVING_AVERAGE_WINDOWS:
        df[f"SMA{window}"] = grouped.rolling(window=window).mean().round(2).reset_index(level=0, drop=True)
        df[f"EMA{window}"] = grouped.ewm(span=window).mean().round(2).reset_index(level=0, drop=True)
    return df


def build_price_parquet(data_dir: Path | str, db_path: Path | None = None, *, technical_features: bool = False) -> Path:
    """Consolidate prices, raising ValueError when no usable day CSVs exist."""
    data_dir = Path(data_dir)
    db_path = db_path or data_dir / "official_daily.sqlite"

    shioaji_frames = load_shioaji_frames(data_dir)
    if not shioaji_frames:
        raise ValueError(f"no usable day CSV files under {data_dir}")
    shioaji = pd.concat(shioaji_frames, ignore_index=True)
    shioaji["Source"] = SOURCE_SHIOAJI

    codes = sorted(shioaji["Code"].astype(str).unique())
    official = load_official_daily(db_path, codes)
    if not official.empty:
        official["Source"] = SOURCE_OFFICIAL

    # Official first, so drop_duplicates(keep="first") resolves every overlapping
    # (Code, Date) in its favour without a join.
    combined = pd.concat([official, shioaji], ignore_index=True) if not official.empty else shioaji
    combined["Date"] = pd.to_datetime(combined["Date"]).dt.tz_localize(None)
    before = len(combined)
    combined = combined.drop_duplicates(subset=["Code", "Date"], keep="first")
    logger.info("Merged %s rows down to %s by (Code, Date), official winning", before, len(combined))

    combined["ts"] = combined["Date"].dt.strftime("%Y-%m-%d")
    combined = attach_moving_averages(combined)
    combined = combined.loc[:, OUTPUT_COLUMNS]

    output_path = data_dir / "price_daily.parquet"
    combined.to_parquet(output_path, index=False)

    # The row and symbol totals alone cannot distinguish "today's bars are in"
    # from "today's bars were never downloaded": on 2026-08-27 and 08-28 this
    # line reported an identical 3,461,382 rows for 2,070 symbols both nights
    # while the cache stayed frozen at 08-26. The latest date, and how many
    # symbols actually carry a bar on it, separate the two -- a collapsed
    # contract list shows up as a fresh date carried by a handful of symbols.
    latest_date = combined["Date"].max()
    symbols_on_latest_date = int(combined.loc[combined["Date"] == latest_date, "Code"].nunique())
    per_source = combined["Source"].value_counts().to_dict()
    logger.info(
        "Saved %s rows for %s symbols to %s (%s..%s, latest bar carried by %s symbols; by source: %s)",
        len(combined),
        combined["Code"].nunique(),
        output_path,
        combined["Date"].min().date(),
        latest_date.date(),
        symbols_on_latest_date,
        per_source,
    )
    # Keep legacy rounded moving averages unchanged. New unrounded components
    # live in a separate, explicitly raw-price generation, never in old columns.
    if technical_features:
        technical = build_cache(combined, data_dir / "technical_features", price_basis="raw-unadjusted")
        logger.info("Technical feature generation: %s", technical.manifest_path)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-path",
        default=str(DEFAULT_DATA_PATH),
        help="Directory containing {code}_day.csv files (default: data/)",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Official daily-price sqlite (default: <data-path>/official_daily.sqlite)",
    )
    parser.add_argument("--technical-features", action="store_true", help="Also retain a versioned technical-component generation (additive disk use)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    build_price_parquet(Path(args.data_path), Path(args.db_path) if args.db_path else None, technical_features=args.technical_features)


if __name__ == "__main__":
    main()
