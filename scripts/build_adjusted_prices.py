from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path

import pandas as pd

from config import config
from shp_utils.logging_utils import setup_logging

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = config.DATA_DIR
DEFAULT_DB_PATH = config.DATA_DIR / "corporate_actions.sqlite"
DEFAULT_OUTPUT_DIR = config.DATA_DIR / "adjusted_prices" / "daily"
EVENTS_FOR_CHART_SCALE = {
    "CASH_DIVIDEND",
    "EX_RIGHT",
    "EX_RIGHT_AND_DIVIDEND",
    "ETF_SPLIT",
    "ETF_REVERSE_SPLIT",
    "CASH_CAPITAL_REDUCTION",
    "LOSS_OFFSET_CAPITAL_REDUCTION",
    "CAPITAL_REDUCTION",
}
PRICE_COLUMNS = ["Open", "High", "Low", "Close"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build adjusted daily price CSVs from raw prices and corporate actions.")
    parser.add_argument("--code", action="append", help="Stock code to build. Can be repeated.")
    parser.add_argument("--codes", help="Comma-separated stock codes to build.")
    parser.add_argument("--all", action="store_true", help="Build all *_day.csv files under data dir.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    codes = resolve_codes(args)
    if not codes:
        raise SystemExit("No codes requested. Use --code, --codes, or --all.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    events_by_code = load_corporate_actions(args.db_path, codes)
    built = 0
    for code in codes:
        source_path = args.data_dir / f"{code}_day.csv"
        if not source_path.is_file():
            logger.warning("skip missing raw CSV: %s", source_path)
            continue
        output_path = args.output_dir / f"{code}_day.csv"
        df = build_adjusted_frame(source_path, events_by_code.get(code, []))
        df.to_csv(output_path, index=False, encoding="utf-8")
        built += 1
        logger.info("built %s rows=%d events=%d", output_path, len(df), len(events_by_code.get(code, [])))
    logger.info("completed adjusted daily build: %d/%d files", built, len(codes))


def resolve_codes(args: argparse.Namespace) -> list[str]:
    codes: list[str] = []
    if args.code:
        codes.extend(args.code)
    if args.codes:
        codes.extend(args.codes.split(","))
    if args.all:
        codes.extend(path.name.split("_")[0] for path in args.data_dir.glob("*_day.csv"))
    return sorted({code.strip() for code in codes if code.strip()})


def load_corporate_actions(db_path: Path, codes: list[str]) -> dict[str, list[sqlite3.Row]]:
    if not db_path.is_file():
        raise SystemExit(f"Corporate action database not found: {db_path}")
    if not codes:
        return {}
    placeholders = ",".join("?" for _ in codes)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT code, ex_date, event_type, price_factor, evidence_status
            FROM corporate_actions
            WHERE code IN ({placeholders})
              AND price_factor IS NOT NULL
              AND event_type IN ({",".join("?" for _ in EVENTS_FOR_CHART_SCALE)})
            ORDER BY ex_date
            """,
            [*codes, *sorted(EVENTS_FOR_CHART_SCALE)],
        ).fetchall()
    events_by_code: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        events_by_code.setdefault(row["code"], []).append(row)
    return events_by_code


def build_adjusted_frame(source_path: Path, events: list[sqlite3.Row]) -> pd.DataFrame:
    df = pd.read_csv(source_path)
    df.columns = [column.strip() for column in df.columns]
    if "Date" not in df.columns:
        if "ts" not in df.columns:
            raise ValueError(f"{source_path} must contain Date or ts")
        df["Date"] = pd.to_datetime(df["ts"])
    else:
        df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)

    for column in PRICE_COLUMNS:
        if column not in df.columns:
            raise ValueError(f"{source_path} missing required column: {column}")
        df[f"Raw{column}"] = df[column]

    df["Adj_Factor"] = 1.0
    events_on_date: dict[pd.Timestamp, list[str]] = {}
    for event in events:
        event_date = pd.to_datetime(event["ex_date"])
        price_factor = event["price_factor"]
        if price_factor is None or price_factor <= 0:
            continue
        df.loc[df["Date"] < event_date, "Adj_Factor"] *= float(price_factor)
        events_on_date.setdefault(event_date, []).append(event["event_type"])

    for column in PRICE_COLUMNS:
        df[column] = (df[f"Raw{column}"] * df["Adj_Factor"]).round(4)

    df["AdjustmentEventCount"] = len(events_on_date)
    df["CorporateActionTypes"] = df["Date"].map(lambda item: ",".join(events_on_date.get(item, [])))

    ordered_columns = [
        "ts",
        "Date",
        *PRICE_COLUMNS,
        "Volume",
        "Adj_Factor",
        "RawOpen",
        "RawHigh",
        "RawLow",
        "RawClose",
        "AdjustmentEventCount",
        "CorporateActionTypes",
    ]
    existing_ordered_columns = [column for column in ordered_columns if column in df.columns]
    extra_columns = [column for column in df.columns if column not in existing_ordered_columns]
    return df.loc[:, existing_ordered_columns + extra_columns]


if __name__ == "__main__":
    setup_logging("build_adjusted_prices.log")
    main()
