"""
Download Stock Module

Downloads 1-minute stock bar data from the Shioaji API.
Supports resumable downloads, usage-quota checks, and error retries, paces its
requests so the server does not drop the session (see MIN_REQUEST_INTERVAL_SEC),
and aborts the run when the API stops answering (see MAX_CONSECUTIVE_FAILURES).
"""

import argparse
import datetime
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import json5
import pandas as pd
import shioaji as sj
from dotenv import load_dotenv
from shioaji.contracts import FetchStatus

from config import config
from shp_utils.logging_utils import setup_logging

# Re-export or just use local typing


logger = logging.getLogger(__name__)

# The server drops the session with a misleading 401 "Token is expired" when
# requests arrive too fast, and the token itself is nowhere near its expiry --
# on 2026-08-15 one was rejected 12s after being issued with 24h of life left.
# Measured on this account: ~64 requests/s died after 340 symbols, while 1200
# symbols (2400 requests) paced at ~20 requests/s finished cleanly, matching the
# healthy 2026-08-08 run's 12.3 symbols/s. A floor between requests keeps the
# run inside that proven band. It only ever slows down days where responses come
# back instantly (a non-trading day, or symbols already up to date); a day with
# real bars to fetch is already slower than this by itself.
MIN_REQUEST_INTERVAL_SEC = 0.1

# The 500MB quota is a whole-day figure that moves by single-digit MB over a
# thousand symbols, so checking it once per symbol adds nothing -- it just
# doubles the request count, which is exactly what got the session dropped.
USAGE_CHECK_INTERVAL_SEC = 60.0

# Second brake, independent of the daily-quota guard in the download loop below.
# When the API session dies mid-run every kbars call runs into its own 30s
# timeout, and api.usage() times out too -- so the quota guard, whose whole job
# is to stop the run, never fires and the loop walks the entire contract list at
# ~35s of pure timeout per symbol. On 2026-08-14 that was three hours of doing
# nothing before the scheduled task was killed by hand. Consecutive *exceptions*
# are the discriminating signal: a symbol that merely has no new bars comes back
# empty without raising, so a quiet day never trips this counter.
MAX_CONSECUTIVE_FAILURES = 10

# Shioaji files every instrument that has no industry category under category
# "00" -- which on TSE means 236 ETFs sitting in the same bucket as ~31,000
# warrants. get_contract_list drops that whole bucket to keep warrants out, so
# no ETF was ever downloaded, including 0050, which check_data_integrity.py
# names as a reference symbol for deriving the trading calendar. List here the
# category-"00" codes worth tracking anyway; widen it to every code starting
# "00" if the full ETF universe is ever wanted (that is a large one-off
# backfill, so it is kept deliberate rather than automatic).
TRACKED_NON_INDUSTRY_CODES = ("0050",)

# The TSE market index (加權指數). Selected with .get() rather than a membership
# test: shioaji 1.2.9's index collection keys itself by exchange-prefixed symbol
# (TSE001), so both `"001" in coll` and `"TSE001" in coll` answer False while
# `coll.get("001")` still returns the contract. The old `if "001" in ...` gate was
# therefore permanently closed, and data/001_day.csv stopped at 2025-12-15.
MARKET_INDEX_CODE = "001"

# Shioaji downloads the contract list asynchronously and login()'s own default is
# contracts_timeout=0 -- login returns without waiting, so whatever has arrived by
# the time api.Contracts is read is what the run sees. On 2026-08-27 and 08-28
# that was 332 warrants and not one ordinary stock: the mapping file was
# overwritten with them, step 1 "succeeded" with 3 symbols, and two trading days
# of bars were never downloaded while the log reported a clean run. A healthy
# fetch of ~45k contracts took ~11s on 2026-08-26, so this ceiling is generous
# rather than tight -- it is a deadline, not a pace.
CONTRACTS_FETCH_TIMEOUT_MS = 60_000

# Second guard, independent of Shioaji's own status flag. A mapping that loses
# most of its entries means the contract list was read before it finished, not
# that the market shrank -- delistings move this by tens, never by half. It
# matters because the backup mirror is additive: it protects against deletion,
# not against an overwrite, so it copied the 332-entry file over the good one.
# This refusal is the last thing between a partial read and both copies gone.
MAPPING_MIN_RETAINED_FRACTION = 0.5


class DownloadAborted(RuntimeError):
    """The API stopped answering, so the run gave up instead of idling.

    Propagated out of ``main`` instead of being logged and swallowed like other
    errors, so the caller (``run_daily.py`` step 1, or this module's own CLI
    exit code) reports the run as failed.

    Carries the count ``main`` would have returned, because symbols fetched
    before the abort are already on disk and still need converting -- on
    2026-08-14 roughly 300 symbols were left with minute bars and no day bars
    precisely because the run ended without that count reaching step 2.
    """

    def __init__(self, message: str, updated_symbol_count: int = 0):
        super().__init__(message)
        self.updated_symbol_count = updated_symbol_count


class RequestPacer:
    """Keeps at least ``min_interval`` seconds between consecutive API calls."""

    def __init__(self, min_interval: float | None = None):
        # Resolved at call time, not import time, so the constant stays the one
        # place the pace is defined (and tests can dial it down).
        self.min_interval = MIN_REQUEST_INTERVAL_SEC if min_interval is None else min_interval
        self._last = 0.0

    def wait(self) -> None:
        if self.min_interval > 0:
            delay = self.min_interval - (time.monotonic() - self._last)
            if delay > 0:
                time.sleep(delay)
        self._last = time.monotonic()


def normalize_codes(codes: list[str] | None) -> set[str] | None:
    if not codes:
        return None
    return {code.strip() for item in codes for code in item.split(",") if code.strip()}


def get_contract_by_code(api: sj.Shioaji, code: str):
    # The index collection is searched last so an ordinary stock never resolves to
    # an index, but it must be searched at all -- without it `--codes 001` finds
    # nothing and the market index cannot be backfilled on its own after a gap.
    return api.Contracts.Stocks.TSE.get(code) or api.Contracts.Stocks.OTC.get(code) or api.Contracts.Indexs.TSE.get(code)


def _chunk_date_ranges(start_str: str, end_str: str, max_days: int = 30) -> list[tuple[str, str]]:
    """Split [start_str, end_str] into consecutive <=max_days-day windows.

    Shioaji's kbars endpoint rejects a single request spanning more than 30
    days, so a wide gap (e.g. a first-time backfill, or a symbol that missed
    several days of updates) must be requested in multiple calls instead of
    being skipped entirely.
    """
    start = datetime.datetime.strptime(start_str, "%Y-%m-%d").date()
    end = datetime.datetime.strptime(end_str, "%Y-%m-%d").date()
    if start > end:
        return []

    chunks = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + datetime.timedelta(days=max_days - 1), end)
        chunks.append((chunk_start.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
        chunk_start = chunk_end + datetime.timedelta(days=1)
    return chunks


def get_last_timestamp_from_csv(file_path: str, date_col_name: str = "ts") -> datetime.datetime | None:
    """
    Efficiently read the last timestamp from a CSV file without loading the whole file.
    Reads the header to find `date_col_name` index, then reads the last line.
    """
    try:
        with open(file_path, "rb") as f:
            # 1. Read Header
            header_line = f.readline().decode("utf-8").strip()
            if not header_line:
                return None

            headers = header_line.split(",")
            try:
                date_col_index = headers.index(date_col_name)
            except ValueError:
                return None

            # 2. Seek to End
            try:
                f.seek(-2, os.SEEK_END)
                while f.read(1) != b"\n":
                    f.seek(-2, os.SEEK_CUR)
            except OSError:
                # File is too small (e.g. only header or empty)
                return None

            last_line = f.readline().decode("utf-8")
            if not last_line:
                return None

            # Avoid parsing header as data if file has only 1 line
            if last_line.strip() == header_line:
                return None

            # 3. Parse Last Line
            parts = last_line.strip().split(",")
            ts_str = parts[date_col_index]

            # Handle numeric timestamps
            try:
                # Try converting to float first, then int
                ts_val = int(float(ts_str))
                return pd.to_datetime(ts_val)
            except ValueError:
                # Not a number, try parsing as string
                return pd.to_datetime(ts_str)

    except (IOError, IndexError, ValueError):
        return None


def get_contract_list(api: sj.Shioaji, stock_category: dict) -> list:
    """Build the list of contracts to download."""
    TSE_contract_list = api.Contracts.Stocks.TSE
    OTC_contract_list = api.Contracts.Stocks.OTC
    logger.info(f"合約來源筆數 (過濾前): TSE={sum(1 for _ in TSE_contract_list)} OTC={sum(1 for _ in OTC_contract_list)}")

    contract_list = []

    # Listed (TSE) stocks
    # Exclude options
    if "TSE" in stock_category:
        option_code = [key for key, value in stock_category["TSE"].items() if value == "期權"]
    else:
        option_code = []

    for contract in TSE_contract_list:
        if contract.category not in option_code and re.match("^[0-9]+$", contract.code):
            contract_list.append(contract)

    # OTC stocks
    if "OTC" in stock_category:
        option_code = [key for key, value in stock_category["OTC"].items() if value == "期權"]
    else:
        option_code = []

    for contract in OTC_contract_list:
        if contract.category not in option_code and re.match("^[0-9]+$", contract.code):
            contract_list.append(contract)

    # Market index
    index_contract = api.Contracts.Indexs.TSE.get(MARKET_INDEX_CODE)
    if index_contract is None:
        logger.warning(f"大盤指數 {MARKET_INDEX_CODE} 不在 Shioaji 指數合約中，這次不下載")
    else:
        contract_list.append(index_contract)

    # ETFs and anything else worth tracking that the category filter above drops
    already = {contract.code for contract in contract_list}
    for code in TRACKED_NON_INDUSTRY_CODES:
        if code in already:
            continue
        contract = get_contract_by_code(api, code)
        if contract is None:
            logger.warning(f"Tracked code {code} not found in Shioaji contracts; skipped.")
            continue
        contract_list.append(contract)

    # Sort
    contract_list = sorted(contract_list, key=lambda x: x.code)
    return contract_list


def assert_contracts_fetched(api: sj.Shioaji) -> None:
    """Refuse to run on a contract list that is still downloading.

    Reads the counts as well as the status, so the log records what the run
    actually saw -- the 2026-08-27 failure was only diagnosable afterwards by
    inferring the contract count from the size of the file it wrote.
    """
    status = api.Contracts.status
    tse_count = sum(1 for _ in api.Contracts.Stocks.TSE)
    otc_count = sum(1 for _ in api.Contracts.Stocks.OTC)
    logger.info(f"合約清單: status={status} TSE={tse_count} OTC={otc_count}")
    if status != FetchStatus.Fetched:
        raise DownloadAborted(f"合約清單尚未下載完成 (status={status}, TSE={tse_count}, OTC={otc_count})；中止下載，避免用半套清單覆寫對照表並漏抓整個市場")


def read_existing_mapping_count(path: str | Path) -> int:
    """Entry count of the mapping already on disk, or 0 if there is none to compare
    against. An unreadable baseline must not block a first run, so it reads as 0."""
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        try:
            # The mapping is written with json.dump, so the stdlib parser reads it in
            # milliseconds; json5 took ~7s on the 53k-entry file (2026-10-22).
            mapping: Any = json.loads(text)
        except ValueError:
            mapping = json5.loads(text)
        return len(mapping)
    except Exception as e:
        logger.warning(f"無法讀取既有對照表 {path}，這次不做筆數比對: {e}")
        return 0


def update_stock_symbol_mapping(api: sj.Shioaji) -> None:
    """Refresh and save the stock code -> name mapping."""
    all_contracts = []
    all_contracts.extend(api.Contracts.Stocks.TSE)
    all_contracts.extend(api.Contracts.Stocks.OTC)
    index_contract = api.Contracts.Indexs.TSE.get(MARKET_INDEX_CODE)
    if index_contract is not None:
        all_contracts.append(index_contract)

    stock_symbol_mapping = {item.code: item.name for item in all_contracts}
    stock_symbol_mapping = dict(sorted(stock_symbol_mapping.items()))

    previous_count = read_existing_mapping_count(config.STOCK_SYMBOL_MAPPING)
    logger.info(f"對照表筆數: 既有 {previous_count} -> 這次讀到 {len(stock_symbol_mapping)}")
    if previous_count and len(stock_symbol_mapping) < previous_count * MAPPING_MIN_RETAINED_FRACTION:
        raise DownloadAborted(
            f"對照表只讀到 {len(stock_symbol_mapping)} 筆，不到既有 {previous_count} 筆的 "
            f"{MAPPING_MIN_RETAINED_FRACTION:.0%}；拒絕覆寫 {config.STOCK_SYMBOL_MAPPING}"
        )

    with open(config.STOCK_SYMBOL_MAPPING, "w", encoding="utf-8") as f:
        json.dump(stock_symbol_mapping, f, ensure_ascii=False, indent=4)
    logger.info(f"對照表已經儲存至 {config.STOCK_SYMBOL_MAPPING} ({len(stock_symbol_mapping)} 筆)")


def _is_timeout_error(error: BaseException) -> bool:
    """Whether a failed kbars request timed out, judged from the exception type or its message."""
    return isinstance(error, TimeoutError) or "timeout" in type(error).__name__.lower() or "timed out" in str(error).lower() or "timeout" in str(error).lower()


def main(codes: list[str] | None = None, start: str | None = None, end: str | None = None) -> int:
    """Download minute bars for the requested (or all) contracts.

    Returns the number of symbols that actually got new/updated rows written
    to disk, so a caller (e.g. `run_daily.py`) can skip downstream steps that
    have nothing new to process, such as the daily-bar conversion, on a day
    with zero new data (non-trading day, quota exhausted, etc.).
    """
    load_dotenv(config.ENV_FILE)
    target_codes = normalize_codes(codes)
    updated_symbol_count = 0

    if bool(start) != bool(end):
        raise SystemExit("--start and --end must be provided together.")
    force_refetch = start is not None and end is not None

    # Check that API key/secret are present
    if "API_KEY" not in os.environ or "SECRET_KEY" not in os.environ:
        logger.error("Error: API_KEY or SECRET_KEY not found in environment variables.")
        return updated_symbol_count

    api = sj.Shioaji()

    # Login
    logger.info("登入 Shioaji API...")
    login_started = time.monotonic()
    api.login(
        api_key=os.environ["API_KEY"],
        secret_key=os.environ["SECRET_KEY"],
        contracts_timeout=CONTRACTS_FETCH_TIMEOUT_MS,
    )
    logger.info("login_elapsed: %.1fs (contracts_timeout=%ds)", time.monotonic() - login_started, CONTRACTS_FETCH_TIMEOUT_MS // 1000)
    assert_contracts_fetched(api)

    try:
        # Load stock category
        logger.info(f"Loading stock category from {config.STOCK_CATEGORY}")
        if os.path.exists(config.STOCK_CATEGORY):
            with open(config.STOCK_CATEGORY, "r", encoding="utf-8") as f:
                stock_category = json5.load(f)
        else:
            logger.warning("Warning: Stock category file not found. Assuming empty.")
            stock_category = {"TSE": {}, "OTC": {}}

        # Update symbol mapping
        mapping_started = time.monotonic()
        update_stock_symbol_mapping(api)
        logger.info("mapping_elapsed: %.1fs", time.monotonic() - mapping_started)

        # Build download list
        contract_list = get_contract_list(api, stock_category)
        if target_codes:
            contract_list = [contract for code in sorted(target_codes) if (contract := get_contract_by_code(api, code)) is not None]
            found_codes = {contract.code for contract in contract_list}
            missing_codes = sorted(target_codes.difference(found_codes))
            if missing_codes:
                logger.warning(f"Requested codes not found in Shioaji contracts: {missing_codes}")
        logger.info(f"共取得 {len(contract_list)} 檔標的")

        start_time = datetime.datetime.now()
        today_date = start_time
        today_str = today_date.strftime("%Y-%m-%d")
        request_end_str = end if start is not None and end is not None else today_str
        downloadable = True
        consecutive_failures = 0
        skipped_failed_count = 0
        skip_reasons = {"timeout": 0, "no_data": 0, "error": 0}
        request_error_count = 0
        pacer = RequestPacer()
        last_usage_check = 0.0

        # Creates only the in-repository default; a configured root must already exist.
        config.ensure_data_dir()

        for contract in contract_list:
            stock_file = f"{config.DATA_DIR}/{contract.code}_min.csv"

            if start is not None and end is not None:
                # Explicit re-fetch: bypass the incremental "last timestamp" shortcut
                # entirely so a specific range can be re-requested even when a later
                # timestamp already exists locally (repairing a mid-history gap).
                start_date_str = start
                start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d")
            else:
                start_date_str = config.SHIOAJI_START_DATE
                # Parse default start date to datetime object for comparison
                start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d")

                if os.path.isfile(stock_file):
                    logger.debug(f"讀取{contract.name} ({contract.code}) 的最後一筆資料時間")
                    try:
                        # Optimization: Read only the last line to get timestamp
                        last_ts = get_last_timestamp_from_csv(stock_file, date_col_name="ts")

                        if last_ts:
                            # Start fetching from the day after the last recorded data point
                            start_date_str = (last_ts + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
                            start_date = datetime.datetime.strptime(f"{start_date_str} 08:00", "%Y-%m-%d %H:%M")
                        else:
                            # File might be empty or header only
                            pass

                    except Exception as e:
                        logger.warning(f"警告: {stock_file} 讀取失敗，可能是檔案損毀 ({e})")
                        logger.info("將重新下載此檔完整資料...")

                        # Rename the corrupted file as a backup (e.g. xx.csv.bak) so it
                        # doesn't affect subsequent processing
                        try:
                            os.rename(stock_file, stock_file + ".bak")
                            logger.info(f"已將損毀檔案備份為: {stock_file}.bak")
                        except OSError:
                            pass

                        # Reset start_date to the default so the full range is re-downloaded
                        start_date_str = config.SHIOAJI_START_DATE
                        start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d")

                # If start_date is already >= today, no update is needed
                if start_date >= today_date:
                    logger.debug(f"跳過{contract.name} ({contract.code}) 因為已經擁有 {start_date_str} 之前的資料")
                    continue

            success = False
            df_new = pd.DataFrame()
            logger.info(f"嘗試取得{contract.name} ({contract.code}) 從 {start_date_str} 到 {request_end_str} 的資料")

            stock_get_start = datetime.datetime.now()
            chunk_dfs = []
            chunk_raised = False
            chunk_timed_out = False
            for chunk_start, chunk_end in _chunk_date_ranges(start_date_str, request_end_str):
                try:
                    pacer.wait()
                    kbars = api.kbars(contract=contract, start=chunk_start, end=chunk_end)
                    chunk_df = pd.DataFrame({**kbars})
                    if not chunk_df.empty:
                        chunk_dfs.append(chunk_df)
                except Exception as e:
                    chunk_raised = True
                    chunk_timed_out = chunk_timed_out or _is_timeout_error(e)
                    request_error_count += 1
                    logger.error(f"取得失敗 ({chunk_start} ~ {chunk_end}): {e}")
            df_new = pd.concat(chunk_dfs, ignore_index=True) if chunk_dfs else pd.DataFrame()

            stock_get_end = datetime.datetime.now()
            logger.debug(f"共花費: {(stock_get_end - stock_get_start).total_seconds()} 秒")

            # Check usage quota, at most once per USAGE_CHECK_INTERVAL_SEC
            if df_new.empty:
                if time.monotonic() - last_usage_check >= USAGE_CHECK_INTERVAL_SEC:
                    last_usage_check = time.monotonic()
                    try:
                        pacer.wait()
                        usage_bytes = api.usage().bytes
                        if usage_bytes >= 524288000:  # 500MB limit
                            logger.warning(f"今日已使用 {usage_bytes} B (達上限)")
                            downloadable = False
                    except Exception as e:
                        logger.warning(f"無法檢查流量使用量，略過本次流量檢查: {e}")
            else:
                success = True

            if not downloadable:
                logger.warning("流量已達上限，停止下載。")
                break

            # Checked after the quota guard so a genuinely spent quota still
            # stops the run with its own, more specific message.
            if chunk_raised and not success:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    raise DownloadAborted(
                        f"連續 {consecutive_failures} 檔完全取不到資料，中止下載（API 可能已停止回應，繼續執行只會空轉）",
                        updated_symbol_count=updated_symbol_count,
                    )
            else:
                consecutive_failures = 0

            if not success:
                skipped_failed_count += 1
                if not chunk_raised:
                    skip_reasons["no_data"] += 1
                elif chunk_timed_out:
                    skip_reasons["timeout"] += 1
                else:
                    skip_reasons["error"] += 1
                logger.warning(f"跳過{contract.name} ({contract.code}) 因為從 {start_date_str} 到 {request_end_str} 的資料取得失敗")
                continue

            # Save/merge logic
            is_append = False
            try:
                if force_refetch and os.path.isfile(stock_file):
                    # The re-fetched range usually overlaps existing data (patching a
                    # mid-history gap), so it must be merged and de-duplicated by
                    # timestamp rather than simply appended, to avoid duplicate rows.
                    logger.debug(f"合併補抓資料到 {contract.name} ({contract.code})，並依時間戳去除重複列")
                    try:
                        existing_df = pd.read_csv(stock_file)
                        try:
                            df_new = df_new.loc[:, existing_df.columns.tolist()]
                        except KeyError:
                            pass  # column drift; fall back to df_new's own columns
                        combined = pd.concat([existing_df, df_new], ignore_index=True)
                        combined = combined.drop_duplicates(subset=["ts"], keep="last")
                        combined = combined.sort_values("ts").reset_index(drop=True)
                        combined.to_csv(stock_file, mode="w", header=True, index=False)
                        is_append = True  # merged in place; treat like append for failure-cleanup semantics below
                        updated_symbol_count += 1
                    except Exception as e:
                        logger.error(f"{stock_file} 合併補抓資料失敗: {e}")
                        logger.error("建議檢查該檔案欄位是否變更，或刪除後重抓。")
                        continue
                else:
                    # Decide append vs overwrite mode.
                    # Use append mode if the file exists and this isn't a full
                    # re-download (start_date isn't the default).
                    if os.path.isfile(stock_file) and start_date_str != config.SHIOAJI_START_DATE:
                        is_append = True

                    if is_append:
                        logger.debug(f"將新資料附加到 {contract.name} ({contract.code})")
                        # Safety check: read the existing file's header to keep
                        # column order consistent
                        try:
                            existing_columns = pd.read_csv(stock_file, nrows=0).columns.tolist()
                            # Reorder df_new's columns to match the existing file
                            df_new = df_new[existing_columns]

                            # Append mode: header=False
                            df_new.to_csv(stock_file, mode="a", header=False, index=False)
                            updated_symbol_count += 1

                        except Exception as e:
                            logger.error(f"{stock_file} 附加失敗: {e}")
                            logger.error("建議檢查該檔案欄位是否變更，或刪除後重抓。")
                            continue
                    else:
                        # New file, or a full re-download
                        logger.info("建立/覆蓋本地股票資料檔")
                        df_new.to_csv(stock_file, mode="w", header=True, index=False)
                        updated_symbol_count += 1

            except Exception as e:
                logger.error(f"{stock_file} 儲存失敗: {e}")
                if not is_append:
                    # Only remove the leftover file when overwrite mode failed;
                    # an append failure must not delete the original file
                    try:
                        os.remove(stock_file)
                    except OSError:
                        pass

        end_time = datetime.datetime.now()
        logger.info(f"結束: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(
            "download_summary: contracts=%d updated=%d skipped_failed=%d skip_reasons=%s request_errors=%d elapsed=%.0fs",
            len(contract_list),
            updated_symbol_count,
            skipped_failed_count,
            skip_reasons,
            request_error_count,
            (end_time - start_time).total_seconds(),
        )
        logger.info(f"程式共花費: {(end_time - start_time).total_seconds()} 秒")

    except DownloadAborted:
        # Deliberately not swallowed by the catch-all below: the point of
        # aborting is that the caller learns the run failed.
        raise
    except Exception as e:
        logger.error(f"發生未預期錯誤: {e}")
    finally:
        # A run aborted by DownloadAborted reaches here with a session the
        # server has already stopped answering, so logout can fail on its own.
        # Let that failure not replace the reason the run is ending.
        try:
            api.logout()
        except Exception as e:
            logger.warning(f"登出失敗，略過: {e}")

    return updated_symbol_count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download Shioaji minute price data.")
    parser.add_argument(
        "--codes",
        nargs="*",
        default=None,
        help="Optional stock codes, comma-separated or space-separated.",
    )
    parser.add_argument(
        "--start",
        default=None,
        help="Force re-fetch: YYYY-MM-DD start date (requires --end). Bypasses incremental resume.",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="Force re-fetch: YYYY-MM-DD end date (requires --start). Bypasses incremental resume.",
    )
    args = parser.parse_args()

    setup_logging("download_stock.log")
    try:
        main(codes=args.codes, start=args.start, end=args.end)
    except DownloadAborted as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc
