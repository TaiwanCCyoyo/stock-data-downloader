"""CLI exit codes distinguish source failures from completed bounded captures."""

from pathlib import Path

import fetch_mops_dividend_evidence as cli
import pytest

from config import config
from shp_utils import mops_dividend_evidence as evidence
from shp_utils.mops_dividend_evidence import DEFAULT_RAW_DIR


@pytest.mark.parametrize("status,exit_code", [("complete", 0), ("source_empty", 0), ("partial", 1), ("error", 1)])
def test_cli_passes_explicit_scope_and_reports_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: str, exit_code: int):
    calls = []

    def fake_capture(company: str, year: int, **kwargs):
        calls.append((company, year, kwargs))
        return {"status": status, "captured_details": 1, "output_dir": str(tmp_path / "new")}

    monkeypatch.setattr(cli, "capture", fake_capture)
    assert cli.main(["--company-id", "5511", "--roc-year", "105", "--max-details", "2", "--output-dir", str(tmp_path / "new")]) == exit_code
    assert calls == [("5511", 105, {"output_dir": tmp_path / "new", "max_details": 2})]


@pytest.mark.parametrize("error", [ValueError("invalid"), FileExistsError("preserve"), PermissionError("boundary")])
def test_cli_input_filesystem_errors_exit_two(monkeypatch: pytest.MonkeyPatch, error: Exception):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(cli, "capture", fail)
    assert cli.main(["--company-id", "5511", "--roc-year", "105"]) == 2


def test_cli_requires_scope_before_capture(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cli, "capture", lambda *args, **kwargs: pytest.fail("must not fetch"))
    with pytest.raises(SystemExit, match="2"):
        cli.main([])


def test_default_output_is_config_anchored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    assert Path(DEFAULT_RAW_DIR).is_absolute()
    assert DEFAULT_RAW_DIR == config.DATA_DIR / "raw" / "mops" / "dividend_announcements"


@pytest.mark.parametrize("link_method", ["is_symlink", "is_junction"])
def test_default_output_rejects_link_components_before_network(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, link_method: str):
    data = tmp_path / "data"
    raw = data / "raw" / "mops" / "dividend_announcements"
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(evidence, "DEFAULT_RAW_DIR", raw)
    monkeypatch.setattr(Path, link_method, lambda self: self == data / "raw")
    with pytest.raises(ValueError, match="link or junction"):
        evidence.capture("5511", 105, transport=lambda *args: pytest.fail("must not fetch"))
    assert not data.exists()


def test_default_output_rejects_raw_root_outside_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(evidence, "DEFAULT_RAW_DIR", tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="outside config.DATA_DIR"):
        evidence.capture("5511", 105, transport=lambda *args: pytest.fail("must not fetch"))
    assert not (tmp_path / "elsewhere").exists()
