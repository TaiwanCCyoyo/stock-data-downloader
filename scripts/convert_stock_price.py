"""
Convert Stock Price Module

Converts downloaded minute-bar data (`_min.csv`) into daily-bar data (`_day.csv`).
Supports multiprocessing to speed up the conversion.
"""

import argparse
import logging
import os
import re
from concurrent.futures import ProcessPoolExecutor
from typing import cast

import pandas as pd

from config import config
from shp_utils.logging_utils import setup_logging

logger = logging.getLogger(__name__)

# pandas' weekday numbering: Monday is 0, Sunday is 6. Named because only Sunday is
# excluded and the reason Saturday is not is easy to get wrong -- see `process_min_file`.
SUNDAY = 6


def process_min_file(min_file: str, data_dir: str) -> str:
    pass
    """
    Generate the daily-bar data corresponding to a single `_min.csv` file.

    Args:
        min_file(str): Input `_min.csv` file name.
        data_dir(str): Folder containing the minute-bar data files.

    Returns:
        None
    """
    day_file = re.sub(r"_min\.csv$", r"_day.csv", min_file)
    result_msg = f"Converted {min_file} to {day_file}"

    # Read minute-bar data
    min_data = pd.read_csv(os.path.join(data_dir, min_file))
    min_data.ts = pd.to_datetime(min_data.ts)
    min_data.set_index("ts", inplace=True)

    # Drop invalid rows (all values zero)
    min_data = min_data.loc[(min_data != 0).all(axis=1)]

    # Drop Sunday bars. Shioaji's kbar service returns a handful of them on days the
    # market never opened -- verified 2026-08-29 by re-querying the live API, which
    # still returns byte-identical bars for 2024-12-01 (255 symbols, 1-2 bars each at
    # 09:01, median 2 lots, priced within the daily limit of the previous real close)
    # and 2021-10-31 (18 symbols). Their shape is a market-wide system test, not
    # trading. Filtering here rather than purging the output once is what makes it
    # stick: the bars are upstream and still live, so any re-download brings them back.
    #
    # `_min.csv` is deliberately left raw -- this drops them on the way into the daily
    # bars, so the original response stays inspectable.
    #
    # Sunday only, never Saturday. Taiwan holds make-up trading sessions (補行交易日) on
    # Saturdays; the official artifact records 15 real ones, and the single Saturday in
    # this cache (2018-12-22) is one of them. `check_data_integrity._derive_trading_calendar`
    # draws the same line for the same reason.
    time_index = cast(pd.DatetimeIndex, min_data.index)
    sunday_mask = time_index.to_series().dt.dayofweek.to_numpy() == SUNDAY
    if sunday_mask.any():
        dropped_days = sorted({ts.strftime("%Y-%m-%d") for ts in time_index[sunday_mask]})
        result_msg += f" [Warning] dropped {int(sunday_mask.sum())} Sunday bar(s) on {', '.join(dropped_days)} (market closed; upstream artifact)"
        min_data = min_data.loc[~sunday_mask]

    # OHLC sanity check
    # Conditions:
    # 1. High must be >= Open, Close, Low
    # 2. Low must be <= Open, Close, High
    # 3. Volume must be >= 0
    invalid_mask = (
        (min_data["High"] < min_data["Open"])
        | (min_data["High"] < min_data["Close"])
        | (min_data["High"] < min_data["Low"])
        | (min_data["Low"] > min_data["Open"])
        | (min_data["Low"] > min_data["Close"])
        | (min_data["Low"] > min_data["High"])
        | (min_data["Volume"] < 0)
    )

    if invalid_mask.any():
        bad_count = invalid_mask.sum()
        # Take the timestamps of the bad rows (first 2)
        bad_rows = min_data.loc[invalid_mask]

        details = []
        for ts in bad_rows.index[:2]:
            dt_str = ts.strftime("%Y-%m-%d %H:%M")
            try:
                # Raw nanosecond value, for searching the CSV
                ts_val = ts.value
                details.append(f"{dt_str} (TS={ts_val})")
            except Exception:
                details.append(dt_str)

        ts_str = ", ".join(details)
        if bad_count > 2:
            ts_str += "..."

        result_msg += f" [Warning] {bad_count} bad row(s): {ts_str}"

        # Drop the invalid rows to avoid polluting the daily bars
        min_data = min_data.loc[~invalid_mask]

    # Resample into daily bars
    day_data = min_data.resample("1D").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})

    day_data = day_data.dropna(subset=["Open", "High", "Low", "Close", "Volume"])

    # Compute moving averages
    day_data["SMA5"] = day_data["Close"].rolling(window=5).mean().round(2)
    day_data["SMA10"] = day_data["Close"].rolling(window=10).mean().round(2)
    day_data["SMA20"] = day_data["Close"].rolling(window=20).mean().round(2)
    day_data["SMA60"] = day_data["Close"].rolling(window=60).mean().round(2)
    day_data["EMA5"] = day_data["Close"].ewm(span=5).mean().round(2)
    day_data["EMA10"] = day_data["Close"].ewm(span=10).mean().round(2)
    day_data["EMA20"] = day_data["Close"].ewm(span=20).mean().round(2)
    day_data["EMA60"] = day_data["Close"].ewm(span=60).mean().round(2)

    # Write the daily-bar data to the `_day.csv` file
    day_data.to_csv(os.path.join(data_dir, day_file), index=True)
    return result_msg


def process_all(codes: list[str] | None = None) -> None:
    # Collect all `_min.csv` files in the data directory
    if not os.path.exists(config.DATA_DIR):
        logger.error(f"Data directory {config.DATA_DIR} does not exist.")
        return

    target_codes = None
    if codes:
        target_codes = {code.strip() for item in codes for code in item.split(",") if code.strip()}
    min_files = [f for f in os.listdir(config.DATA_DIR) if f.endswith("_min.csv")]
    if target_codes:
        min_files = [f for f in min_files if f.removesuffix("_min.csv") in target_codes]

    if not min_files:
        logger.info("No _min.csv files found.")
        return

    logger.info(f"Found {len(min_files)} files to convert.")

    # Use a ProcessPoolExecutor to build the process pool
    with ProcessPoolExecutor() as executor:
        # Dispatch each conversion task to the process pool
        results = executor.map(process_min_file, min_files, [config.DATA_DIR] * len(min_files))

        # Log from the main process only, to avoid multiple processes
        # competing for the file write lock (a common issue on Windows)
        for res in results:
            if res:
                logger.info(res)


if __name__ == "__main__":
    # Setup Logger
    parser = argparse.ArgumentParser(description="Convert selected minute CSV files to daily CSV files.")
    parser.add_argument(
        "--codes",
        nargs="*",
        default=None,
        help="Optional stock codes, comma-separated or space-separated.",
    )
    args = parser.parse_args()

    setup_logging("convert_stock_price.log")
    process_all(codes=args.codes)
