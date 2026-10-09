import logging
import os
import re
import time

import yfinance as yf

from config import config
from shp_utils.logging_utils import setup_logging

logger = logging.getLogger(__name__)

DATA_DIR = config.DATA_DIR
DIV_DIR = config.DATA_DIR / "dividends"


def get_stock_codes():
    """Scan data directory for existing day files"""
    codes: list[str] = []
    if not os.path.exists(DATA_DIR):
        return codes

    for f in os.listdir(DATA_DIR):
        match = re.search(r"^(\d+)_day\.csv$", f)
        if match:
            codes.append(match.group(1))
    return sorted(codes)


def fetch_dividend(code: str) -> None:
    try:
        # Try TWSE first
        ticker_str = f"{code}.TW"
        ticker = yf.Ticker(ticker_str)
        divs = ticker.dividends

        # If empty, try OTC
        if divs.empty:
            ticker_str = f"{code}.TWO"
            ticker = yf.Ticker(ticker_str)
            divs = ticker.dividends

        if not divs.empty:
            # Create the output directory here, not at import time: importing
            # this module should not touch the data directory at all.
            os.makedirs(DIV_DIR, exist_ok=True)
            target = os.path.join(DIV_DIR, f"{code}.csv")
            divs.to_csv(target, header=["Dividends"])
            logger.info(f"[{code}] Fetched {len(divs)} records from {ticker_str}")
        else:
            logger.warning(f"[{code}] No dividend data found")

    except Exception as e:
        logger.error(f"[{code}] Error: {e}")


def main():
    setup_logging("fetch_dividends.log")
    codes = get_stock_codes()
    logger.info(f"Found {len(codes)} stocks locally.")

    # Check existing
    tasks = []
    for code in codes:
        if not os.path.exists(os.path.join(DIV_DIR, f"{code}.csv")):
            tasks.append(code)

    logger.info(f"Fetching needed for {len(tasks)} stocks (Skip {len(codes) - len(tasks)}).")

    # Use ThreadPool with single worker to avoid Rate Limit
    # Yahoo Finance is strict.
    for code in tasks:
        fetch_dividend(code)
        time.sleep(2.0)  # Sleep 2.0s between requests

    logger.info("Dividend fetch complete.")


if __name__ == "__main__":
    main()
