"""Conservative prose selection and real Git merge-base change handling."""

import json
import re
import subprocess
import sys
from pathlib import Path

import ci_scope
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow() -> dict:
    return yaml.load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_workflow_keeps_metadata_edits_separate_from_required_validation() -> None:
    config = workflow()
    assert "edited" in config["on"]["pull_request"]["types"]
    job = config["jobs"]["repository-checks"]
    metadata = "github.event.action == 'edited' && github.event.changes.base == null"
    # A job skipped by a job-level if keeps its unevaluated name, so the job
    # must run for the metadata-edit name to replace the required one.
    assert "if" not in job
    assert job["name"] == "${{ " + metadata + " && 'metadata-edit-ignored' || 'repository-checks' }}"
    assert job["env"]["METADATA_EDIT"] == "${{ " + metadata + " }}"
    assert config["concurrency"]["group"] == (
        "${{ github.workflow }}-${{ " + metadata + " && github.run_id || github.event.pull_request.number || github.run_id }}"
    )
    assert job["runs-on"] == "${{ " + metadata + " && 'ubuntu-latest' || 'windows-latest' }}"
    assert config["permissions"] == {"contents": "read"}
    guard = "env.METADATA_EDIT != 'true'"
    notice, *validation = job["steps"]
    assert notice["if"] == "env.METADATA_EDIT == 'true'"
    # Every validation step is Windows PowerShell work that a metadata edit must skip.
    assert all(guard in step.get("if", "") for step in validation)


def test_workflow_uses_exact_python_and_selected_merge_base() -> None:
    steps = workflow()["jobs"]["repository-checks"]["steps"]
    named = {step["name"]: step for step in steps if "name" in step}
    assert ".python-version" in named["Read exact Python version"]["run"]
    setup = next(step for step in steps if step.get("uses", "").startswith("astral-sh/setup-uv@"))
    assert setup["with"]["python-version"] == "${{ steps.python-version.outputs.version }}"
    # An unset uv version floats to the newest release; keep it an exact pin.
    assert re.fullmatch(r"\d+\.\d+\.\d+", setup["with"]["version"])
    assert named["Repository checks"]["env"]["CHECK_BASE"] == "${{ steps.scope.outputs.merge_base }}"
    assert named["Install dependencies"]["run"] == "uv sync --locked --group dev"
    assert named["Full offline tests"]["if"] == "env.METADATA_EDIT != 'true' && steps.scope.outputs.full == 'true'"
    assert named["Prose selector contracts"]["if"] == "env.METADATA_EDIT != 'true' && steps.scope.outputs.full != 'true'"
    # Only evidence publication may override normal success gating after a failure.
    assert all("always()" not in step.get("if", "") for step in steps if step.get("name") != "Publish scope evidence")


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows step exit behavior")
@pytest.mark.parametrize("step_name", ["Select scope", "Repository checks"])
@pytest.mark.parametrize("event", ["pull_request", "workflow_dispatch"])
@pytest.mark.parametrize("exit_code", [0, 23])
def test_multiline_windows_steps_propagate_command_exit(step_name: str, event: str, exit_code: int) -> None:
    steps = workflow()["jobs"]["repository-checks"]["steps"]
    script = next(step["run"] for step in steps if step.get("name") == step_name)
    # Replace only the process boundary, exercising both native dispatch branches
    # without installing hooks or invoking any acquisition command.
    harness = f"function uv {{ $global:LASTEXITCODE = {exit_code} }}\n$env:CHECK_EVENT = '{event}'\n" + script
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", harness], capture_output=True, timeout=20)
    assert result.returncode == exit_code, result.stderr


@pytest.mark.parametrize("path", sorted(ci_scope.PROSE_PATHS))
def test_reviewed_prose_can_skip_runtime_tests(path: str) -> None:
    assert not ci_scope.needs_full_tests([path])


@pytest.mark.parametrize(
    "path",
    [
        "scripts/run_daily.py",
        "shp_utils/data_status.py",
        "tests/test_data_status.py",
        "config/config.py",
        "cron/run_daily_scheduled.cmd",
        "uv.lock",
        "pyproject.toml",
        ".python-version",
        ".github/workflows/ci.yml",
        ".pre-commit-config.yaml",
        "conftest.py",
        "tests/conftest.py",
        "docs/contracts/data-status.md",
        "docs/new-guide.md",
        "new/file",
        "README.md/unknown.py",
    ],
)
def test_runtime_configuration_contracts_and_unknown_paths_run_full(path: str) -> None:
    assert ci_scope.needs_full_tests(["README.md", path])


def test_empty_diff_runs_full() -> None:
    assert ci_scope.needs_full_tests([])


def test_rename_delete_and_unusual_names_keep_all_paths() -> None:
    paths = ci_scope.changed_paths(b"R100\0scripts/old.py\0README.md\0D\0tests/deleted.py\0A\0odd\nname.md\0")
    assert paths == ["scripts/old.py", "README.md", "tests/deleted.py", "odd\nname.md"]
    assert ci_scope.needs_full_tests(paths)


@pytest.mark.parametrize("data", [b"M\0README.md", b"R100\0README.md\0", b"Z\0README.md\0", b"M\0\0"])
def test_bad_diff_cannot_certify_prose(data: bytes) -> None:
    with pytest.raises(ValueError):
        ci_scope.changed_paths(data)


def run_git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def test_selection_and_hook_base_exclude_unrelated_base_advances(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_git(tmp_path, "init")
    run_git(tmp_path, "config", "user.name", "CI fixture")
    run_git(tmp_path, "config", "user.email", "ci@example.test")
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    run_git(tmp_path, "add", ".")
    run_git(tmp_path, "commit", "-m", "base")
    ancestor = run_git(tmp_path, "rev-parse", "HEAD")
    run_git(tmp_path, "checkout", "-b", "feature")
    (tmp_path / "README.md").write_text("changed\n", encoding="utf-8")
    run_git(tmp_path, "commit", "-am", "prose")
    head = run_git(tmp_path, "rev-parse", "HEAD")
    run_git(tmp_path, "checkout", "--detach", ancestor)
    (tmp_path / "runtime.py").write_text("pass\n", encoding="utf-8")
    run_git(tmp_path, "add", ".")
    run_git(tmp_path, "commit", "-m", "unrelated base advance")
    base = run_git(tmp_path, "rev-parse", "HEAD")
    monkeypatch.chdir(tmp_path)
    plan = ci_scope.select(base, head)
    assert plan == {"full": False, "merge_base": ancestor, "head": head, "paths": ["README.md"]}


def test_manual_full_and_invalid_ref_exit_codes(tmp_path: Path) -> None:
    script = Path(ci_scope.__file__)
    output = tmp_path / "output"
    plan = tmp_path / "plan.json"
    command = [sys.executable, str(script), "--output", str(output), "--plan", str(plan)]
    result = subprocess.run([*command, "--full"], cwd=tmp_path, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(plan.read_text(encoding="utf-8"))["full"] is True
    assert output.read_text(encoding="utf-8") == "full=true\n"
    failed_output = tmp_path / "failed-output"
    result = subprocess.run(
        [sys.executable, str(script), "--output", str(failed_output), "--base", "missing-ref"],
        cwd=tmp_path,
        capture_output=True,
    )
    assert result.returncode != 0
    assert not failed_output.exists()
