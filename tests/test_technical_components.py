from __future__ import annotations

import json
from typing import cast

import numpy as np
import pandas as pd
import pytest

from shp_utils import technical_components
from shp_utils.technical_components import FEATURE_COLUMNS, INPUT_COLUMNS, compute_components, definition_metadata, normalize_input


def bars(count: int, *, code: str = "0012") -> pd.DataFrame:
    close = np.arange(1, count + 1, dtype=np.float64) + 100
    return pd.DataFrame({
        "Code": code,
        "Date": pd.date_range("2024-01-01", periods=count),
        "Open": close,
        "High": close + 1,
        "Low": close - 1,
        "Close": close,
        "Volume": np.arange(count, dtype=np.float64) + 1,
        "Source": "official",
    })


def test_hand_checked_ema_macd_seeds_and_full_windows() -> None:
    result = compute_components(bars(130))
    assert pd.isna(result.loc[3, "SMA5"])
    assert result.loc[4, "SMA5"] == 103
    assert result.loc[11, "EMA12"] == 106.5
    assert result.loc[12, "EMA12"] == pytest.approx((2 / 13) * 113 + (11 / 13) * 106.5)
    assert pd.isna(result.loc[24, "EMA26"])
    assert result.loc[25, "EMA26"] == 113.5
    assert result.loc[25, "MACD12_26"] == pytest.approx(7)
    assert pd.isna(result.loc[32, "MACDSignal9"])
    assert result.loc[33, "MACDSignal9"] == pytest.approx(7)
    assert result.loc[33, "MACDHistogram"] == pytest.approx(0)
    assert pd.isna(result.loc[4, "Return5"])
    assert result.loc[5, "Return5"] == pytest.approx(106 / 101 - 1)
    assert pd.isna(result.loc[119, "Return120"])
    assert result.loc[120, "Return120"] == pytest.approx(221 / 101 - 1)


def test_prior_extrema_bollinger_atr_and_volume_formulas() -> None:
    result = compute_components(bars(21))
    assert pd.isna(result.loc[19, "PriorHigh20"])
    assert result.loc[20, "PriorHigh20"] == 121
    assert result.loc[20, "PriorLow20"] == 100
    assert result.loc[19, "BBMean20"] == 110.5
    assert result.loc[19, "BBStd20"] == pytest.approx(np.std(np.arange(101, 121), ddof=0))
    assert result.loc[19, "BBUpper20"] == pytest.approx(110.5 + 2 * np.std(np.arange(101, 121)))
    assert pd.isna(result.loc[12, "ATR14"])
    assert result.loc[13, "ATR14"] == 2
    assert result.loc[14, "ATR14"] == 2
    assert result.loc[19, "VolumeMean5"] == 18
    assert result.loc[19, "VolumeMean20"] == 10.5
    assert result.loc[19, "VolumeRatio5_20"] == pytest.approx(18 / 10.5)


def test_wilder_atr_recursive_update_and_rsi_seed() -> None:
    frame = bars(16)
    close = np.array([100, 102, 101, 103, 102, 104, 103, 105, 104, 106, 105, 107, 106, 108, 107, 109], dtype=float)
    for column in ("Open", "Close"):
        frame[column] = close
    frame["High"], frame["Low"] = close + 1, close - 1
    result = compute_components(frame)
    assert pd.isna(result.loc[13, "RSI14"])
    assert result.loc[14, "RSI14"] == pytest.approx(100 - 100 / 3)
    expected_gain = ((13 * 1) + 2) / 14
    expected_loss = (13 * 0.5) / 14
    assert result.loc[15, "RSI14"] == pytest.approx(100 - 100 / (1 + expected_gain / expected_loss))
    seed_tr = (2 + 7 * 3 + 6 * 2) / 14
    assert result.loc[13, "ATR14"] == pytest.approx(seed_tr)
    assert result.loc[14, "ATR14"] == pytest.approx((13 * seed_tr + 2) / 14)


@pytest.mark.parametrize("direction,expected", [(0, 50), (1, 100), (-1, 0)])
def test_flat_gain_only_and_loss_only_rsi(direction: int, expected: int) -> None:
    frame = bars(15)
    close = 100 + direction * np.arange(15)
    frame["Open"], frame["Close"] = close, close
    frame["High"], frame["Low"] = close + 1, close - 1
    assert compute_components(frame).loc[14, "RSI14"] == expected


def test_price_gap_resets_warmup_but_not_volume_windows() -> None:
    frame = bars(45)
    frame.loc[20, "High"] = 1
    result = compute_components(frame)
    assert not result.loc[20, "price_valid"]
    price_columns = [column for column in FEATURE_COLUMNS if not column.startswith("Volume")]
    assert cast(bool, result.loc[20, price_columns].isna().all())
    assert result.loc[21:24, "SMA5"].isna().all()
    assert result.loc[25, "SMA5"] == 124
    assert pd.isna(result.loc[25, "Return5"])
    assert result.loc[26, "Return5"] == pytest.approx(127 / 122 - 1)
    assert pd.isna(result.loc[44, "EMA26"])
    assert result.loc[20, "VolumeMean20"] == 11.5


@pytest.mark.parametrize("invalid", [np.nan, np.inf, -1])
def test_invalid_volume_is_unknown_without_resetting_price(invalid: float) -> None:
    frame = bars(45)
    frame.loc[20, "Volume"] = invalid
    result = compute_components(frame)
    assert cast(bool, result["price_valid"].all())
    assert result.loc[20, "SMA5"] == 119
    assert result.loc[20:24, "VolumeMean5"].isna().all()
    assert result.loc[25, "VolumeMean5"] == 24
    assert result.loc[20:39, "VolumeMean20"].isna().all()
    assert result.loc[40, "VolumeMean20"] == 31.5


def test_short_segments_zero_volume_and_absent_dates_not_filled() -> None:
    frame = bars(4).drop(index=1)
    frame["Volume"] = 0
    result = compute_components(frame)
    assert len(result) == 3
    assert result.loc[:, FEATURE_COLUMNS].isna().all().all()
    zero = bars(20)
    zero["Volume"] = 0
    result = compute_components(zero)
    assert result.loc[19, "VolumeMean20"] == 0
    assert pd.isna(result.loc[19, "VolumeRatio5_20"])


def test_symbols_sort_and_prefix_has_no_future_dependency() -> None:
    first, second = bars(130), bars(40, code="9999")
    mixed = pd.concat([second, first]).sample(frac=1, random_state=4)
    normalized = normalize_input(mixed)
    assert normalized.loc[0, "Code"] == "0012"
    assert normalized.index.equals(pd.RangeIndex(170))
    result = compute_components(mixed)
    pd.testing.assert_frame_equal(result.iloc[:130].reset_index(drop=True), compute_components(first))
    pd.testing.assert_frame_equal(compute_components(first.iloc[:50]), compute_components(first).iloc[:50])
    assert pd.isna(result.loc[133, "SMA5"])


@pytest.mark.parametrize("column,value", [("Code", None), ("Code", 12), ("Source", np.nan), ("Source", " "), ("Close", "bad"), ("Volume", ""), ("Close", True)])
def test_normalization_rejects_malformed_text_and_numbers(column: str, value: object) -> None:
    frame = bars(2).astype(object)
    frame.loc[0, column] = value
    with pytest.raises(ValueError, match=column):
        normalize_input(frame)


@pytest.mark.parametrize("value", ["bad-date", "2024-02-30", "2024-01-01T01:00:00", "2024-01-01T00:00:00Z", pd.NaT, 20240101])
def test_normalization_rejects_malformed_dates(value: object) -> None:
    frame = bars(2).astype(object)
    frame.loc[0, "Date"] = value
    with pytest.raises(ValueError, match="Date"):
        normalize_input(frame)


def test_duplicates_nonfinite_retention_precision_and_metadata() -> None:
    frame = bars(2)
    with pytest.raises(ValueError, match="duplicate"):
        normalize_input(pd.concat([frame, frame.iloc[:1]]))
    frame.loc[0, "Close"] = 101.123456789
    frame.loc[1, "Volume"] = np.inf
    normalized = normalize_input(frame)
    assert list(normalized.columns) == INPUT_COLUMNS
    assert normalized.loc[0, "Close"] == 101.123456789
    assert np.isinf(normalized.loc[1, "Volume"])
    assert normalized["Close"].dtype == np.dtype("float64")
    json.dumps(definition_metadata(), allow_nan=False)


def test_empty_input_retains_output_schema() -> None:
    result = compute_components(bars(0))
    assert list(result.columns) == ["Code", "Date", "price_valid", *FEATURE_COLUMNS]
    assert result.empty


@pytest.mark.parametrize("length", [*range(1, 36), 59, 60, 61, 119, 120, 121])
def test_short_fastpaths_equal_full_math_prefix(length: int) -> None:
    # A long valid segment executes every formula; its prefix provides the
    # original full-math results even when the short path omits unavailable work.
    frame = bars(130)
    close = 100 + np.sin(np.arange(130) / 3) * 5 + np.arange(130) / 10
    frame["Open"], frame["Close"] = close, close
    frame["High"], frame["Low"] = close + 2, close - 3
    frame["Volume"] = np.arange(130) % 7
    expected = compute_components(frame).iloc[:length]
    actual = compute_components(frame.iloc[:length])
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)


def test_one_to_four_row_fragments_skip_price_work_but_compute_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_price_work(_segment: pd.DataFrame) -> pd.DataFrame:
        pytest.fail("one-to-four-row fragments must skip _price_segment")

    monkeypatch.setattr(technical_components, "_price_segment", unexpected_price_work)
    frame = bars(21)
    frame.loc[[1, 4, 8, 13, 15, 18], "High"] = 1
    result = compute_components(frame)
    price_columns = [column for column in FEATURE_COLUMNS if not column.startswith("Volume")]
    assert result.loc[:, price_columns].isna().all().all()
    assert result.loc[19, "VolumeMean5"] == 18
    assert result.loc[19, "VolumeMean20"] == 10.5
    assert result.loc[19, "VolumeRatio5_20"] == pytest.approx(18 / 10.5)
