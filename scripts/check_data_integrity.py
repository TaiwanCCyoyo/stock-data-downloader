"""
Check Data Integrity Module

Audits `data/price_daily.parquet` -- the artifact research actually reads -- and
writes a stable JSON report (`data/data_quality_report.json`) that a caller can
parse without depending on this pipeline's internals.

This is step 9 of the `run_daily.py` one-click refresh, but can also be run
standalone.

Why the parquet and not the `{code}_day.csv` files
--------------------------------------------------
Until 2026-09-02 every check here read the Shioaji day CSVs. That made sense
while they were the price source. They are not: since 2026-08-30 the official
feed builds `price_daily.parquet`, which carries 6,350,534 official rows against
182,427 Shioaji ones. Auditing the CSVs had three consequences.

It flagged 2,593 of 2,603 symbols, which is not a report anyone reads. Almost
all of it was structural: 42,051 "present in official, absent from Shioaji"
rows, in that one direction only, which is the design rather than a defect.

It was blind to 2010-2018. The CSVs start 2019-01-23; the parquet starts
2010-01-04.

And the two-source close/volume comparison could only ever examine discarded
data. `build_price_parquet` merges by `(Code, Date)` with official winning, so a
row is `Source == "shioaji"` exactly when official has nothing for that key --
verified, zero overlap. Where the two sources disagreed, research read
official's number and the Shioaji one was thrown away; where research reads a
Shioaji number, there is no official number to compare it against. So the
question that check was asking had no bearing on the artifact.

What replaces it is `unexplained_fallback`, which asks the one question about
those 182,427 rows that is answerable: is the day inside the window official
does cover for that symbol? Outside it, the fallback is extending reach official
has not got to, which is expected and is reported as a coverage number. Inside
it, official has the symbol and lacks a day it should have -- the seam a
gap-selecting backfill leaves behind, per `data-structures.md`. Measured
2026-09-02, that is zero across all 2,070 symbols.

Together these took the report from flagging 2,593 of 2,603 symbols to 714 of
2,070, and the written file from 25.9MB to 509KB.

On cost, use the numbers from the daily run rather than a standalone one: the
CSV version took 16m03s in step 9 on 2026-09-01, and the first parquet version
took 5m55s on 2026-09-02. A standalone build measured about thirty seconds at
the time, which was a warm-cache figure and not what the pipeline saw. Two
fixes since then -- asking for official's windows per code instead of one
`GROUP BY` over 6.35M rows, and computing `pct_change` once for the frame
instead of twice per symbol -- took a standalone build from 92.9s to 53.5s.
Their effect on the daily run has not been observed yet.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import cast

import pandas as pd

from config import config
from shp_utils.logging_utils import setup_logging

logger = logging.getLogger(__name__)

REPORT_SCHEMA_VERSION = 3
DEFAULT_DATA_PATH = config.DATA_DIR

# Taiwan enforces a +-10% daily price-limit band, so a limit-up day lands at
# exactly the old threshold of 0.10 and `>=` flagged every single one: 65.9% of
# the 8,167 gap flags in the 2026-09-01 report sat in [0.095, 0.105]. A limit
# move is the most ordinary thing a runaway stock does, so the check was loudest
# on exactly the population this project exists to find. The band is widened
# past the limit, with headroom for the tick rounding that lets a low-priced
# stock's limit close land slightly above a clean 10%.
PRICE_GAP_THRESHOLD = 0.105
# Only corporate actions whose own expected price impact clears the band are
# checked against observed price behaviour, so both directions compare
# like-for-like magnitudes.
CA_FACTOR_THRESHOLD = 0.10

# Instruments the price-limit band does not apply to, which therefore cannot be
# judged against it. ETFs tracking foreign indices trade without a limit and the
# available metadata cannot separate them from domestic-tracking ones, so the
# whole category is exempt rather than half-wrongly flagged. A symbol absent
# from `symbol_meta.sqlite` -- the TAIEX index among them -- is exempt for the
# same reason: unknown, so not asserted against.
LIMIT_BOUND_CATEGORIES = ("股票", "創新板")
# Taiwan suspends the price limit for a newly listed symbol's opening sessions.
IPO_UNLIMITED_SESSIONS = 5

# Per symbol, and per check, how many dated examples the report carries. The
# point of the report is that a human reads it; a full enumeration is what the
# previous schema did and why nobody could.
MAX_EXAMPLES_PER_SYMBOL = 20


def _load_price_frame(data_dir: Path) -> pd.DataFrame | None:
    """Load the columns of `price_daily.parquet` the checks need."""
    parquet_path = data_dir / "price_daily.parquet"
    if not parquet_path.is_file():
        logger.warning("Price cache not found at %s; the report will be empty.", parquet_path)
        return None
    frame = pd.read_parquet(parquet_path, columns=["ts", "Code", "Close", "Volume", "Source"])
    frame["Date"] = pd.to_datetime(frame["ts"]).dt.normalize()
    frame["Code"] = frame["Code"].astype(str)
    frame = frame.sort_values(["Code", "Date"], kind="stable").reset_index(drop=True)
    # Computed once for every row here rather than per symbol in each check.
    frame["Return"] = frame.groupby("Code", sort=False)["Close"].pct_change()
    return frame


def _load_symbol_meta(data_dir: Path) -> dict[str, dict]:
    """Load per-symbol category and listing date from `symbol_meta.sqlite`."""
    db_path = data_dir / "symbol_meta.sqlite"
    if not db_path.is_file():
        logger.warning("Symbol metadata not found at %s; no symbol will be treated as price-limit bound.", db_path)
        return {}
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT code, security_category, listed_date FROM symbol_meta").fetchall()
    return {str(code): {"security_category": category, "listed_date": pd.to_datetime(listed_date, errors="coerce")} for code, category, listed_date in rows}


def _load_corporate_action_index(db_path: Path) -> dict[str, list[tuple[str, float | None]]]:
    """Load (ex_date, price_factor) pairs per code from `corporate_actions.sqlite`."""
    if not db_path.is_file():
        logger.warning("Corporate-action database not found at %s; CA cross-check will be skipped.", db_path)
        return {}
    by_code: dict[str, list[tuple[str, float | None]]] = defaultdict(list)
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT code, ex_date, price_factor FROM corporate_actions").fetchall()
    for code, ex_date, price_factor in rows:
        by_code[str(code)].append((str(ex_date), price_factor))
    return dict(by_code)


def _load_official_windows(db_path: Path, codes: Iterable[str]) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    """Per code, the first and last date official actually carries a row for.

    Deliberately not `official_history_raw_ranges`. A month there can be
    `status='ok'` with `row_count=0` -- 51,898 of 280,084 ranges are, across 609
    codes -- because the per-code backfill asks one market for a code's whole
    history, and a symbol that transferred between TPEx and TWSE genuinely has
    no rows in its current market for the era before the move. Reading those as
    "official was asked and had nothing" flagged 69,977 rows that are not
    defects.

    The observed window is assertable where the range table is not: inside it
    official demonstrably carries the symbol, so a day it lacks is a real gap.

    Asked per code rather than as one `GROUP BY code`. The grouped form is a
    covering-index SCAN of all 6.35M rows and answers for 51,066 codes, most of
    them warrants the parquet has never heard of; the per-code form is an
    indexed SEARCH, and only 2,070 codes are ever asked about. Measured 50s
    against 1s, and step 9 of the daily run spent most of its time here.
    """
    if not db_path.is_file():
        logger.warning("Official daily database not found at %s; fallback rows cannot be explained.", db_path)
        return {}
    windows: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {}
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        for code in codes:
            first, last = conn.execute(
                "SELECT min(date), max(date) FROM official_daily_price WHERE code = ?",
                (code,),
            ).fetchone()
            if first and last:
                windows[str(code)] = (cast(pd.Timestamp, pd.Timestamp(first)), cast(pd.Timestamp, pd.Timestamp(last)))
    return windows


def _limit_bound_codes(meta: dict[str, dict]) -> set[str]:
    return {code for code, entry in meta.items() if entry.get("security_category") in LIMIT_BOUND_CATEGORIES}


def _ipo_exempt_through(frame: pd.DataFrame, listed_date: pd.Timestamp | None) -> pd.Timestamp | None:
    """Last date of the opening sessions a new listing trades without a limit.

    Only when the listing itself falls inside the cache. Almost every symbol
    predates 2010-01-04, where the parquet starts, and its first rows there are
    an artefact of where the cache begins rather than an IPO -- exempting them
    would silently blind the check to the earliest years of every long-listed
    symbol.
    """
    if listed_date is None or frame.empty or listed_date < frame["Date"].iloc[0]:
        return None
    sessions = frame.loc[frame["Date"] >= listed_date, "Date"]
    if sessions.empty:
        return None
    return sessions.iloc[min(IPO_UNLIMITED_SESSIONS, len(sessions)) - 1]


def _price_gap_findings(
    frame: pd.DataFrame,
    ca_dates: set[str],
    *,
    is_limit_bound: bool,
    listed_date: pd.Timestamp | None,
) -> list[dict]:
    """Day-over-day close moves too large for the price-limit band to explain.

    Reads the precomputed ``Return`` column rather than calling ``pct_change``
    per group: with 2,070 groups the per-call pandas overhead dominated, and
    this function and the corporate-action one were each paying it separately.
    """
    if not is_limit_bound:
        return []
    moves = frame.loc[frame["Return"].abs() > PRICE_GAP_THRESHOLD, ["Date", "Return"]]
    if moves.empty:
        return []
    exempt_through = _ipo_exempt_through(frame, listed_date)
    findings = []
    for report_date, value in zip(moves["Date"], moves["Return"], strict=True):
        if exempt_through is not None and listed_date <= report_date <= exempt_through:
            continue
        date_str = report_date.strftime("%Y-%m-%d")
        if date_str in ca_dates:
            continue
        findings.append({"date": date_str, "return": round(float(value), 4)})
    return findings


def _ca_without_gap_findings(
    frame: pd.DataFrame,
    ca_events: list[tuple[str, float | None]],
) -> list[dict]:
    """Corporate actions big enough to move the price, where it did not move."""
    relevant = [(ex_date, factor) for ex_date, factor in ca_events if factor is not None and abs(1.0 - factor) >= CA_FACTOR_THRESHOLD]
    if not relevant:
        return []
    moved = {report_date.strftime("%Y-%m-%d") for report_date in frame.loc[frame["Return"].abs() > PRICE_GAP_THRESHOLD, "Date"]}
    dates_in_frame = {report_date.strftime("%Y-%m-%d") for report_date in frame["Date"]}
    findings = []
    for ex_date, price_factor in relevant:
        if ex_date not in dates_in_frame or ex_date in moved:
            continue
        findings.append({"date": ex_date, "expected_factor": round(float(price_factor), 4)})
    return sorted(findings, key=lambda finding: finding["date"])


def _unexplained_fallback_findings(
    frame: pd.DataFrame,
    window: tuple[pd.Timestamp, pd.Timestamp] | None,
) -> list[str]:
    """Shioaji-sourced days inside the window official does cover for this symbol.

    Research reads these values. Outside official's window the fallback is
    simply extending coverage official has not reached, which is expected and is
    reported as a coverage statistic instead. Inside it, official has the symbol
    and lacks a day it should have -- the seam a gap-selecting backfill leaves
    behind, which `data-structures.md` documents. Measured 2026-09-02 this is
    zero across all 2,070 symbols, which is what the check looks like when it
    passes.
    """
    if window is None:
        return []
    first, last = window
    fallback = frame.loc[frame["Source"] == "shioaji", "Date"]
    inside = fallback[(fallback >= first) & (fallback <= last)]
    return sorted({report_date.strftime("%Y-%m-%d") for report_date in inside})


def _coverage(frame: pd.DataFrame, official_windows: dict[str, tuple[pd.Timestamp, pd.Timestamp]]) -> dict:
    """Statistics about the artifact, reported as numbers rather than flags."""
    by_source = frame["Source"].value_counts().to_dict()
    fallback = frame.loc[frame["Source"] == "shioaji"]
    dates = frame["Date"]
    latest = dates.max()
    latest_rows = frame[frame["Date"] == latest]
    latest_official = int((latest_rows["Source"] == "official").sum())
    fallback_only = set(fallback["Code"]) - set(frame.loc[frame["Source"] == "official", "Code"])
    # How much of the fallback is official extending its reach rather than
    # missing something, so the raw fallback count is not read as a defect count.
    outside_official_window = 0
    for code, group in fallback.groupby("Code"):
        window = official_windows.get(str(code))
        if window is None:
            outside_official_window += len(group)
            continue
        first, last = window
        outside_official_window += int(((group["Date"] < first) | (group["Date"] > last)).sum())
    return {
        "first_date": dates.min().strftime("%Y-%m-%d"),
        "last_date": latest.strftime("%Y-%m-%d"),
        "trading_day_count": int(dates.nunique()),
        "saturday_session_count": int(pd.Series(dates.unique()).dt.dayofweek.eq(5).sum()),
        "symbol_count": int(frame["Code"].nunique()),
        "row_count": int(len(frame)),
        "rows_by_source": {str(source): int(count) for source, count in by_source.items()},
        "fallback_row_count": int(len(fallback)),
        "fallback_symbol_count": int(fallback["Code"].nunique()),
        "fallback_only_symbol_count": len(fallback_only),
        "fallback_rows_outside_official_window": outside_official_window,
        "latest_day_row_count": int(len(latest_rows)),
        "latest_day_official_share": round(latest_official / len(latest_rows), 4) if len(latest_rows) else None,
    }


CHECK_NAMES = ("price_gap_without_ca_event", "ca_event_without_price_gap", "unexplained_fallback")


def build_report(data_dir: Path | str) -> dict:
    """Scan `data_dir` and build the data-quality report as a plain dict."""
    data_dir = Path(data_dir)
    frame = _load_price_frame(data_dir)
    generated_at = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    if frame is None or frame.empty:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "generated_at": generated_at,
            "subject": "price_daily.parquet",
            "coverage": {},
            "checks": {name: {"symbol_count": 0, "finding_count": 0} for name in CHECK_NAMES},
            "symbols": {},
        }

    meta = _load_symbol_meta(data_dir)
    limit_bound = _limit_bound_codes(meta)
    listed_dates = {code: entry["listed_date"] for code, entry in meta.items() if pd.notna(entry["listed_date"])}
    ca_index = _load_corporate_action_index(data_dir / "corporate_actions.sqlite")
    official_windows = _load_official_windows(data_dir / "official_daily.sqlite", frame["Code"].unique())

    symbols: dict[str, dict] = {}
    totals = {name: {"symbol_count": 0, "finding_count": 0} for name in CHECK_NAMES}
    for code, group in frame.groupby("Code", sort=True):
        ca_events = ca_index.get(str(code), [])
        findings: dict[str, list[dict] | list[str]] = {
            "price_gap_without_ca_event": _price_gap_findings(
                group,
                {ex_date for ex_date, _factor in ca_events},
                is_limit_bound=code in limit_bound,
                listed_date=listed_dates.get(str(code)),
            ),
            "ca_event_without_price_gap": _ca_without_gap_findings(group, ca_events),
            "unexplained_fallback": _unexplained_fallback_findings(group, official_windows.get(str(code))),
        }
        if not any(findings.values()):
            continue
        entry: dict = {}
        for name, values in findings.items():
            totals[name]["finding_count"] += len(values)
            if values:
                totals[name]["symbol_count"] += 1
            entry[name] = {"count": len(values), "examples": values[:MAX_EXAMPLES_PER_SYMBOL]}
        symbols[str(code)] = entry

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": generated_at,
        "subject": "price_daily.parquet",
        "coverage": _coverage(frame, official_windows),
        "checks": {
            "price_gap_without_ca_event": {
                "description": "close move beyond the daily price-limit band with no corporate action on that date",
                "threshold": PRICE_GAP_THRESHOLD,
                **totals["price_gap_without_ca_event"],
            },
            "ca_event_without_price_gap": {
                "description": "corporate action expected to reset the reference price, where the close did not move",
                "threshold": CA_FACTOR_THRESHOLD,
                **totals["ca_event_without_price_gap"],
            },
            "unexplained_fallback": {
                "description": "row served by the Shioaji fallback inside official's own covered window for that symbol",
                **totals["unexplained_fallback"],
            },
        },
        "symbols": symbols,
    }


def write_report(report: dict, data_dir: Path | str) -> Path:
    output_path = Path(data_dir) / "data_quality_report.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def run_health_check(data_dir: Path | str = DEFAULT_DATA_PATH) -> Path:
    """Build and write the data-quality report; returns the output path."""
    report = build_report(data_dir)
    output_path = write_report(report, data_dir)
    coverage = report["coverage"]
    logger.info(
        "Wrote data quality report to %s (%s rows over %s symbols, %d flagged; %s)",
        output_path,
        coverage.get("row_count", 0),
        coverage.get("symbol_count", 0),
        len(report["symbols"]),
        ", ".join(f"{name}={report['checks'][name]['symbol_count']} symbols" for name in CHECK_NAMES),
    )
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-path",
        default=str(DEFAULT_DATA_PATH),
        help="Directory containing price_daily.parquet (default: data/)",
    )
    args = parser.parse_args()
    run_health_check(Path(args.data_path))


if __name__ == "__main__":
    setup_logging("check_data_integrity.log")
    main()
