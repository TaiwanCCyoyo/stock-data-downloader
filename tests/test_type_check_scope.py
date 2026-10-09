"""Imported implementations must not block unrelated, correctly typed callers."""

import subprocess
import sys
from pathlib import Path


def test_mypy_retains_imported_types_without_imported_diagnostics(tmp_path: Path) -> None:
    config = Path(__file__).resolve().parents[1] / "pyproject.toml"
    helper = tmp_path / "helper.py"
    caller = tmp_path / "caller.py"
    helper.write_text('def parse(value: int) -> int:\n    return "broken"\n', encoding="utf-8")
    caller.write_text("from helper import parse\nresult: int = parse(1)\n", encoding="utf-8")

    # Editable installs can classify repo-local .tmp fixtures as installed code.
    # Expose their diagnostics so normal/silent imports remain the tested behavior.
    def check(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "mypy", "--no-incremental", "--no-silence-site-packages", "--config-file", str(config), *arguments],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    normal = check("--follow-imports=normal", str(caller))
    assert normal.returncode == 1 and "[return-value]" in normal.stdout, normal.stdout + normal.stderr

    targeted = check(str(caller))
    assert targeted.returncode == 0, targeted.stdout + targeted.stderr

    explicit = check(str(caller), str(helper))
    assert explicit.returncode == 1 and "[return-value]" in explicit.stdout, explicit.stdout + explicit.stderr

    caller.write_text('from helper import parse\nresult: int = parse("wrong")\n', encoding="utf-8")
    invalid_call = check(str(caller))
    assert invalid_call.returncode == 1 and "[arg-type]" in invalid_call.stdout, invalid_call.stdout + invalid_call.stderr


def test_mypy_checks_requests_types_without_network_calls(tmp_path: Path) -> None:
    config = Path(__file__).resolve().parents[1] / "pyproject.toml"
    caller = tmp_path / "request_types.py"

    def check(source: str) -> subprocess.CompletedProcess[str]:
        caller.write_text(source, encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-m", "mypy", "--no-incremental", "--config-file", str(config), str(caller)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    valid = check('import requests\nresponse: requests.Response = requests.get("https://example.test", timeout=10)\n')
    assert valid.returncode == 0, valid.stdout + valid.stderr

    invalid = check('import requests\nresponse: int = requests.get("https://example.test", timeout=10)\n')
    assert invalid.returncode == 1 and "[assignment]" in invalid.stdout, invalid.stdout + invalid.stderr
