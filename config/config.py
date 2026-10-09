# config.py

from pathlib import Path

# All data paths are anchored to the repository root rather than the current
# working directory. Two conventions used to coexist here -- some modules
# resolved "data/" relative to the CWD, others relative to their own file --
# and they agreed only because the scripts happened to live in the repository
# root, which is also where they were run from. Anchoring every path to one
# place is what lets the scripts be invoked from any directory, and from
# anywhere in the tree, without half of them writing to a different "data".
REPO_ROOT = Path(__file__).resolve().parent.parent

# DATA_DIR 是用於定義預設的資料緩存目錄。
DATA_DIR = REPO_ROOT / "data"

# STOCK_CATEGORY 用於定義預設的股票類型對照表
STOCK_CATEGORY = DATA_DIR / "stock_category.json5"

# STOCK_SYMBOL_MAPPING  用於定義預設的股票股號對照表
STOCK_SYMBOL_MAPPING = DATA_DIR / "stock_symbol_mapping.json5"

# SHIOAJI_START_DATE 用於定義 shioaji 資料的起始日期
SHIOAJI_START_DATE = "2018-12-07"

# API_KEY 用於定義預設的 api 放置的檔案
# 第一行寫 API key
# 第二行寫 Secret key
API_KEY = "api.key"  # pragma: allowlist secret
