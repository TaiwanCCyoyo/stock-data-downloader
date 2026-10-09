"""Offline discovery tests; no broker, acquisition or dividend requests."""

from pathlib import Path

import fetch_dividends
import pytest


def test_stock_codes_use_only_sorted_daily_filenames(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("9002_day.csv", "9001_day.csv", "9003_min.csv", "notes.txt"):
        (tmp_path / name).write_text("synthetic", encoding="utf-8")
    monkeypatch.setattr(fetch_dividends, "DATA_DIR", tmp_path)
    assert fetch_dividends.get_stock_codes() == ["9001", "9002"]


def test_missing_stock_directory_is_not_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    absent = tmp_path / "absent"
    monkeypatch.setattr(fetch_dividends, "DATA_DIR", absent)
    assert fetch_dividends.get_stock_codes() == []
    assert not absent.exists()
