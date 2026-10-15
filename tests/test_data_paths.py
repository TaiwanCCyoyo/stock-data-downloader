"""Every default data path must anchor to the repository root, not the CWD.

Two conventions used to coexist: some modules resolved "data/" relative to the
current working directory, others relative to their own file. They agreed only
because the scripts sat in the repository root and were run from there. Anything
that breaks that coincidence -- running from another directory, or moving the
scripts into a subdirectory -- would have sent half the pipeline to one "data"
directory and half to another, silently, with no error to notice.
"""

from __future__ import annotations

import os
from pathlib import Path

import backfill_official_history
import build_adjusted_prices
import build_price_parquet
import check_data_integrity
import fetch_corporate_actions
import fetch_dividends
import fetch_institutional
import fetch_monthly_revenue
import fetch_official_daily_price
import fetch_symbol_meta
import fetch_value_chain_classification
import pytest

from config import config

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_PATHS = {
    "config.DATA_DIR": config.DATA_DIR,
    "config.STOCK_SYMBOL_MAPPING": config.STOCK_SYMBOL_MAPPING,
    "build_price_parquet.DEFAULT_DATA_PATH": build_price_parquet.DEFAULT_DATA_PATH,
    "check_data_integrity.DEFAULT_DATA_PATH": check_data_integrity.DEFAULT_DATA_PATH,
    "fetch_corporate_actions.DEFAULT_DB_PATH": fetch_corporate_actions.DEFAULT_DB_PATH,
    "fetch_corporate_actions.DEFAULT_RAW_DIR": fetch_corporate_actions.DEFAULT_RAW_DIR,
    "fetch_institutional.DEFAULT_DB_PATH": fetch_institutional.DEFAULT_DB_PATH,
    "fetch_institutional.DEFAULT_RAW_DIR": fetch_institutional.DEFAULT_RAW_DIR,
    "fetch_institutional.DEFAULT_SYMBOL_META_PATH": fetch_institutional.DEFAULT_SYMBOL_META_PATH,
    "fetch_official_daily_price.DEFAULT_DB_PATH": fetch_official_daily_price.DEFAULT_DB_PATH,
    "fetch_monthly_revenue.DEFAULT_DB_PATH": fetch_monthly_revenue.DEFAULT_DB_PATH,
    "fetch_monthly_revenue.DEFAULT_RAW_DIR": fetch_monthly_revenue.DEFAULT_RAW_DIR,
    "fetch_symbol_meta.DEFAULT_DB_PATH": fetch_symbol_meta.DEFAULT_DB_PATH,
    "fetch_symbol_meta.DEFAULT_RAW_DIR": fetch_symbol_meta.DEFAULT_RAW_DIR,
    "fetch_value_chain_classification.DEFAULT_DATA_DIR": fetch_value_chain_classification.DEFAULT_DATA_DIR,
    "fetch_value_chain_classification.DEFAULT_LATEST_PATH": fetch_value_chain_classification.DEFAULT_LATEST_PATH,
    "fetch_value_chain_classification.DEFAULT_HISTORY_DIR": fetch_value_chain_classification.DEFAULT_HISTORY_DIR,
    "build_adjusted_prices.DEFAULT_DB_PATH": build_adjusted_prices.DEFAULT_DB_PATH,
    "build_adjusted_prices.DEFAULT_DATA_DIR": build_adjusted_prices.DEFAULT_DATA_DIR,
    "build_adjusted_prices.DEFAULT_OUTPUT_DIR": build_adjusted_prices.DEFAULT_OUTPUT_DIR,
    "backfill_official_history.DEFAULT_LOCK_PATH": backfill_official_history.DEFAULT_LOCK_PATH,
    "fetch_dividends.DATA_DIR": fetch_dividends.DATA_DIR,
    "fetch_dividends.DIV_DIR": fetch_dividends.DIV_DIR,
}


def test_no_module_defines_a_cwd_relative_data_path() -> None:
    """Catch a new `Path("data/...")` before it can split the data directory.

    The dict above only covers constants someone remembered to list; this
    reads the source, so a path added later is caught even if nobody updates
    the test. Both omissions have already happened once.
    """
    offenders = []
    for source in sorted((REPO_ROOT / "scripts").glob("*.py")):
        for lineno, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if 'Path("data/' in stripped or '= "data"' in stripped or '= "data/' in stripped:
                offenders.append(f"{source.name}:{lineno}: {stripped}")
    assert not offenders, "CWD-relative data paths must go through config.DATA_DIR:\n" + "\n".join(offenders)


def test_no_module_creates_directories_at_import_time() -> None:
    """Importing a pipeline module must not touch the filesystem.

    fetch_dividends used to call os.makedirs at module level, so merely
    importing it -- as the test suite does -- created data/dividends in the
    live cache. Side effects belong in the code path that actually writes.
    """
    offenders = []
    for source in sorted((REPO_ROOT / "scripts").glob("*.py")):
        for lineno, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            if line[:1].isspace() or line.strip().startswith("#"):
                continue  # indented: inside a function, which is fine
            if "makedirs(" in line or ".mkdir(" in line:
                offenders.append(f"{source.name}:{lineno}: {line.strip()}")
    assert not offenders, "directory creation at import time:\n" + "\n".join(offenders)


def test_every_default_path_is_absolute() -> None:
    for name, value in DEFAULT_PATHS.items():
        assert Path(value).is_absolute(), f"{name} is relative to the CWD: {value}"


def test_every_default_path_is_under_the_repository_root() -> None:
    """Unless the data directory is deliberately configured elsewhere; see test_data_dir_config."""
    if config.DATA_DIR_CONFIGURED:
        pytest.skip(f"{config.DATA_DIR_ENV} selects {config.DATA_DIR} ({config.DATA_DIR_SOURCE})")
    for name, value in DEFAULT_PATHS.items():
        resolved = Path(value).resolve()
        assert REPO_ROOT in resolved.parents or resolved == REPO_ROOT, f"{name} escapes the repository root: {resolved}"


def test_every_default_path_lives_under_the_one_data_directory() -> None:
    data_dir = Path(config.DATA_DIR).resolve()
    for name, value in DEFAULT_PATHS.items():
        resolved = Path(value).resolve()
        assert resolved == data_dir or data_dir in resolved.parents, f"{name} resolves outside {data_dir}: {resolved}"


def test_the_tracked_category_table_stays_in_the_checkout() -> None:
    """It ships with the code, so it must not move with a configured DATA_DIR."""
    assert config.STOCK_CATEGORY == REPO_ROOT / "data" / "stock_category.json5"
    assert config.STOCK_CATEGORY.is_file()


def test_no_entry_point_searches_upward_for_an_env_file() -> None:
    """A bare load_dotenv() walks up from the script, so a worktree nested under
    the primary checkout would load the primary's production settings."""
    offenders = []
    for source in sorted((REPO_ROOT / "scripts").glob("*.py")):
        for lineno, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            if "load_dotenv()" in line or "find_dotenv(" in line:
                offenders.append(f"{source.name}:{lineno}: {line.strip()}")
    assert not offenders, "load the checkout's own config.ENV_FILE explicitly:\n" + "\n".join(offenders)


def test_paths_do_not_move_when_the_working_directory_changes(tmp_path: Path) -> None:
    """The property that lets these scripts be invoked from anywhere."""
    before = {name: Path(value).resolve() for name, value in DEFAULT_PATHS.items()}

    original = Path.cwd()
    try:
        os.chdir(tmp_path)
        after = {name: Path(value).resolve() for name, value in DEFAULT_PATHS.items()}
    finally:
        os.chdir(original)

    assert after == before
