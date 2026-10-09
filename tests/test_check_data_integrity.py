"""Pytest coverage for check_data_integrity.py, using a small fixture data directory.

The subject is `price_daily.parquet`, the artifact research reads, so every
fixture here builds one. See the module docstring in `check_data_integrity.py`
for why the Shioaji day CSVs stopped being the thing worth auditing.

Run with: uv run pytest tests/test_check_data_integrity.py -v
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
from check_data_integrity import MAX_EXAMPLES_PER_SYMBOL, build_report, run_health_check
from fetch_corporate_actions import init_db

# Trading days used across fixtures: Tue-Fri, then the following Mon.
D1, D2, D3, D4, D5 = "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"

STOCK = "股票"
ETF = "ETF"


def _write_price_parquet(data_dir: Path, rows: list[dict]) -> None:
    """rows: dicts of code/date/close, with optional volume and source."""
    frame = pd.DataFrame([
        {
            "ts": pd.Timestamp(row["date"]),
            "Code": row["code"],
            "Close": float(row["close"]),
            "Volume": float(row.get("volume", 1000)),
            "Source": row.get("source", "official"),
        }
        for row in rows
    ])
    frame.to_parquet(data_dir / "price_daily.parquet", index=False)


def _write_symbol_meta(data_dir: Path, symbols: list[tuple[str, str, str]]) -> None:
    """symbols: list of (code, security_category, listed_date)."""
    db_path = data_dir / "symbol_meta.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS symbol_meta (
                code TEXT PRIMARY KEY, name TEXT, isin TEXT, listed_date TEXT,
                market TEXT, industry_category TEXT, is_etf INTEGER,
                security_category TEXT, cfi_code TEXT, fetched_at TEXT
            )
            """
        )
        for code, category, listed_date in symbols:
            conn.execute(
                "INSERT OR REPLACE INTO symbol_meta (code, name, listed_date, market, is_etf, security_category) VALUES (?, ?, ?, ?, ?, ?)",
                (code, code, listed_date, "上市", 1 if category == ETF else 0, category),
            )


def _write_corporate_actions_db(data_dir: Path, events: list[tuple[str, str, str, float | None]]) -> None:
    """events: list of (code, ex_date, event_type, price_factor)."""
    db_path = data_dir / "corporate_actions.sqlite"
    init_db(db_path)
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        for code, ex_date, event_type, price_factor in events:
            conn.execute(
                """
                INSERT INTO corporate_actions (
                    source, market, code, ex_date, event_type, price_factor, detail_key, raw_fields_json, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("test", "TWSE", code, ex_date, event_type, price_factor, f"{code}-{ex_date}", "{}", fetched_at),
            )


def _write_official_daily(data_dir: Path, rows: list[tuple[str, str]]) -> None:
    """rows: list of (code, date). Only the observed window matters to the checks."""
    db_path = data_dir / "official_daily.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS official_daily_price (
                market TEXT NOT NULL, code TEXT NOT NULL, date TEXT NOT NULL,
                close REAL, volume REAL, close_missing INTEGER DEFAULT 0,
                PRIMARY KEY (market, code, date)
            )
            """
        )
        for code, date in rows:
            conn.execute(
                "INSERT OR REPLACE INTO official_daily_price (market, code, date, close, volume) VALUES (?, ?, ?, ?, ?)",
                ("TWSE", code, date, 100.0, 1000.0),
            )


def _flat_series(code: str, closes: list[tuple[str, float]], source: str = "official") -> list[dict]:
    return [{"code": code, "date": date, "close": close, "source": source} for date, close in closes]


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path


def _findings(report: dict, code: str, check: str) -> list:
    entry = report["symbols"].get(code)
    return entry[check]["examples"] if entry else []


# --- the price-limit band -------------------------------------------------


def test_a_limit_up_day_is_not_reported_as_a_data_defect(data_dir: Path):
    """The old threshold was 0.10 with `>=`, so every 漲停 was flagged. Given the
    whole point of this project is finding runaway stocks, the check was
    loudest on exactly the population it exists to serve."""
    _write_price_parquet(data_dir, _flat_series("1111", [(D1, 100.0), (D2, 110.0), (D3, 121.0)]))
    _write_symbol_meta(data_dir, [("1111", STOCK, "2000-01-01")])

    report = build_report(data_dir)

    assert _findings(report, "1111", "price_gap_without_ca_event") == []


def test_a_move_the_price_limit_cannot_explain_is_flagged(data_dir: Path):
    _write_price_parquet(data_dir, _flat_series("1111", [(D1, 100.0), (D2, 100.0), (D3, 100.0), (D4, 100.0), (D5, 100.0), ("2024-01-09", 130.0)]))
    _write_symbol_meta(data_dir, [("1111", STOCK, "2000-01-01")])

    report = build_report(data_dir)

    assert [finding["date"] for finding in _findings(report, "1111", "price_gap_without_ca_event")] == ["2024-01-09"]


def test_a_large_move_on_a_corporate_action_date_is_not_flagged(data_dir: Path):
    _write_price_parquet(data_dir, _flat_series("1111", [(D1, 100.0), (D2, 100.0), (D3, 100.0), (D4, 100.0), (D5, 100.0), ("2024-01-09", 130.0)]))
    _write_symbol_meta(data_dir, [("1111", STOCK, "2000-01-01")])
    _write_corporate_actions_db(data_dir, [("1111", "2024-01-09", "增資", 0.77)])

    report = build_report(data_dir)

    assert _findings(report, "1111", "price_gap_without_ca_event") == []


def test_an_etf_is_not_judged_against_a_band_it_may_not_trade_under(data_dir: Path):
    """Foreign-tracking ETFs have no daily limit and the metadata cannot pick
    them out, so the category is exempt rather than half-wrongly flagged."""
    _write_price_parquet(data_dir, _flat_series("0050", [(D1, 100.0), (D2, 100.0), (D3, 100.0), (D4, 100.0), (D5, 100.0), ("2024-01-09", 130.0)]))
    _write_symbol_meta(data_dir, [("0050", ETF, "2003-06-30")])

    report = build_report(data_dir)

    assert _findings(report, "0050", "price_gap_without_ca_event") == []


def test_a_symbol_with_no_metadata_is_not_asserted_against(data_dir: Path):
    """The TAIEX index is the real case: it is in the parquet, has no limit, and
    is absent from symbol_meta."""
    _write_price_parquet(data_dir, _flat_series("001", [(D1, 100.0), (D2, 100.0), (D3, 100.0), (D4, 100.0), (D5, 100.0), ("2024-01-09", 130.0)]))
    _write_symbol_meta(data_dir, [])

    report = build_report(data_dir)

    assert _findings(report, "001", "price_gap_without_ca_event") == []


def test_the_opening_sessions_of_a_listing_are_exempt(data_dir: Path):
    """Taiwan suspends the limit for a new listing's first sessions."""
    closes = [(D1, 100.0), (D2, 200.0), (D3, 300.0), (D4, 400.0), (D5, 500.0)]
    closes += [(f"2024-01-{day:02d}", 500.0) for day in (9, 10, 11)]
    _write_price_parquet(data_dir, _flat_series("1111", closes))
    _write_symbol_meta(data_dir, [("1111", STOCK, D1)])

    report = build_report(data_dir)

    assert _findings(report, "1111", "price_gap_without_ca_event") == []


def test_a_long_listed_symbol_gets_no_ipo_exemption_at_the_start_of_the_cache(data_dir: Path):
    """The exemption keys off the listing date, not position in the frame.

    Almost every symbol predates 2010-01-04 where the parquet starts, so its
    first rows there are an artefact of where the cache begins. Exempting them
    by position silently blinded the check to the earliest years of every
    long-listed symbol -- and a fixture only as long as the exemption window
    cannot tell the two rules apart.
    """
    closes = [(D1, 100.0), (D2, 130.0), (D3, 130.0), (D4, 130.0), (D5, 130.0)]
    closes += [(f"2024-01-{day:02d}", 130.0) for day in (9, 10, 11)]
    _write_price_parquet(data_dir, _flat_series("1111", closes))
    _write_symbol_meta(data_dir, [("1111", STOCK, "1962-02-09")])

    report = build_report(data_dir)

    assert [finding["date"] for finding in _findings(report, "1111", "price_gap_without_ca_event")] == [D2]


def test_a_move_after_the_opening_sessions_is_still_checked(data_dir: Path):
    """The exemption covers the opening window only, not the whole series."""
    closes = [(D1, 100.0), (D2, 100.0), (D3, 100.0), (D4, 100.0), (D5, 100.0)]
    closes += [("2024-01-09", 100.0), ("2024-01-10", 140.0)]
    _write_price_parquet(data_dir, _flat_series("1111", closes))
    _write_symbol_meta(data_dir, [("1111", STOCK, D1)])

    report = build_report(data_dir)

    assert [finding["date"] for finding in _findings(report, "1111", "price_gap_without_ca_event")] == ["2024-01-10"]


# --- corporate actions the price did not react to -------------------------


def test_a_corporate_action_with_no_price_move_is_flagged(data_dir: Path):
    _write_price_parquet(data_dir, _flat_series("1111", [(D1, 100.0), (D2, 100.0), (D3, 100.0)]))
    _write_symbol_meta(data_dir, [("1111", STOCK, "2000-01-01")])
    _write_corporate_actions_db(data_dir, [("1111", D2, "減資", 0.5)])

    report = build_report(data_dir)

    assert [finding["date"] for finding in _findings(report, "1111", "ca_event_without_price_gap")] == [D2]


def test_a_small_corporate_action_is_not_expected_to_move_the_price(data_dir: Path):
    _write_price_parquet(data_dir, _flat_series("1111", [(D1, 100.0), (D2, 100.0), (D3, 100.0)]))
    _write_symbol_meta(data_dir, [("1111", STOCK, "2000-01-01")])
    _write_corporate_actions_db(data_dir, [("1111", D2, "除息", 0.98)])

    report = build_report(data_dir)

    assert _findings(report, "1111", "ca_event_without_price_gap") == []


# --- the fallback rows research actually reads ----------------------------


def test_a_fallback_day_inside_officials_own_window_is_flagged(data_dir: Path):
    """Official carries this symbol on either side, so a day it lacks is a real
    gap rather than coverage it has not reached."""
    _write_price_parquet(
        data_dir,
        [
            {"code": "1111", "date": D1, "close": 100.0, "source": "official"},
            {"code": "1111", "date": D2, "close": 100.0, "source": "shioaji"},
            {"code": "1111", "date": D3, "close": 100.0, "source": "official"},
        ],
    )
    _write_symbol_meta(data_dir, [("1111", STOCK, "2000-01-01")])
    _write_official_daily(data_dir, [("1111", D1), ("1111", D3)])

    report = build_report(data_dir)

    assert _findings(report, "1111", "unexplained_fallback") == [D2]


def test_a_fallback_day_outside_officials_window_is_coverage_not_a_defect(data_dir: Path):
    """182,427 rows of the live cache are exactly this, and every one of them
    lies outside official's window -- measured 2026-09-02."""
    _write_price_parquet(
        data_dir,
        [
            {"code": "1111", "date": D1, "close": 100.0, "source": "shioaji"},
            {"code": "1111", "date": D2, "close": 100.0, "source": "official"},
            {"code": "1111", "date": D3, "close": 100.0, "source": "official"},
        ],
    )
    _write_symbol_meta(data_dir, [("1111", STOCK, "2000-01-01")])
    _write_official_daily(data_dir, [("1111", D2), ("1111", D3)])

    report = build_report(data_dir)

    assert "1111" not in report["symbols"]
    assert report["coverage"]["fallback_row_count"] == 1
    assert report["coverage"]["fallback_rows_outside_official_window"] == 1


def test_a_symbol_official_has_never_carried_is_not_flagged(data_dir: Path):
    _write_price_parquet(data_dir, _flat_series("9999", [(D1, 100.0), (D2, 100.0)], source="shioaji"))
    _write_symbol_meta(data_dir, [("9999", STOCK, "2000-01-01")])
    _write_official_daily(data_dir, [])

    report = build_report(data_dir)

    assert "9999" not in report["symbols"]
    assert report["coverage"]["fallback_rows_outside_official_window"] == 2


# --- report shape ---------------------------------------------------------


def test_only_symbols_with_a_finding_are_listed(data_dir: Path):
    """A report nobody can read is not a report. The clean symbols are counted
    in `coverage`, not enumerated."""
    rows = _flat_series("1111", [(D1, 100.0), (D2, 100.0), (D3, 100.0)])
    rows += _flat_series("2222", [(D1, 100.0), (D2, 100.0), (D3, 100.0)])
    _write_price_parquet(data_dir, rows)
    _write_symbol_meta(data_dir, [("1111", STOCK, "2000-01-01"), ("2222", STOCK, "2000-01-01")])
    _write_corporate_actions_db(data_dir, [("1111", D2, "減資", 0.5)])

    report = build_report(data_dir)

    assert set(report["symbols"]) == {"1111"}
    assert report["coverage"]["symbol_count"] == 2


def test_report_has_stable_top_level_schema(data_dir: Path):
    _write_price_parquet(data_dir, _flat_series("1111", [(D1, 100.0), (D2, 100.0)]))
    _write_symbol_meta(data_dir, [("1111", STOCK, "2000-01-01")])

    report = build_report(data_dir)

    assert report["schema_version"] == 3
    assert report["subject"] == "price_daily.parquet"
    assert set(report) == {"schema_version", "generated_at", "subject", "coverage", "checks", "symbols"}
    assert set(report["checks"]) == {"price_gap_without_ca_event", "ca_event_without_price_gap", "unexplained_fallback"}
    for check in report["checks"].values():
        assert {"symbol_count", "finding_count"} <= set(check)


def test_coverage_reports_the_source_split_and_the_latest_day(data_dir: Path):
    """The newest bar is provisional while official lags a trading day, so the
    share of it that is official is worth stating."""
    _write_price_parquet(
        data_dir,
        [
            {"code": "1111", "date": D1, "close": 100.0, "source": "official"},
            {"code": "1111", "date": D2, "close": 100.0, "source": "shioaji"},
            {"code": "2222", "date": D2, "close": 100.0, "source": "official"},
        ],
    )
    _write_symbol_meta(data_dir, [])

    coverage = build_report(data_dir)["coverage"]

    assert coverage["rows_by_source"] == {"official": 2, "shioaji": 1}
    assert coverage["trading_day_count"] == 2
    assert coverage["latest_day_row_count"] == 2
    assert coverage["latest_day_official_share"] == 0.5


def test_a_missing_price_cache_yields_an_empty_report_rather_than_an_error(data_dir: Path):
    report = build_report(data_dir)

    assert report["schema_version"] == 3
    assert report["symbols"] == {}
    assert report["checks"]["price_gap_without_ca_event"]["finding_count"] == 0


def test_run_health_check_writes_report_file(data_dir: Path):
    _write_price_parquet(data_dir, _flat_series("1111", [(D1, 100.0), (D2, 100.0)]))
    _write_symbol_meta(data_dir, [("1111", STOCK, "2000-01-01")])

    output_path = run_health_check(data_dir)

    assert output_path.is_file()
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["schema_version"] == 3
    assert written["coverage"]["row_count"] == 2


def test_per_symbol_examples_are_capped(data_dir: Path):
    """A symbol with hundreds of findings must not make the file unreadable.

    More events than MAX_EXAMPLES_PER_SYMBOL, so the full count is still
    reported while the listing itself stays bounded.
    """
    closes = [(f"2024-{month:02d}-{day:02d}", 100.0) for month in (1, 2, 3) for day in range(1, 11)]
    _write_price_parquet(data_dir, _flat_series("1111", closes))
    _write_symbol_meta(data_dir, [("1111", STOCK, "2000-01-01")])
    _write_corporate_actions_db(data_dir, [("1111", date, "減資", 0.5) for date, _close in closes])

    report = build_report(data_dir)
    entry = report["symbols"]["1111"]["ca_event_without_price_gap"]

    assert entry["count"] == 30
    assert len(entry["examples"]) == MAX_EXAMPLES_PER_SYMBOL
