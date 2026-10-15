"""The live data directory can sit outside the checkout, configured explicitly.

The cases that matter are the ones that must not happen silently: a bad value
falling back to `<repo>/data` (and re-downloading into it), and a development
worktree nested under the primary checkout picking up the primary's `.env`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from config import config
from config.config import DATA_DIR_ENV, DataDirConfigError, resolve_data_dir

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    root.mkdir()
    return root


@pytest.fixture()
def external(tmp_path: Path) -> Path:
    root = tmp_path / "stock-data"
    root.mkdir()
    return root


def test_unset_means_the_in_repository_default(checkout: Path) -> None:
    assert resolve_data_dir({}, checkout) == (checkout / "data", "default")


def test_environment_selects_an_external_directory(checkout: Path, external: Path) -> None:
    assert resolve_data_dir({DATA_DIR_ENV: str(external)}, checkout) == (external.resolve(), "environment")


def test_the_checkouts_own_env_file_selects_it(checkout: Path, external: Path) -> None:
    (checkout / ".env").write_text(f"API_KEY=x\n{DATA_DIR_ENV}={external.as_posix()}\n", encoding="utf-8")

    path, source = resolve_data_dir({}, checkout)

    assert path == external.resolve()
    assert source == str(checkout / ".env")


def test_the_environment_wins_over_the_env_file(checkout: Path, external: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (checkout / ".env").write_text(f"{DATA_DIR_ENV}={other}\n", encoding="utf-8")

    assert resolve_data_dir({DATA_DIR_ENV: str(external)}, checkout)[0] == external.resolve()


def test_a_nested_worktree_does_not_inherit_the_primary_env_file(checkout: Path, external: Path) -> None:
    """Primary producer worktrees live at `<primary>/.worktrees/<name>/`."""
    (checkout / ".env").write_text(f"{DATA_DIR_ENV}={external}\n", encoding="utf-8")
    worktree = checkout / ".worktrees" / "task"
    worktree.mkdir(parents=True)

    assert resolve_data_dir({}, worktree) == (worktree / "data", "default")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "relative/data",
        pytest.param("D:relative", marks=pytest.mark.skipif(os.name != "nt", reason="drive-relative paths are Windows-only")),
        pytest.param("\\rooted-without-drive", marks=pytest.mark.skipif(os.name != "nt", reason="rootless paths are Windows-only")),
    ],
)
def test_a_non_absolute_value_fails_without_fallback(checkout: Path, value: str) -> None:
    with pytest.raises(DataDirConfigError):
        resolve_data_dir({DATA_DIR_ENV: value}, checkout)
    assert not (checkout / "data").exists()


def test_a_missing_directory_fails_and_is_not_created(checkout: Path, tmp_path: Path) -> None:
    missing = tmp_path / "typo"

    with pytest.raises(DataDirConfigError, match="not an existing directory"):
        resolve_data_dir({DATA_DIR_ENV: str(missing)}, checkout)
    assert not missing.exists()


def test_a_file_is_not_a_data_directory(checkout: Path, tmp_path: Path) -> None:
    file = tmp_path / "file"
    file.write_text("", encoding="utf-8")

    with pytest.raises(DataDirConfigError, match="not an existing directory"):
        resolve_data_dir({DATA_DIR_ENV: str(file)}, checkout)


def test_a_bad_env_file_value_also_fails(checkout: Path) -> None:
    (checkout / ".env").write_text(f"{DATA_DIR_ENV}=data\n", encoding="utf-8")

    with pytest.raises(DataDirConfigError, match=str(checkout / ".env").replace("\\", "\\\\")):
        resolve_data_dir({}, checkout)


def test_a_directory_overlapping_the_checkout_is_refused(checkout: Path) -> None:
    inside = checkout / "data"
    inside.mkdir()

    for value in (inside, checkout, checkout.parent):
        with pytest.raises(DataDirConfigError, match="overlaps the checkout"):
            resolve_data_dir({DATA_DIR_ENV: str(value)}, checkout)


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions are Windows-only")
def test_a_junction_is_refused(checkout: Path, external: Path, tmp_path: Path) -> None:
    import _winapi

    junction = tmp_path / "junction"
    _winapi.CreateJunction(str(external), str(junction))
    try:
        for value in (junction, junction / "nested"):
            (external / "nested").mkdir(exist_ok=True)
            with pytest.raises(DataDirConfigError, match="symlink or junction"):
                resolve_data_dir({DATA_DIR_ENV: str(value)}, checkout)
    finally:
        os.rmdir(junction)  # removes the junction itself, never its target
    assert external.is_dir()


def test_ensure_data_dir_never_creates_a_configured_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "gone"
    monkeypatch.setattr(config, "DATA_DIR", missing)
    monkeypatch.setattr(config, "DATA_DIR_CONFIGURED", True)

    with pytest.raises(DataDirConfigError, match="no longer exists"):
        config.ensure_data_dir()
    assert not missing.exists()


def test_ensure_data_dir_creates_the_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    default = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", default)
    monkeypatch.setattr(config, "DATA_DIR_CONFIGURED", False)

    assert config.ensure_data_dir() == default
    assert default.is_dir()


_PROBE = """
import json, sys
sys.path[:0] = [sys.argv[1], sys.argv[1] + "/scripts"]
import backfill_official_history, build_adjusted_prices, build_price_parquet, check_data_integrity
import fetch_corporate_actions, fetch_dividends, fetch_institutional, fetch_monthly_revenue
import fetch_official_daily_price, fetch_symbol_meta, fetch_value_chain_classification
from config import config
from shp_utils import mops_dividend_evidence, mops_material_evidence
paths = {
    "config.DATA_DIR": config.DATA_DIR,
    "config.STOCK_SYMBOL_MAPPING": config.STOCK_SYMBOL_MAPPING,
    "backfill_official_history.DEFAULT_LOCK_PATH": backfill_official_history.DEFAULT_LOCK_PATH,
    "build_adjusted_prices.DEFAULT_OUTPUT_DIR": build_adjusted_prices.DEFAULT_OUTPUT_DIR,
    "build_price_parquet.DEFAULT_DATA_PATH": build_price_parquet.DEFAULT_DATA_PATH,
    "check_data_integrity.DEFAULT_DATA_PATH": check_data_integrity.DEFAULT_DATA_PATH,
    "fetch_corporate_actions.DEFAULT_DB_PATH": fetch_corporate_actions.DEFAULT_DB_PATH,
    "fetch_dividends.DIV_DIR": fetch_dividends.DIV_DIR,
    "fetch_institutional.DEFAULT_DB_PATH": fetch_institutional.DEFAULT_DB_PATH,
    "fetch_monthly_revenue.DEFAULT_DB_PATH": fetch_monthly_revenue.DEFAULT_DB_PATH,
    "fetch_official_daily_price.DEFAULT_DB_PATH": fetch_official_daily_price.DEFAULT_DB_PATH,
    "fetch_symbol_meta.DEFAULT_DB_PATH": fetch_symbol_meta.DEFAULT_DB_PATH,
    "fetch_value_chain_classification.DEFAULT_DATA_DIR": fetch_value_chain_classification.DEFAULT_DATA_DIR,
    "mops_dividend_evidence.DEFAULT_RAW_DIR": mops_dividend_evidence.DEFAULT_RAW_DIR,
    "mops_material_evidence.DEFAULT_RAW_DIR": mops_material_evidence.DEFAULT_RAW_DIR,
}
print(json.dumps({"data": {k: str(v) for k, v in paths.items()}, "category": str(config.STOCK_CATEGORY)}))
"""


def _probe(value: str, *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != DATA_DIR_ENV}
    env[DATA_DIR_ENV] = value
    return subprocess.run([sys.executable, "-c", _PROBE, str(REPO_ROOT)], cwd=cwd, env=env, capture_output=True, text=True, check=False)


def test_every_pipeline_default_follows_a_configured_root(external: Path, tmp_path: Path) -> None:
    """The constants bind at import, so only a fresh interpreter shows the real wiring."""
    result = _probe(str(external), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)

    root = external.resolve()
    for name, value in report["data"].items():
        resolved = Path(value)
        assert resolved == root or root in resolved.parents, f"{name} does not follow {DATA_DIR_ENV}: {resolved}"
    assert Path(report["category"]) == REPO_ROOT / "config" / "stock_category.json5"


def test_a_bad_value_stops_every_entry_point_at_import(tmp_path: Path) -> None:
    result = _probe(str(tmp_path / "typo"))

    assert result.returncode != 0
    assert "DataDirConfigError" in result.stderr
    assert not (tmp_path / "typo").exists()


def test_a_bare_key_in_the_env_file_fails_instead_of_meaning_unset(checkout: Path) -> None:
    """python-dotenv parses a line without `=` as None; that is a broken setting."""
    (checkout / ".env").write_text(f"API_KEY=x\n{DATA_DIR_ENV}\n", encoding="utf-8")

    with pytest.raises(DataDirConfigError, match="set but empty"):
        resolve_data_dir({}, checkout)
