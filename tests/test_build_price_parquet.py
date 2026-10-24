"""Pytest coverage for build_price_parquet.py's merge, units and summary line.

Run with: uv run pytest tests/test_build_price_parquet.py -v
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import build_price_parquet
import pandas as pd
import pytest
from fetch_official_daily_price import MARKET_TWSE, init_db, upsert_rows


def _write_day_csv(data_dir: Path, code: str, dates: list[str], closes: list[float] | None = None, volumes: list[float] | None = None) -> None:
    n = len(dates)
    closes = closes if closes is not None else [1.0] * n
    volumes = volumes if volumes is not None else [1.0] * n
    pd.DataFrame({
        "Date": dates,
        "Open": closes,
        "High": closes,
        "Low": closes,
        "Close": closes,
        "Volume": volumes,
    }).to_csv(data_dir / f"{code}_day.csv", index=False)


def _official_row(code: str, trade_date: str, close: float, shares: int, *, close_missing: bool = False) -> dict[str, Any]:
    return {
        "market": MARKET_TWSE,
        "code": code,
        "date": trade_date,
        "name": None,
        "open": close,
        "high": close,
        "low": close,
        "close": None if close_missing else close,
        "volume": shares,
        "value": None,
        "transactions": None,
        "close_missing": close_missing,
        "raw_fields_json": "{}",
        "fetched_at": "2026-08-30T00:00:00+00:00",
    }


def _write_official(data_dir: Path, rows: list[dict[str, Any]]) -> None:
    db_path = data_dir / "official_daily.sqlite"
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        upsert_rows(conn, rows)
        conn.commit()


def _build_and_read(data_dir: Path) -> pd.DataFrame:
    build_price_parquet.build_price_parquet(data_dir)
    return pd.read_parquet(data_dir / "price_daily.parquet")


@pytest.mark.parametrize("unusable_csv", [False, True])
def test_no_usable_day_csv_is_an_ordinary_library_error(tmp_path: Path, unusable_csv: bool) -> None:
    if unusable_csv:
        (tmp_path / "2330_day.csv").write_text("Close\n100\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no usable day CSV files"):
        build_price_parquet.build_price_parquet(tmp_path)
    assert not (tmp_path / "price_daily.parquet").exists()


def test_summary_names_the_latest_bar_and_how_many_symbols_carry_it(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    """Row and symbol totals alone cannot tell a fresh cache from a frozen one --
    on 2026-08-27 and 08-28 this line reported identical totals two nights running
    while no new bar had been downloaded. The latest date plus its symbol count
    distinguishes a healthy day from both a frozen cache and a collapsed contract
    list (a fresh date carried by a handful of symbols)."""
    _write_day_csv(tmp_path, "1101", ["2026-08-26", "2026-08-27"])
    _write_day_csv(tmp_path, "2330", ["2026-08-26", "2026-08-27"])
    # Stops one day short, so the newest date is not carried by every symbol.
    _write_day_csv(tmp_path, "2317", ["2026-08-26"])

    with caplog.at_level(logging.INFO, logger=build_price_parquet.__name__):
        build_price_parquet.build_price_parquet(tmp_path)

    summary = [record.getMessage() for record in caplog.records if "Saved" in record.getMessage()]
    assert summary, "the build must log a summary line"
    assert "2026-08-26..2026-08-27" in summary[-1], summary[-1]
    assert "carried by 2 symbols" in summary[-1], summary[-1]
    assert "by source: {'shioaji': 5}" in summary[-1], summary[-1]


def test_official_wins_where_both_sources_cover_the_same_day(tmp_path: Path):
    # Official is the source of record. Where both have the day, its values are the
    # ones that land -- that is the whole point of the change, and the Source column
    # is what lets a stored result say so afterwards.
    _write_day_csv(tmp_path, "2330", ["2026-08-26"], closes=[100.0], volumes=[10.0])
    _write_official(tmp_path, [_official_row("2330", "2026-08-26", 111.0, 25_000)])

    df = _build_and_read(tmp_path)

    (row,) = df.loc[df["Code"] == "2330"].to_dict("records")
    assert row["Close"] == 111.0
    assert row["Source"] == "official"


def test_a_symbol_official_does_not_cover_survives_as_shioaji(tmp_path: Path):
    # `001`, the market index, has no official daily series at all, and 49 other
    # symbols are likewise absent. They must not vanish just because the source of
    # record does not know them -- most symbols starting 2010 and 50 starting
    # 2018-12 is the intended shape, not a bug.
    _write_day_csv(tmp_path, "001", ["2026-08-26"], closes=[20000.0])
    _write_day_csv(tmp_path, "2330", ["2026-08-26"], closes=[100.0])
    _write_official(tmp_path, [_official_row("2330", "2026-08-26", 111.0, 25_000)])

    df = _build_and_read(tmp_path)

    index_rows = df.loc[df["Code"] == "001"]
    assert len(index_rows) == 1
    assert index_rows.iloc[0]["Close"] == 20000.0
    assert index_rows.iloc[0]["Source"] == "shioaji"


def test_official_volume_is_converted_from_shares_to_lots_as_a_float(tmp_path: Path):
    # This cache publishes lots and is documented that way; the official artifact
    # publishes shares. The result must stay a float -- TWSE's figures include
    # odd-lot trading and do not divide evenly by 1000, and truncating them would
    # be a silent, plausible-looking error.
    _write_day_csv(tmp_path, "2330", ["2026-08-26"], closes=[100.0], volumes=[1.0])
    _write_official(tmp_path, [_official_row("2330", "2026-08-26", 100.0, 25_500)])

    df = _build_and_read(tmp_path)

    (row,) = df.loc[df["Code"] == "2330"].to_dict("records")
    assert row["Volume"] == pytest.approx(25.5)
    assert df["Volume"].dtype == "float64"


def test_a_day_only_one_source_has_is_kept_from_that_source(tmp_path: Path):
    # The merge is a union, not an intersection: official reaches back before the
    # Shioaji files begin, and the Shioaji files carry the current day before the
    # backfill has reached it.
    _write_day_csv(tmp_path, "2330", ["2026-08-27"], closes=[102.0])
    _write_official(tmp_path, [_official_row("2330", "2020-01-02", 50.0, 1_000)])

    df = _build_and_read(tmp_path).sort_values("Date")

    assert list(df["ts"]) == ["2020-01-02", "2026-08-27"]
    assert list(df["Source"]) == ["official", "shioaji"]


def test_an_official_row_with_no_close_is_skipped(tmp_path: Path):
    # A listed symbol that did not trade that day. A real day, but one this cache
    # has never carried, and admitting it would change every symbol's bar count.
    _write_day_csv(tmp_path, "2330", ["2026-08-27"], closes=[102.0])
    _write_official(
        tmp_path,
        [
            _official_row("2330", "2026-08-25", 0.0, 0, close_missing=True),
            _official_row("2330", "2026-08-26", 100.0, 1_000),
        ],
    )

    df = _build_and_read(tmp_path).sort_values("Date")

    assert list(df["ts"]) == ["2026-08-26", "2026-08-27"]


def test_moving_averages_do_not_bleed_across_symbols(tmp_path: Path):
    # Computed per symbol over the merged series. A rolling window that ran over
    # the concatenated frame would fold one symbol's last bars into the next
    # symbol's first ones and produce a plausible wrong number, not an error.
    _write_day_csv(tmp_path, "1101", [f"2026-08-{d:02d}" for d in range(3, 8)], closes=[10.0] * 5)
    _write_day_csv(tmp_path, "2330", [f"2026-08-{d:02d}" for d in range(3, 8)], closes=[100.0] * 5)

    df = _build_and_read(tmp_path)

    for code, expected in (("1101", 10.0), ("2330", 100.0)):
        rows = df.loc[df["Code"] == code].sort_values("Date")
        assert rows["SMA5"].iloc[-1] == pytest.approx(expected)
        assert rows["SMA5"].iloc[:-1].isna().all(), "a 5-day window cannot be full before the 5th bar"


def test_moving_averages_match_the_day_csv_for_a_shioaji_only_symbol(tmp_path: Path):
    # The formulas must be the ones convert_stock_price.py already uses, rounding
    # included. A symbol official does not cover has exactly the same input series
    # as before, so its averages must come out byte-identical -- otherwise this
    # change moves numbers it has no business moving.
    closes = [10.0, 11.0, 12.0, 11.5, 13.0, 12.5, 14.0]
    dates = [f"2026-08-{d:02d}" for d in range(3, 10)]
    _write_day_csv(tmp_path, "001", dates, closes=closes)

    df = _build_and_read(tmp_path).sort_values("Date")

    expected_sma = pd.Series(closes).rolling(window=5).mean().round(2)
    expected_ema = pd.Series(closes).ewm(span=5).mean().round(2)
    assert df["SMA5"].reset_index(drop=True).equals(expected_sma)
    assert df["EMA5"].reset_index(drop=True).equals(expected_ema)
