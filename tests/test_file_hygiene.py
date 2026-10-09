"""File checks must be read-only, multilingual and safe for private text."""

import subprocess
import sys
from pathlib import Path

import pytest
from file_hygiene import check_file_hygiene


@pytest.mark.parametrize(
    ("contents", "valid"),
    [
        (b"Plain UTF-8 text\n", True),
        ("\u5408\u6cd5\u7684\u7e41\u9ad4\u4e2d\u6587\n".encode(), True),
        (b'{"symbol": "2330"}\n', True),
        (b"", True),
        (b"bad encoding: \xff", False),
        ("broken text: \ufffd".encode(), False),
        (b"\xef\xbb\xbfUTF-8 BOM", False),
    ],
)
def test_encoding_check_never_rewrites_input(tmp_path: Path, contents: bytes, valid: bool) -> None:
    path = tmp_path / "sample.md"
    path.write_bytes(contents)
    assert check_file_hygiene(str(path)) is valid
    assert path.read_bytes() == contents


def test_missing_file_is_not_created(tmp_path: Path) -> None:
    path = tmp_path / "missing.md"
    assert check_file_hygiene(str(path))
    assert not path.exists()


def test_unreadable_input_fails(tmp_path: Path) -> None:
    assert not check_file_hygiene(str(tmp_path))


def test_cli_checks_all_files_without_printing_private_text(tmp_path: Path) -> None:
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("Heading\nprivate fixture one: \ufffd\n", encoding="utf-8")
    second.write_bytes(b"private fixture two: \xff")
    script = Path(__file__).resolve().parents[1] / "scripts" / "file_hygiene.py"
    result = subprocess.run(
        [sys.executable, str(script), "--file", str(first), str(second)],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert f"{first}:2:" in result.stderr
    assert str(second) in result.stderr
    assert "private fixture" not in result.stderr + result.stdout


def test_cli_accepts_valid_text(tmp_path: Path) -> None:
    path = tmp_path / "valid.md"
    path.write_text("Valid text\n", encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts" / "file_hygiene.py"
    result = subprocess.run([sys.executable, str(script), "--file", str(path)], capture_output=True)
    assert result.returncode == 0
    assert not result.stdout
    assert not result.stderr
