"""Functional contracts for the local builder and consolidated-price integration."""

from __future__ import annotations

import hashlib
from pathlib import Path

import build_price_parquet
import build_technical_cache
import pandas as pd
import pytest

from config import config
from shp_utils.technical_cache import load_features


def _frame() -> pd.DataFrame:
    close = pd.Series([100 + i / 3 for i in range(70)])
    return pd.DataFrame({
        "Code": "0050",
        "Date": pd.date_range("2020-01-01", periods=70),
        "Open": close,
        "High": close + 1,
        "Low": close - 1,
        "Close": close,
        "Volume": 10.5,
        "Source": "official",
    })


def test_cli_default_uses_config_not_current_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    prices = source / "price_daily.parquet"
    _frame().to_parquet(prices, index=False)
    old_hash = hashlib.sha256(prices.read_bytes()).hexdigest()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", str(source))
    monkeypatch.chdir(elsewhere)
    assert build_technical_cache.main([]) == 0
    manifests = list((source / "technical_features" / "manifests").glob("*.json"))
    assert len(manifests) == 1
    assert build_technical_cache.main(["--verify", str(manifests[0])]) == 0
    assert hashlib.sha256(prices.read_bytes()).hexdigest() == old_hash
    assert list(elsewhere.iterdir()) == []
    assert len(load_features(manifests[0])) == 70


def test_custom_input_requires_basis_before_reading(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="2"):
        build_technical_cache.main(["--input", str(tmp_path / "absent.parquet")])


@pytest.mark.parametrize(
    "arguments",
    [
        ["--price-basis", "adjusted"],
        ["--price-basis", "raw-unadjusted"],
        ["--input", "absent.parquet", "--price-basis", ""],
        ["--input", "absent.parquet", "--price-basis", "   "],
        ["--verify", "absent.json", "--price-basis", "adjusted"],
        ["--verify", "absent.json", "--input", "absent.parquet"],
    ],
)
def test_invalid_basis_arguments_fail_before_file_access(arguments: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_file_access(*args: object, **kwargs: object) -> None:
        pytest.fail("argument validation must precede input reads, cache writes and manifest verification")

    monkeypatch.setattr(build_technical_cache.pd, "read_parquet", unexpected_file_access)
    monkeypatch.setattr(build_technical_cache, "build_cache", unexpected_file_access)
    monkeypatch.setattr(build_technical_cache, "verify_manifest", unexpected_file_access)
    with pytest.raises(SystemExit, match="2"):
        build_technical_cache.main(arguments)


def test_custom_input_with_basis_builds_declared_generation(tmp_path: Path) -> None:
    source = tmp_path / "custom.parquet"
    output = tmp_path / "features"
    _frame().to_parquet(source, index=False)
    arguments = ["--input", str(source), "--price-basis", "caller-adjusted", "--output-dir", str(output)]
    assert build_technical_cache.main(arguments) == 0
    manifests = list((output / "manifests").glob("*.json"))
    assert len(manifests) == 1
    assert build_technical_cache.verify_manifest(manifests[0])["price_basis"] == "caller-adjusted"
    assert len(load_features(manifests[0])) == 70


def test_integrated_builder_preserves_legacy_values_and_adds_pinned_features(tmp_path: Path) -> None:
    frame = _frame()
    frame.drop(columns=["Code", "Source"]).to_csv(tmp_path / "0050_day.csv", index=False)
    result = build_price_parquet.build_price_parquet(tmp_path, technical_features=True)
    actual = pd.read_parquet(result)
    pd.testing.assert_series_equal(actual["SMA5"], frame["Close"].rolling(5).mean().round(2), check_names=False)
    pd.testing.assert_series_equal(actual["EMA5"], frame["Close"].ewm(span=5).mean().round(2), check_names=False)
    assert list(actual.columns) == build_price_parquet.OUTPUT_COLUMNS
    manifests = list((tmp_path / "technical_features" / "manifests").glob("*.json"))
    features = load_features(manifests[0])
    assert len(features) == len(actual)
    pd.testing.assert_series_equal(features["SMA5"], actual["Close"].rolling(5).mean(), check_names=False)
    assert features["SMA5"].iloc[-1] != actual["SMA5"].iloc[-1]


def test_feature_failure_is_not_reported_as_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _frame().drop(columns=["Code", "Source"]).to_csv(tmp_path / "0050_day.csv", index=False)

    def fail(*args: object, **kwargs: object) -> None:
        raise ValueError("cache evidence corrupt")

    monkeypatch.setattr(build_price_parquet, "build_cache", fail)
    with pytest.raises(ValueError, match="cache evidence corrupt"):
        build_price_parquet.build_price_parquet(tmp_path, technical_features=True)
    assert (tmp_path / "0050_day.csv").is_file()


def test_default_daily_build_does_not_accumulate_feature_generations(tmp_path: Path) -> None:
    _frame().drop(columns=["Code", "Source"]).to_csv(tmp_path / "0050_day.csv", index=False)
    build_price_parquet.build_price_parquet(tmp_path)
    assert not (tmp_path / "technical_features").exists()
