"""CLI contracts for bounded MOPS material evidence capture."""

from __future__ import annotations

from pathlib import Path

import fetch_mops_material_evidence as cli
import pytest


@pytest.mark.parametrize("status,expected", [("complete", 0), ("source_empty", 0), ("partial", 1), ("error", 1)])
def test_cli_passes_required_company_month_scope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: str, expected: int):
    calls = []

    def fake_capture(company: str, year: int, month: int, **kwargs):
        calls.append((company, year, month, kwargs))
        return {"status": status, "captured_details": 1, "output_dir": str(tmp_path / "new")}

    monkeypatch.setattr(cli, "capture", fake_capture)
    assert cli.main(["--company-id", "6465", "--roc-year", "115", "--roc-month", "7", "--max-details", "2", "--output-dir", str(tmp_path / "new")]) == expected
    assert calls == [("6465", 115, 7, {"output_dir": tmp_path / "new", "max_details": 2})]


def test_cli_requires_month_before_capture(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cli, "capture", lambda *args, **kwargs: pytest.fail("must not fetch"))
    with pytest.raises(SystemExit, match="2"):
        cli.main(["--company-id", "6465", "--roc-year", "115"])


@pytest.mark.parametrize("error", [ValueError("invalid scope"), PermissionError("boundary")])
def test_cli_returns_two_for_input_or_storage_failure(monkeypatch: pytest.MonkeyPatch, error: Exception):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(cli, "capture", fail)
    assert cli.main(["--company-id", "6465", "--roc-year", "115", "--roc-month", "7"]) == 2
