"""
Run Daily Module (Entry Point)

Runs the local stock data update flow end to end, so a single invocation
leaves all local data (minute/day prices, corporate actions, parquet cache,
official daily price, monthly revenue and institutional artifacts,
data-quality report) up to date:
1. `download_stock.main()`: download minute price data.
2. `convert_stock_price.process_all()`: convert minute data into daily bars.
3. `fetch_corporate_actions.run_fetch()`: refresh this year's ex-right /
   ex-dividend / capital-reduction index for every source in both markets
   (TWSE and TPEx), and close out the previous year once (see
   `_refresh_corporate_actions`).
4. `fetch_official_daily_price.run_fetch()`: refresh the latest trading day
   of official TWSE/TPEx bulk daily prices. A failure here degrades to "no
   official data refreshed this run" rather than aborting the other steps.
5. `build_price_parquet.build_price_parquet()`: rebuild the consolidated
   price_daily.parquet cache read by downstream consumers. Must run after
   step 4: the cache takes its values from the official artifact, so building
   first would leave the newest bar on the Shioaji-derived volume definition
   and flip it to the official one the next day.
6. `fetch_monthly_revenue.refresh_latest()`:
   Refresh the latest listed and OTC monthly-revenue batches, preserving the
   first local observation as conservative availability evidence.
7. `fetch_institutional.run_daily_fetch()`: refresh a rolling seven-calendar-day
   window of official TWSE/TPEx institutional-investor reports. Independent of
   steps 1-5; a failure is recorded without aborting the remaining steps.
8. `fetch_value_chain_classification.refresh_value_chain_classification()`:
   refresh the official TPEx industry value-chain snapshot when the latest
   valid observation is at least seven days old.
9. `check_data_integrity.run_health_check()`: scan local data for
   completeness, cross-check official vs Shioaji-derived daily data, and
   refresh data_quality_report.json. Must run after step 5: the cross-check
   reads this run's official data, so if it ran first it would compare
   against stale (or, on a first-ever run, entirely absent) official data.
10. `backup_data.run_backup()`: mirror the refreshed cache to the configured
   backup location. Runs last so it captures this run's output, and is
   skipped when any cache-writing step failed -- see `main()`.

Exits non-zero if any step failed, so an unattended scheduler reports a bad
run instead of silently succeeding.
"""

import argparse
import datetime
import logging
from pathlib import Path

import convert_stock_price
import download_stock
from backup_data import BACKUP_DIR_ENV, run_backup
from build_price_parquet import DEFAULT_DATA_PATH, build_price_parquet
from check_data_integrity import run_health_check
from dotenv import load_dotenv
from fetch_corporate_actions import DEFAULT_DB_PATH, DEFAULT_RAW_DIR, SOURCE_CONFIGS, run_fetch
from fetch_institutional import run_daily_fetch as run_institutional_fetch
from fetch_monthly_revenue import refresh_latest as refresh_monthly_revenue
from fetch_official_daily_price import run_fetch as run_official_daily_fetch
from fetch_symbol_meta import refresh_symbol_meta
from fetch_value_chain_classification import refresh_value_chain_classification

from config import config
from shp_utils.logging_utils import StepClock, launch_epoch_from_env, log_startup, setup_logging, start_heartbeat

logger = logging.getLogger(__name__)

# Task Scheduler stops StockProject-DailyUpdate after 3 hours (setup_scheduled_tasks.ps1).
# Heartbeats turn WARNING 30 minutes before that, measured from the launcher's start when the
# scheduled launcher provides it. With a 10-minute heartbeat the first WARNING lands 20-30 minutes
# ahead of the kill, so a run heading for it shows it.
SCHEDULER_TIME_LIMIT_SEC = 3 * 3600
HEARTBEAT_WARN_MARGIN_SEC = 30 * 60


def _corporate_action_fetch_args(source: str, year: int, *, force: bool) -> argparse.Namespace:
    return argparse.Namespace(
        source=source,
        start_year=year,
        end_year=year,
        start_date=None,
        end_date=None,
        sleep=1.5,
        retries=3,
        retry_sleep=5.0,
        force=force,
        db_path=DEFAULT_DB_PATH,
        raw_dir=DEFAULT_RAW_DIR,
    )


def _refresh_corporate_actions(today: datetime.date | None = None) -> None:
    """Refresh the ex-right / ex-dividend / capital-reduction index, both markets.

    Two fetches per source, and the second one is why this is not a one-liner.

    The current year is re-fetched every run with ``force``, because this
    year's index grows as events are announced. Its request range ends at
    today, so no single run ever covers a whole year: whatever the last run of
    December happened to cover is all that year ever gets. Miss the last few
    runs of the year -- a machine asleep over the new-year break is enough --
    and those days' events are never fetched again, because from January the
    year has rolled over and nothing looks back.

    So the previous year is also fetched, as one full 0101-1231 range and
    *without* ``force``. That range is a stable cache key, so the fetcher's own
    raw-range cache skips it after it succeeds once: the cost is three requests
    in one January run per year, and the year is closed out completely no
    matter how long the machine was off. It self-heals on any later run if that
    first attempt fails.

    Each source is isolated. The sources span two exchanges, so without isolation an
    outage at one of them would also cost the refresh of every source that happens to
    sort after it -- and `sorted()` interleaves the markets, so a TPEx outage would
    take TWSE sources down with it. Failures are collected and re-raised together at
    the end, so the step is still reported as failed while every reachable source has
    been refreshed.
    """
    today = today or datetime.date.today()
    failures: list[str] = []
    for source in sorted(SOURCE_CONFIGS):
        for year, force in ((today.year - 1, False), (today.year, True)):
            try:
                run_fetch(_corporate_action_fetch_args(source, year, force=force))
            except Exception:
                logger.exception("除權息來源 %s %s 年更新失敗", source, year)
                failures.append(f"{source}:{year}")
    if failures:
        raise RuntimeError(f"corporate-action refresh failed for: {', '.join(failures)}")


def main(codes: list[str] | None = None, backup_dir: str | None = None, archive: bool = False) -> int:
    """Run the daily refresh. Returns 0 when every step succeeded, 1 otherwise.

    Steps are divided into two classes. Steps 1, 2 and 5 write the price cache,
    so a failure there can leave a truncated file; they block the backup,
    because mirroring a truncated file over a good backup copy is the one way
    an additive backup can still lose data. Steps 3, 5, 6, 7 and 8 are recorded but
    do not block: step 3 writes a separate corporate-action database inside a
    an additive backup can still lose data. Steps 3, 5, 6, 6b, 7, 8 and 9 are
    recorded but do not block: step 3 writes a separate corporate-action
    database inside a transaction and never touches the price cache, steps
    5-8 write independent official-data artifacts, and step 9 only writes a
    report.
    Downloading nothing is not a failure -- that is simply a non-trading day.
    """
    setup_logging("run_daily.log")
    # Load .env up front rather than relying on a later step to do it, so
    # machine-local settings (SHIOAJI_BACKUP_DIR) are available to every step
    # regardless of which ones run. This checkout's own file only; see
    # config.ENV_FILE.
    load_dotenv(config.ENV_FILE)

    cache_failures: list[str] = []
    other_failures: list[str] = []

    logger.info("=== 日常更新作業開始 %s ===", datetime.datetime.now())
    log_startup(logger, "run_daily")
    heartbeat_stop = start_heartbeat(
        logger,
        warn_after_seconds=SCHEDULER_TIME_LIMIT_SEC - HEARTBEAT_WARN_MARGIN_SEC,
        launch_epoch=launch_epoch_from_env(),
    )
    step_clock = StepClock(logger)
    logger.info("data directory: %s (%s)", config.DATA_DIR, config.DATA_DIR_SOURCE)
    logger.info("\n--- 步驟 1: 下載股票資料 ---")
    step_clock.begin("步驟 1")
    updated_symbol_count = 0
    try:
        updated_symbol_count = download_stock.main(codes=codes)
    except download_stock.DownloadAborted as exc:
        # Still a failed step (so the backup is skipped), but the symbols
        # fetched before the abort are already on disk and deserve converting.
        logger.exception("下載過程中發生錯誤")
        updated_symbol_count = exc.updated_symbol_count
        cache_failures.append("步驟 1 下載")
    except Exception:
        logger.exception("下載過程中發生錯誤")
        cache_failures.append("步驟 1 下載")

    logger.info("\n--- 步驟 2: 轉換資料 (分K -> 日K) ---")
    step_clock.begin("步驟 2")
    if updated_symbol_count:
        logger.info("步驟 1 更新了 %s 檔標的，開始分K轉日K轉換", updated_symbol_count)
        try:
            convert_stock_price.process_all(codes=codes)
        except Exception:
            logger.exception("轉檔過程中發生錯誤")
            cache_failures.append("步驟 2 轉檔")
    else:
        # A legitimate quiet day (a holiday the scheduler still fired on) looks
        # exactly like a broken step 1 from here, so this cannot be an error --
        # but it must not read as routine either. Two such nights in a row is
        # worth investigating; 2026-08-27 and 08-28 were both this line at INFO.
        logger.warning("步驟 1 沒有下載到任何新資料，略過分K轉日K轉換；若今天是交易日請檢查步驟 1 的合約清單筆數")

    logger.info("\n--- 步驟 3: 更新除權息資料 ---")
    step_clock.begin("步驟 3")
    try:
        _refresh_corporate_actions()
    except Exception:
        # Recorded, but deliberately not a cache failure: this step writes only
        # corporate_actions.sqlite, one transaction per fetched range, so a failure
        # leaves the price cache untouched and that database consistent. Blocking
        # the backup here traded a transient TWSE outage for a night with no backup
        # of the expensive-to-reacquire price data (2026-08-19).
        logger.exception("除權息更新過程中發生錯誤")
        other_failures.append("步驟 3 除權息")

    logger.info("\n--- 步驟 4: 下載官方每日股價 (TWSE/TPEx) ---")
    step_clock.begin("步驟 4")
    try:
        official_counts = run_official_daily_fetch()
        logger.info("official_daily_fetch_counts: %s", official_counts)
    except Exception:
        logger.exception("官方每日股價下載過程中發生錯誤")
        other_failures.append("步驟 4 官方股價")

    # After the official fetch, not before it. The parquet takes its values from
    # the official artifact now, so building first would leave today's bar
    # Shioaji-sourced and flip it to official tomorrow -- and the two do not
    # publish the same volume (TWSE's includes odd lots, ~1.5% higher). That is a
    # definition change on the newest bar, every day, exactly where a volume-surge
    # rule looks.
    logger.info("\n--- 步驟 5: 重建 price_daily.parquet 快取 ---")
    step_clock.begin("步驟 5")
    try:
        build_price_parquet(Path(DEFAULT_DATA_PATH))
    except Exception:
        logger.exception("parquet 重建過程中發生錯誤")
        cache_failures.append("步驟 5 parquet")

    logger.info("\n--- 步驟 6: 更新上市與上櫃月營收 ---")
    step_clock.begin("步驟 6")
    try:
        monthly_revenue_counts = refresh_monthly_revenue()
        logger.info("monthly_revenue_refresh_counts: %s", monthly_revenue_counts)
    except Exception:
        logger.exception("月營收更新過程中發生錯誤")
        other_failures.append("步驟 6 月營收")

    # Numbered 6b so the established step numbers in logs and docs keep their meaning.
    # Before step 7, which reads symbol_meta to tell ETFs from stocks, so a newly listed
    # code is not recorded as unknown; the health check reads it for categories too.
    logger.info("\n--- 步驟 6b: 更新證券基本資料 (symbol_meta) ---")
    step_clock.begin("步驟 6b")
    try:
        symbol_meta_refreshed = refresh_symbol_meta()
        logger.info("symbol_meta_refreshed: %s", symbol_meta_refreshed)
    except Exception:
        logger.exception("證券基本資料更新過程中發生錯誤")
        other_failures.append("步驟 6b 證券基本資料")

    logger.info("\n--- 步驟 7: 下載三大法人每日買賣 (TWSE/TPEx) ---")
    step_clock.begin("步驟 7")
    try:
        institutional_counts = run_institutional_fetch()
        logger.info("institutional_daily_fetch_counts: %s", institutional_counts)
    except Exception:
        logger.exception("三大法人每日買賣下載過程中發生錯誤")
        other_failures.append("步驟 7 三大法人")

    logger.info("\n--- 步驟 8: 更新官方產業價值鏈分類 ---")
    step_clock.begin("步驟 8")
    try:
        classification_result = refresh_value_chain_classification()
        logger.info("value_chain_classification_refreshed: %s", classification_result.refreshed)
    except Exception:
        logger.exception("官方產業價值鏈分類更新過程中發生錯誤")
        other_failures.append("步驟 8 產業價值鏈分類")

    logger.info("\n--- 步驟 9: 檢查資料完整性 ---")
    step_clock.begin("步驟 9")
    try:
        run_health_check(DEFAULT_DATA_PATH)
    except Exception:
        logger.exception("資料完整性檢查過程中發生錯誤")
        other_failures.append("步驟 9 完整性檢查")

    logger.info("\n--- 步驟 10: 備份資料快取 ---")
    step_clock.begin("步驟 10")
    if cache_failures:
        logger.warning("前置步驟失敗，略過備份以免把不完整的檔案覆蓋到備份上：%s", cache_failures)
    else:
        try:
            run_backup(backup_dir, data_dir=DEFAULT_DATA_PATH, archive=archive)
        except Exception:
            logger.exception("備份過程中發生錯誤")
            other_failures.append("步驟 10 備份")

    step_clock.finish()
    heartbeat_stop.set()
    logger.info("\n=== 作業結束 %s ===", datetime.datetime.now())

    failures = cache_failures + other_failures
    if failures:
        logger.error("本次執行有步驟失敗：%s", failures)
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download and convert local stock data.")
    parser.add_argument(
        "--codes",
        nargs="*",
        default=None,
        help="Optional stock codes, comma-separated or space-separated.",
    )
    parser.add_argument(
        "--backup-dir",
        default=None,
        help=f"Back up the data cache here after a successful run. Defaults to ${BACKUP_DIR_ENV}.",
    )
    parser.add_argument(
        "--archive",
        action="store_true",
        help="Also build quarterly split archives under <backup-dir>/archive.",
    )
    args = parser.parse_args()
    raise SystemExit(main(codes=args.codes, backup_dir=args.backup_dir, archive=args.archive))
