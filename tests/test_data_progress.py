"""Execute the documented read-only inspection against isolated SQLite fixtures."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from config import config


def _inspect() -> None:
    document = (Path(__file__).resolve().parents[1] / "docs/data-progress.md").read_text(encoding="utf-8")
    source = document.split("@'\n", 1)[1].split("\n'@", 1)[0]
    exec(compile(source, "docs/data-progress.md", "exec"), {})


@pytest.mark.parametrize("period_column", ["period", "year_month", None])
def test_inspection_finds_old_candidate_gaps_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], period_column: str | None
) -> None:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    path = tmp_path / "official_daily.sqlite"
    days = [(date(2025, 1, 1) + timedelta(days=index)).isoformat() for index in range(25)]
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE official_daily_price (market TEXT, date TEXT, code TEXT)")
        conn.executemany(
            "INSERT INTO official_daily_price VALUES (?, ?, ?)",
            [(market, day, "example") for market in ("twse", "tpex") for day in days if market != "tpex" or day != days[2]],
        )
        if period_column:
            conn.execute(f"CREATE TABLE official_history_raw_ranges (market TEXT, code TEXT, {period_column} TEXT, status TEXT)")
            conn.execute("INSERT INTO official_history_raw_ranges VALUES ('twse', 'example', '2025-01', 'error')")
    before = path.read_bytes()
    _inspect()
    output = capsys.readouterr().out
    assert f"Potential gaps observed in another market: tpex ['{days[2]}']" in output
    assert "Potential gaps observed in another market: twse []" in output
    assert ("Unsettled checkpoints:" if period_column else "History checkpoints unavailable") in output
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == [path.name]


def test_missing_database_is_not_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with pytest.raises(sqlite3.OperationalError):
        _inspect()
    assert not list(tmp_path.iterdir())


def test_empty_database_does_not_invent_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with sqlite3.connect(tmp_path / "official_daily.sqlite") as conn:
        conn.execute("CREATE TABLE official_daily_price (market TEXT, date TEXT, code TEXT)")
    _inspect()
    output = capsys.readouterr().out
    assert "Coverage (market, first, latest, dates, codes): []" in output
    assert "Full observed interval:" not in output


def test_mixed_twse_checkpoints_keep_day_and_month_ranges_separate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    path = tmp_path / "official_daily.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE official_daily_price (market TEXT, date TEXT, code TEXT)")
        conn.execute("CREATE TABLE official_history_raw_ranges (market TEXT, code TEXT, period TEXT, status TEXT)")
        conn.executemany(
            "INSERT INTO official_history_raw_ranges VALUES (?, ?, ?, ?)",
            [("TWSE", "2330", "2025-01", "ok"), ("TWSE", "", "2025-01-02", "ok"), ("TWSE", "", "2025-01-03", "ok")],
        )
    before = path.read_bytes()
    _inspect()
    output = capsys.readouterr().out
    assert "('TWSE', 'market-day', 'ok', 2, '2025-01-02', '2025-01-03')" in output
    assert "('TWSE', 'symbol-month', 'ok', 1, '2025-01', '2025-01')" in output
    assert path.read_bytes() == before
