"""Pure observed-row technical components, without calendar or price adjustment."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, cast

import numpy as np
import pandas as pd
from numpy.typing import NDArray

CONTRACT_VERSION = "technical-components.v1"
INPUT_COLUMNS = ["Code", "Date", "Open", "High", "Low", "Close", "Volume", "Source"]
FEATURE_COLUMNS = (
    [f"SMA{n}" for n in (5, 10, 20, 60, 120)]
    + ["EMA12", "EMA26", "MACD12_26", "MACDSignal9", "MACDHistogram"]
    + [f"Return{n}" for n in (5, 20, 60, 120)]
    + ["PriorHigh20", "PriorHigh60", "PriorLow20", "PriorLow60"]
    + ["BBMean20", "BBStd20", "BBUpper20", "BBLower20", "ATR14", "RSI14"]
    + ["VolumeMean5", "VolumeMean20", "VolumeRatio5_20"]
)
logger = logging.getLogger(__name__)


def normalize_input(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate keys and normalize relevant columns, retaining nonfinite numbers."""
    if not frame.columns.is_unique or any(column not in frame for column in INPUT_COLUMNS):
        raise ValueError("input must contain unique named input columns")
    result = frame.loc[:, INPUT_COLUMNS].copy()
    for column in ("Code", "Source"):
        values = cast(pd.Series, result[column])
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError(f"{column} must be nonempty text")
    dates: list[pd.Timestamp] = []
    for value in cast(pd.Series, result["Date"]):
        if not isinstance(value, (str, date, datetime, pd.Timestamp, np.datetime64)):
            raise ValueError("Date must be a naive midnight date")
        try:
            timestamp = pd.Timestamp(value)
        except (ValueError, TypeError, OverflowError) as error:
            raise ValueError("Date must be a naive midnight date") from error
        if not isinstance(timestamp, pd.Timestamp) or timestamp.tzinfo is not None or timestamp != timestamp.normalize():
            raise ValueError("Date must be a naive midnight date")
        dates.append(timestamp)
    result["Date"] = pd.DatetimeIndex(dates)
    for column in ("Open", "High", "Low", "Close", "Volume"):
        numbers: list[float] = []
        for value in cast(pd.Series, result[column]):
            if value is None or value is pd.NA:
                numbers.append(float("nan"))
                continue
            if isinstance(value, (bool, np.bool_)):
                raise ValueError(f"{column} must contain numbers or missing values")
            try:
                numbers.append(float(value))
            except (ValueError, TypeError, OverflowError) as error:
                raise ValueError(f"{column} must contain numbers or missing values") from error
        result[column] = np.asarray(numbers, dtype=np.float64)
    if result.duplicated(["Code", "Date"]).any():
        raise ValueError("duplicate Code/Date keys")
    return result.sort_values(["Code", "Date"]).reset_index(drop=True)


def _seeded_average(values: NDArray[np.float64], window: int, alpha: float) -> NDArray[np.float64]:
    """SMA seed followed by recursive smoothing, skipping leading warm-up NaNs."""
    result = np.full(len(values), np.nan)
    finite = np.flatnonzero(np.isfinite(values))
    if not len(finite):
        return result
    start = int(finite[0])
    seed_end = start + window
    if seed_end > len(values) or not np.isfinite(values[start:seed_end]).all():
        return result
    result[seed_end - 1] = values[start:seed_end].mean()
    for index in range(seed_end, len(values)):
        result[index] = alpha * values[index] + (1 - alpha) * result[index - 1]
    return result


def _price_segment(segment: pd.DataFrame) -> pd.DataFrame:
    length = len(segment)
    close = cast(pd.Series, segment["Close"]).reset_index(drop=True)
    output = pd.DataFrame(index=pd.RangeIndex(length))
    for window in (5, 10, 20, 60, 120):
        if length >= window:
            output[f"SMA{window}"] = close.rolling(window, min_periods=window).mean()
    for window in (5, 20, 60, 120):
        if length > window:
            output[f"Return{window}"] = close / close.shift(window) - 1
    if length < 12:
        return output
    values = close.to_numpy(dtype=np.float64)
    ema12 = _seeded_average(values, 12, 2 / 13)
    output["EMA12"] = ema12
    if length >= 26:
        ema26 = _seeded_average(values, 26, 2 / 27)
        macd = ema12 - ema26
        output["EMA26"], output["MACD12_26"] = ema26, macd
        if length >= 34:
            signal = _seeded_average(macd, 9, 2 / 10)
            output["MACDSignal9"], output["MACDHistogram"] = signal, macd - signal
    if length >= 20:
        mean = close.rolling(20, min_periods=20).mean()
        std = close.rolling(20, min_periods=20).std(ddof=0)
        output["BBMean20"], output["BBStd20"] = mean, std
        output["BBUpper20"], output["BBLower20"] = mean + 2 * std, mean - 2 * std
    if length >= 14:
        high = cast(pd.Series, segment["High"]).reset_index(drop=True)
        low = cast(pd.Series, segment["Low"]).reset_index(drop=True)
        for window in (20, 60):
            if length > window:
                output[f"PriorHigh{window}"] = high.shift(1).rolling(window, min_periods=window).max()
                output[f"PriorLow{window}"] = low.shift(1).rolling(window, min_periods=window).min()
        previous = close.shift(1)
        tr = pd.concat([high - low, (high - previous).abs(), (low - previous).abs()], axis=1).max(axis=1)
        output["ATR14"] = _seeded_average(tr.to_numpy(dtype=np.float64), 14, 1 / 14)
    if length >= 15:
        rsi = np.full(length, np.nan)
        changes = np.diff(values)
        gain = _seeded_average(np.maximum(changes, 0), 14, 1 / 14)
        loss = _seeded_average(np.maximum(-changes, 0), 14, 1 / 14)
        known = np.isfinite(gain) & np.isfinite(loss)
        flat = known & (gain == 0) & (loss == 0)
        positive_only = known & (gain > 0) & (loss == 0)
        declining = known & (loss > 0)
        change_rsi = np.full(len(changes), np.nan)
        change_rsi[flat] = 50
        change_rsi[positive_only] = 100
        change_rsi[declining] = 100 - 100 / (1 + gain[declining] / loss[declining])
        rsi[1:] = change_rsi
        output["RSI14"] = rsi
    return output


def compute_components(frame: pd.DataFrame) -> pd.DataFrame:
    """Compute each symbol independently, resetting price state at invalid OHLC."""
    normalized = normalize_input(frame)
    result = normalized.loc[:, ["Code", "Date"]].copy()
    ohlc = normalized.loc[:, ["Open", "High", "Low", "Close"]].to_numpy(dtype=np.float64)
    valid = np.isfinite(ohlc).all(axis=1) & (ohlc > 0).all(axis=1)
    valid &= (ohlc[:, 1] >= ohlc[:, [0, 2, 3]].max(axis=1)) & (ohlc[:, 2] <= ohlc[:, [0, 1, 3]].min(axis=1))
    result["price_valid"] = valid
    for column in FEATURE_COLUMNS:
        result[column] = np.nan
    for _, symbol in normalized.groupby("Code", sort=False):
        indices = symbol.index.to_numpy(dtype=int)
        local_valid = valid[indices]
        boundaries = np.diff(np.r_[False, local_valid, False].astype(int))
        for start, stop in zip(np.flatnonzero(boundaries == 1), np.flatnonzero(boundaries == -1), strict=True):
            if stop - start < 5:
                continue
            segment_indices = indices[start:stop]
            components = _price_segment(normalized.iloc[segment_indices])
            result.loc[segment_indices, components.columns] = components.to_numpy(dtype=np.float64)
        volume = cast(pd.Series, symbol["Volume"])
        volume = cast(pd.Series, volume.where(np.isfinite(volume) & (volume >= 0)))
        mean5 = cast(pd.Series, volume.rolling(5, min_periods=5).mean())
        mean20 = cast(pd.Series, volume.rolling(20, min_periods=20).mean())
        result.loc[indices, "VolumeMean5"] = mean5.to_numpy(dtype=np.float64)
        result.loc[indices, "VolumeMean20"] = mean20.to_numpy(dtype=np.float64)
        volume_ratio = cast(pd.Series, mean5 / mean20.where(mean20 != 0))
        result.loc[indices, "VolumeRatio5_20"] = volume_ratio.to_numpy(dtype=np.float64)
    logger.debug("Technical components rows=%d symbols=%d invalid_prices=%d", len(result), normalized["Code"].nunique(), int((~valid).sum()))
    return result


def definition_metadata() -> dict[str, Any]:
    """Return JSON-compatible formula, warm-up and gap definitions for manifests."""
    return {
        "contract_version": CONTRACT_VERSION,
        "precision": "unrounded float64",
        "volume_unit": "lots",
        "gap_policy": (
            "Observed rows only; no inferred calendar or filling. Invalid OHLC resets all price warm-up; "
            "volume windows are independent and require finite nonnegative volume."
        ),
        "price_valid": "Open/High/Low/Close finite and positive; Low <= Open,Close <= High and Low <= High",
        "SMA": "Full n-close arithmetic mean; n=5,10,20,60,120; first value at row n",
        "EMA": "n-close SMA seed at row n then alpha=2/(n+1); n=12,26",
        "MACD12_26": "EMA12 - EMA26; first value at row 26",
        "MACDSignal9": "First 9 finite MACD values SMA seed at row 34, then alpha=2/10; leading warm-up NaNs skipped",
        "MACDHistogram": "MACD12_26 - MACDSignal9",
        "Return": "Close[t]/Close[t-n]-1; n=5,20,60,120; first value at row n+1",
        "PriorHighLow": "Maximum High/minimum Low over previous n rows excluding current; n=20,60; first value at row n+1",
        "Bollinger": "20-close mean and population std (ddof=0); upper/lower mean +/-2 std; first value at row 20",
        "ATR14": "TR=max(High-Low,abs(High-previous Close),abs(Low-previous Close)); first TR=High-Low; seed mean of 14 TR at row 14 then alpha=1/14",
        "RSI14": "14 changes seed mean gain/loss at row 15 then Wilder alpha=1/14; 100-100/(1+gain/loss); flat=50, gain-only=100, loss-only=0",
        "VolumeMean": "Full n-row mean of finite nonnegative Volume; n=5,20",
        "VolumeRatio5_20": "VolumeMean5/VolumeMean20; zero denominator unknown",
    }
