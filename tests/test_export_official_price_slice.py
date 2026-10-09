"""Focused tests for the additive explicit official-price exporter."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import export_official_price_slice as exporter
import pyarrow as pa
import pyarrow.parquet as pq
import pytest


def _make_db(path: Path, *, primary_key: bool = True) -> None:
    key = ", PRIMARY KEY (market, code, date)" if primary_key else ""
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE official_daily_price (
                market TEXT NOT NULL, code TEXT NOT NULL, date TEXT NOT NULL,
                name TEXT, open REAL, high REAL, low REAL, close REAL,
                volume INTEGER, value REAL, transactions INTEGER,
                close_missing INTEGER NOT NULL, raw_fields_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            """
            + key
            + ")"
        )


def _insert(
    path: Path,
    *,
    market: str = "TWSE",
    code: str = "9999",
    trade_date: str = "2019-01-02",
    close: float | None = 10.5,
    volume: int | float | None = 25_500,
    close_missing: int = 0,
) -> None:
    row = (
        market,
        code,
        trade_date,
        "Old instrument",
        10.0,
        11.0,
        9.0,
        close,
        volume,
        123_456.0,
        7,
        close_missing,
        '{"source":"fixture"}',
        "2019-02-01T00:00:00+00:00",
    )
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO official_daily_price VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)


@pytest.fixture
def source_db(tmp_path: Path) -> Path:
    path = tmp_path / "source.sqlite"
    _make_db(path)
    _insert(path)
    _insert(path, market="TPEx", code="9999", close=99.0)
    _insert(path, code="9999", trade_date="2019-01-03", close=11.0)
    _insert(path, code="1101", close=None, volume=0, close_missing=1)
    return path


def _manifest(destination: Path) -> dict[str, object]:
    return json.loads((destination / "manifest.json").read_text(encoding="utf-8"))


def test_exports_only_requested_market_codes_and_dates_preserving_source_values(source_db: Path, tmp_path: Path):
    before = hashlib.sha256(source_db.read_bytes()).hexdigest()
    destination = exporter.export_official_price_slice(
        db_path=source_db,
        market="TWSE",
        codes=["9999", "1101", "9999", "MISSING"],
        start_date="2019-01-02",
        end_date="2019-01-02",
        output_dir=tmp_path / "slice",
    )

    assert destination == tmp_path / "slice"
    table = pq.read_table(destination / "prices.parquet")
    assert table.schema == exporter.SCHEMA
    assert table.column("market").to_pylist() == ["TWSE", "TWSE"]
    assert table.column("code").to_pylist() == ["1101", "9999"]
    missing = table.to_pylist()[0]
    assert missing["close"] is None
    assert missing["volume"] == 0
    assert missing["close_missing"] == 1
    assert table.to_pylist()[1]["volume"] == 25_500
    assert hashlib.sha256(source_db.read_bytes()).hexdigest() == before

    manifest = _manifest(destination)
    assert manifest["selected_rows_sha256"] == exporter._canonical_digest([list(row.values()) for row in table.to_pylist()])
    assert manifest["selection"] == {
        "market": "TWSE",
        "codes": ["1101", "9999", "MISSING"],
        "start_date": "2019-01-02",
        "end_date": "2019-01-02",
    }
    assert manifest["observed_codes"] == ["1101", "9999"]
    assert manifest["missing_codes"] == ["MISSING"]
    assert manifest["units"] == {"volume": "shares"}
    assert manifest["coverage_certified"] is False


def test_empty_and_partial_selections_keep_the_explicit_arrow_schema(source_db: Path, tmp_path: Path):
    empty = exporter.export_official_price_slice(
        db_path=source_db,
        market="TWSE",
        codes=["MISSING"],
        start_date="2019-01-02",
        end_date="2019-01-02",
        output_dir=tmp_path / "empty",
    )
    assert pq.read_table(empty / "prices.parquet").schema == exporter.SCHEMA
    assert _manifest(empty)["missing_codes"] == ["MISSING"]

    partial = exporter.export_official_price_slice(
        db_path=source_db,
        market="TWSE",
        codes=["1101", "MISSING"],
        start_date="2019-01-02",
        end_date="2019-01-02",
        output_dir=tmp_path / "partial",
    )
    schema = pq.read_schema(partial / "prices.parquet")
    assert schema.field("open").type == pa.float64()
    assert schema.field("volume").type == pa.int64()
    assert _manifest(partial)["missing_codes"] == ["MISSING"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"market": "OTHER"}, "market"),
        ({"codes": []}, "codes"),
        ({"codes": ["9999", 1]}, "codes"),
        ({"start_date": "2019-1-02"}, "start_date"),
        ({"start_date": "2019-01-03", "end_date": "2019-01-02"}, "start_date"),
        ({"max_rows": 0}, "max_rows"),
    ],
)
def test_rejects_invalid_selection_before_creating_destination(source_db: Path, tmp_path: Path, kwargs: dict[str, object], message: str):
    destination = tmp_path / "invalid"
    params: dict[str, object] = {
        "db_path": source_db,
        "market": "TWSE",
        "codes": ["9999"],
        "start_date": "2019-01-02",
        "end_date": "2019-01-02",
        "output_dir": destination,
    }
    params.update(kwargs)
    with pytest.raises(ValueError, match=message):
        exporter.export_official_price_slice(**params)  # type: ignore[arg-type]
    assert not destination.exists()


def test_rejects_invalid_source_ids_duplicate_keys_and_budget_without_output(tmp_path: Path):
    invalid = tmp_path / "invalid.sqlite"
    _make_db(invalid)
    _insert(invalid, code="9999", close_missing=2)
    with pytest.raises(ValueError, match="close_missing"):
        exporter.export_official_price_slice(
            db_path=invalid,
            market="TWSE",
            codes=["9999"],
            start_date="2019-01-02",
            end_date="2019-01-02",
            output_dir=tmp_path / "bad-flag",
        )

    fractional = tmp_path / "fractional.sqlite"
    _make_db(fractional)
    _insert(fractional, volume=1.5)  # SQLite INTEGER affinity permits this REAL value.
    with pytest.raises(ValueError, match="volume"):
        exporter.export_official_price_slice(
            db_path=fractional,
            market="TWSE",
            codes=["9999"],
            start_date="2019-01-02",
            end_date="2019-01-02",
            output_dir=tmp_path / "fractional-output",
        )

    duplicate = tmp_path / "duplicate.sqlite"
    _make_db(duplicate, primary_key=False)
    _insert(duplicate)
    _insert(duplicate)
    with pytest.raises(ValueError, match="duplicate"):
        exporter.export_official_price_slice(
            db_path=duplicate,
            market="TWSE",
            codes=["9999"],
            start_date="2019-01-02",
            end_date="2019-01-02",
            output_dir=tmp_path / "duplicate-output",
        )

    source = tmp_path / "budget.sqlite"
    _make_db(source)
    _insert(source)
    _insert(source, trade_date="2019-01-03")
    output = tmp_path / "over-budget"
    with pytest.raises(ValueError, match="exceed"):
        exporter.export_official_price_slice(
            db_path=source,
            market="TWSE",
            codes=["9999"],
            start_date="2019-01-02",
            end_date="2019-01-03",
            output_dir=output,
            max_rows=1,
        )
    assert not output.exists()


def test_existing_destination_is_preserved_and_hashes_reproduce(source_db: Path, tmp_path: Path):
    existing = tmp_path / "existing"
    existing.mkdir()
    sentinel = existing / "keep.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError):
        exporter.export_official_price_slice(
            db_path=source_db,
            market="TWSE",
            codes=["9999"],
            start_date="2019-01-02",
            end_date="2019-01-02",
            output_dir=existing,
        )
    assert sentinel.read_text(encoding="utf-8") == "preserve"

    first = exporter.export_official_price_slice(
        db_path=source_db,
        market="TWSE",
        codes=["9999"],
        start_date="2019-01-02",
        end_date="2019-01-02",
        output_dir=tmp_path / "first",
    )
    second = exporter.export_official_price_slice(
        db_path=source_db,
        market="TWSE",
        codes=["9999"],
        start_date="2019-01-02",
        end_date="2019-01-02",
        output_dir=tmp_path / "second",
    )
    first_manifest = _manifest(first)
    second_manifest = _manifest(second)
    assert first_manifest["selected_rows_sha256"] == second_manifest["selected_rows_sha256"]
    assert first_manifest["prices_parquet_sha256"] == hashlib.sha256((first / "prices.parquet").read_bytes()).hexdigest()

    with sqlite3.connect(source_db) as connection:
        connection.execute("UPDATE official_daily_price SET close = 12.0 WHERE market = 'TWSE' AND code = '9999' AND date = '2019-01-02'")
    changed = exporter.export_official_price_slice(
        db_path=source_db,
        market="TWSE",
        codes=["9999"],
        start_date="2019-01-02",
        end_date="2019-01-02",
        output_dir=tmp_path / "changed",
    )
    assert _manifest(changed)["selected_rows_sha256"] != first_manifest["selected_rows_sha256"]


def test_real_cli(source_db: Path, tmp_path: Path):
    destination = tmp_path / "cli"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/export_official_price_slice.py",
            "--db-path",
            str(source_db),
            "--market",
            "TWSE",
            "--codes",
            "9999",
            "--start-date",
            "2019-01-02",
            "--end-date",
            "2019-01-02",
            "--output-dir",
            str(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == str(destination)
    assert (destination / "manifest.json").is_file()
