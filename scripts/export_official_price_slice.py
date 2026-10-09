"""Export an explicit, source-preserving slice of official daily prices."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq

COLUMNS = (
    "market",
    "code",
    "date",
    "name",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "value",
    "transactions",
    "close_missing",
    "raw_fields_json",
    "fetched_at",
)
SCHEMA = pa.schema([
    pa.field("market", pa.string(), nullable=False),
    pa.field("code", pa.string(), nullable=False),
    pa.field("date", pa.string(), nullable=False),
    pa.field("name", pa.string()),
    pa.field("open", pa.float64()),
    pa.field("high", pa.float64()),
    pa.field("low", pa.float64()),
    pa.field("close", pa.float64()),
    pa.field("volume", pa.int64()),
    pa.field("value", pa.float64()),
    pa.field("transactions", pa.int64()),
    pa.field("close_missing", pa.int64()),
    pa.field("raw_fields_json", pa.string(), nullable=False),
    pa.field("fetched_at", pa.string(), nullable=False),
])
VALID_MARKETS = frozenset(("TWSE", "TPEx"))


def _canonical_date(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO date string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field_name} must be a canonical ISO date") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{field_name} must be a canonical ISO date")
    return value


def _normalise_codes(codes: Iterable[str]) -> list[str]:
    if isinstance(codes, str):
        raise ValueError("codes must be an iterable of nonempty strings")
    try:
        normalised = sorted(set(codes))
    except TypeError as error:
        raise ValueError("codes must be an iterable of nonempty strings") from error
    if not normalised or any(not isinstance(code, str) or not code for code in normalised):
        raise ValueError("codes must be a nonempty iterable of nonempty strings")
    return normalised


def _validate_row(row: tuple[Any, ...], *, market: str, requested_codes: set[str]) -> list[Any]:
    values = list(row)
    row_market, code, trade_date = values[:3]
    if row_market != market or not isinstance(row_market, str):
        raise ValueError("source row has an invalid market identity")
    if not isinstance(code, str) or code not in requested_codes:
        raise ValueError("source row has an invalid code identity")
    _canonical_date(trade_date, "source row date")
    for index, column in ((8, "volume"), (10, "transactions")):
        value = values[index]
        if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError(f"source row {column} must be an integer or null")
    if not isinstance(values[11], int) or isinstance(values[11], bool) or values[11] not in (0, 1):
        raise ValueError("source row close_missing must be 0 or 1")
    for index, column in ((12, "raw_fields_json"), (13, "fetched_at")):
        if not isinstance(values[index], str):
            raise ValueError(f"source row {column} must be a string")
    return values


def _source_uri(db_path: Path) -> str:
    return f"{db_path.resolve().as_uri()}?mode=ro"


def _read_rows(*, db_path: Path, market: str, codes: list[str], start_date: str, end_date: str, max_rows: int) -> list[list[Any]]:
    if not db_path.is_file():
        raise ValueError(f"source database does not exist: {db_path}")
    query = f"""
        SELECT {", ".join(COLUMNS)}
        FROM official_daily_price
        WHERE market = ? AND code = ? AND date >= ? AND date <= ?
        ORDER BY date
    """
    rows: list[list[Any]] = []
    requested_codes = set(codes)
    source_keys: set[tuple[str, str, str]] = set()
    with sqlite3.connect(_source_uri(db_path), uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        for code in codes:
            for source_row in connection.execute(query, (market, code, start_date, end_date)):
                row = _validate_row(source_row, market=market, requested_codes=requested_codes)
                key = (row[0], row[1], row[2])
                if key in source_keys:
                    raise ValueError(f"duplicate source key: {key}")
                source_keys.add(key)
                rows.append(row)
                if len(rows) > max_rows:
                    raise ValueError(f"selected rows exceed max_rows={max_rows}")
    return rows


def _canonical_digest(rows: list[list[Any]]) -> str:
    payload = {"columns": list(COLUMNS), "rows": rows}
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def export_official_price_slice(
    *,
    db_path: Path | str,
    market: str,
    codes: Iterable[str],
    start_date: str,
    end_date: str,
    output_dir: Path | str,
    max_rows: int = 100_000,
) -> Path:
    """Export one explicit market/code/date selection from a read-only SQLite DB."""
    if market not in VALID_MARKETS:
        raise ValueError("market must be TWSE or TPEx")
    requested_codes = _normalise_codes(codes)
    start_date = _canonical_date(start_date, "start_date")
    end_date = _canonical_date(end_date, "end_date")
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    if not isinstance(max_rows, int) or isinstance(max_rows, bool) or max_rows <= 0:
        raise ValueError("max_rows must be a positive integer")

    source_path = Path(db_path)
    destination = Path(output_dir)
    if destination.exists():
        raise FileExistsError(f"output directory already exists: {destination}")

    rows = _read_rows(
        db_path=source_path,
        market=market,
        codes=requested_codes,
        start_date=start_date,
        end_date=end_date,
        max_rows=max_rows,
    )
    selected_rows_sha256 = _canonical_digest(rows)
    observed_codes = sorted({row[1] for row in rows})
    missing_codes = [code for code in requested_codes if code not in observed_codes]

    table = pa.Table.from_pylist([dict(zip(COLUMNS, row, strict=True)) for row in rows], schema=SCHEMA)
    destination.mkdir(parents=True, exist_ok=False)
    prices_path = destination / "prices.parquet"
    with prices_path.open("xb") as prices_file:
        pq.write_table(table, prices_file)
    prices_parquet_sha256 = hashlib.sha256(prices_path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": "explicit-official-price-slice.v1",
        "source_db_path": str(source_path),
        "selection": {
            "market": market,
            "codes": requested_codes,
            "start_date": start_date,
            "end_date": end_date,
        },
        "row_count": len(rows),
        "observed_codes": observed_codes,
        "missing_codes": missing_codes,
        "columns": list(COLUMNS),
        "units": {"volume": "shares"},
        "coverage_certified": False,
        "selected_rows_sha256": selected_rows_sha256,
        "prices_parquet_sha256": prices_parquet_sha256,
    }
    with (destination / "manifest.json").open("x", encoding="utf-8") as manifest_file:
        json.dump(manifest, manifest_file, ensure_ascii=False, allow_nan=False, indent=2)
        manifest_file.write("\n")
    return destination


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", required=True, type=Path)
    parser.add_argument("--market", required=True, choices=sorted(VALID_MARKETS))
    parser.add_argument("--codes", required=True, nargs="+")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--max-rows", type=int, default=100_000)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    try:
        output_dir = export_official_price_slice(
            db_path=args.db_path,
            market=args.market,
            codes=args.codes,
            start_date=args.start_date,
            end_date=args.end_date,
            output_dir=args.output_dir,
            max_rows=args.max_rows,
        )
    except (FileExistsError, ValueError, sqlite3.Error) as error:
        raise SystemExit(str(error)) from error
    sys.stdout.write(f"{output_dir}\n")


if __name__ == "__main__":
    main()
