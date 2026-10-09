"""Pytest coverage for convert_stock_price.py.

Run with: uv run pytest tests/test_convert_stock_price.py -v
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from convert_stock_price import process_min_file

MIN_CSV_COLUMNS = ["ts", "Open", "High", "Low", "Close", "Volume"]


def _write_min_csv(data_dir: Path, code: str, rows: list[tuple[str, float, int]]) -> str:
    """rows: list of (timestamp, price, volume); OHLC are all set to price."""
    frame = pd.DataFrame(
        [{"ts": ts, "Open": px, "High": px, "Low": px, "Close": px, "Volume": vol} for ts, px, vol in rows],
        columns=pd.Index(MIN_CSV_COLUMNS),
    )
    name = f"{code}_min.csv"
    frame.to_csv(data_dir / name, index=False)
    return name


def _day_bars(data_dir: Path, code: str) -> pd.DataFrame:
    day = pd.read_csv(data_dir / f"{code}_day.csv")
    day["ts"] = pd.to_datetime(day["ts"])
    return day


def test_sunday_bars_are_dropped_before_the_daily_bar_is_built(tmp_path: Path):
    # Shioaji still serves these: 2024-12-01 is a Sunday, and the live API returns
    # 1-2 bars at 09:01 for 255 symbols on it. Left in, they become a daily bar for a
    # day the market never opened -- and they poison every subsequent SMA/EMA window.
    name = _write_min_csv(
        tmp_path,
        "TESTSUN",
        [
            ("2024-11-29 09:01:00", 100.0, 1000),
            ("2024-11-29 13:30:00", 101.0, 1000),
            ("2024-12-01 09:01:00", 200.0, 13),  # Sunday: market closed
            ("2024-12-02 09:01:00", 102.0, 1000),
            ("2024-12-02 13:30:00", 103.0, 1000),
        ],
    )
    message = process_min_file(name, str(tmp_path))

    days = _day_bars(tmp_path, "TESTSUN")
    assert "2024-12-01" not in set(days["ts"].dt.strftime("%Y-%m-%d"))
    assert days["ts"].dt.dayofweek.max() < 6
    assert "dropped 1 Sunday bar(s) on 2024-12-01" in message


def test_a_saturday_make_up_session_survives(tmp_path: Path):
    # The filter must be Sunday-only. 2018-12-22 is a Saturday and a real TWSE make-up
    # trading session (補行交易日) -- one of 15 in the official artifact, and the only
    # Saturday in this cache. Dropping "weekends" would silently delete a real day.
    name = _write_min_csv(
        tmp_path,
        "TESTSAT",
        [
            ("2018-12-21 09:01:00", 100.0, 1000),
            ("2018-12-22 09:01:00", 105.0, 500),  # Saturday: real make-up session
            ("2018-12-22 11:30:00", 106.0, 500),
        ],
    )
    message = process_min_file(name, str(tmp_path))

    days = _day_bars(tmp_path, "TESTSAT")
    assert "2018-12-22" in set(days["ts"].dt.strftime("%Y-%m-%d"))
    assert "Sunday" not in message


def test_a_file_with_no_sunday_bars_is_not_annotated(tmp_path: Path):
    name = _write_min_csv(
        tmp_path,
        "TESTCLEAN",
        [("2024-11-28 09:01:00", 100.0, 1000), ("2024-11-29 09:01:00", 101.0, 1000)],
    )
    message = process_min_file(name, str(tmp_path))

    assert "Sunday" not in message
    assert len(_day_bars(tmp_path, "TESTCLEAN")) == 2
