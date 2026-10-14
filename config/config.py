# config.py

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values

# All data paths are anchored to the repository root rather than the current
# working directory. Two conventions used to coexist here -- some modules
# resolved "data/" relative to the CWD, others relative to their own file --
# and they agreed only because the scripts happened to live in the repository
# root, which is also where they were run from. Anchoring every path to one
# place is what lets the scripts be invoked from any directory, and from
# anywhere in the tree, without half of them writing to a different "data".
REPO_ROOT = Path(__file__).resolve().parent.parent

# This checkout's own machine-local settings. Read by explicit path, never by
# python-dotenv's upward search: a development worktree under
# `<primary>/.worktrees/<name>/` would otherwise find the primary checkout's
# `.env` and inherit its production data directory and backup destination.
ENV_FILE = REPO_ROOT / ".env"

# Names the live data directory when it lives outside the checkout. The process
# environment wins over this checkout's `.env`; unset in both means the
# in-repository default. See docs/operations.md#data-location.
DATA_DIR_ENV = "SHIOAJI_DATA_DIR"


class DataDirConfigError(RuntimeError):
    """A configured data directory is unusable; there is deliberately no fallback."""


def _has_link_component(path: Path) -> bool:
    return any(part.is_symlink() or part.is_junction() for part in (path, *path.parents))


def _configured_value(environ: Mapping[str, str], env_file: Path) -> tuple[str | None, str]:
    value = environ.get(DATA_DIR_ENV)
    if value is not None:
        return value, "environment"
    if env_file.is_file():
        values = dotenv_values(env_file)
        if DATA_DIR_ENV in values:
            # A bare `SHIOAJI_DATA_DIR` line (no `=`) parses as None; it is a
            # broken setting, not an absent one, so it must not fall back.
            return values[DATA_DIR_ENV] or "", str(env_file)
    return None, "default"


def resolve_data_dir(environ: Mapping[str, str], repo_root: Path) -> tuple[Path, str]:
    """Return (data directory, where its setting came from).

    A configured value must name an existing, absolute, link-free directory
    outside the checkout. Anything else raises instead of falling back to
    `<repo>/data`: a typo that quietly selected an empty directory would start
    re-downloading quota-limited history into it.
    """
    value, source = _configured_value(environ, repo_root / ".env")
    if value is None:
        return repo_root / "data", source
    if not value.strip():
        raise DataDirConfigError(f"{DATA_DIR_ENV} is set but empty ({source})")
    path = Path(value.strip())
    if not path.is_absolute():
        raise DataDirConfigError(f"{DATA_DIR_ENV}={value!r} ({source}) is not an absolute path")
    if not path.is_dir():
        raise DataDirConfigError(f"{DATA_DIR_ENV}={value!r} ({source}) is not an existing directory")
    if _has_link_component(path):
        raise DataDirConfigError(f"{DATA_DIR_ENV}={value!r} ({source}) contains a symlink or junction")
    resolved, repo = path.resolve(), repo_root.resolve()
    if resolved == repo or repo in resolved.parents or resolved in repo.parents:
        raise DataDirConfigError(f"{DATA_DIR_ENV}={value!r} ({source}) overlaps the checkout {repo}")
    return resolved, source


# DATA_DIR 是用於定義預設的資料緩存目錄。
DATA_DIR, DATA_DIR_SOURCE = resolve_data_dir(os.environ, REPO_ROOT)
DATA_DIR_CONFIGURED = DATA_DIR_SOURCE != "default"


def ensure_data_dir() -> Path:
    """Create the in-repository default when missing; never create a configured one."""
    data_dir = Path(DATA_DIR)
    if not data_dir.is_dir():
        if DATA_DIR_CONFIGURED:
            raise DataDirConfigError(f"configured data directory {data_dir} ({DATA_DIR_SOURCE}) no longer exists")
        data_dir.mkdir(parents=True)
    return data_dir


# STOCK_CATEGORY 用於定義預設的股票類型對照表。
# It is tracked with the code rather than acquired, so it stays in the checkout
# when DATA_DIR is configured elsewhere.
STOCK_CATEGORY = REPO_ROOT / "data" / "stock_category.json5"

# STOCK_SYMBOL_MAPPING  用於定義預設的股票股號對照表
STOCK_SYMBOL_MAPPING = DATA_DIR / "stock_symbol_mapping.json5"

# SHIOAJI_START_DATE 用於定義 shioaji 資料的起始日期
SHIOAJI_START_DATE = "2018-12-07"

# API_KEY 用於定義預設的 api 放置的檔案
# 第一行寫 API key
# 第二行寫 Secret key
API_KEY = "api.key"  # pragma: allowlist secret
