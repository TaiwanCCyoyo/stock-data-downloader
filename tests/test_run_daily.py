"""Pytest coverage for run_daily.py's step sequencing and failure isolation.

Run with: uv run pytest tests/test_run_daily.py -v
"""

from __future__ import annotations

import argparse
import datetime

import pytest
import run_daily


def _patch_all_steps(monkeypatch: pytest.MonkeyPatch, call_order: list[str], *, downloaded_count: int = 1) -> None:
    def record_step(name: str, result: object = None) -> object:
        call_order.append(name)
        return result

    monkeypatch.setattr(run_daily, "setup_logging", lambda *_a, **_kw: None)
    monkeypatch.setattr(run_daily.download_stock, "main", lambda **_kw: record_step("download", downloaded_count))
    monkeypatch.setattr(run_daily.convert_stock_price, "process_all", lambda **_kw: call_order.append("convert"))
    monkeypatch.setattr(run_daily, "_refresh_corporate_actions", lambda: call_order.append("corporate_actions"))
    monkeypatch.setattr(run_daily, "build_price_parquet", lambda *_a, **_kw: call_order.append("parquet"))
    monkeypatch.setattr(run_daily, "run_official_daily_fetch", lambda: record_step("official_fetch", {}))
    monkeypatch.setattr(run_daily, "refresh_monthly_revenue", lambda: record_step("monthly_revenue", {}))
    monkeypatch.setattr(
        run_daily,
        "run_institutional_fetch",
        lambda: record_step("institutional_fetch", {}),
    )
    monkeypatch.setattr(
        run_daily,
        "refresh_value_chain_classification",
        lambda: record_step("value_chain", type("Result", (), {"refreshed": True})()),
    )
    monkeypatch.setattr(run_daily, "refresh_symbol_meta", lambda: record_step("symbol_meta", False))
    monkeypatch.setattr(run_daily, "run_health_check", lambda *_a, **_kw: call_order.append("health_check"))
    monkeypatch.setattr(run_daily, "run_backup", lambda *_a, **_kw: record_step("backup", True))


def test_official_fetch_runs_before_health_check(monkeypatch: pytest.MonkeyPatch):
    # The health check cross-references official_daily.sqlite against Shioaji-derived
    # data; if the official fetch ran after it, every run would cross-check against
    # stale (previous-run) or entirely absent official data.
    call_order: list[str] = []
    _patch_all_steps(monkeypatch, call_order)

    run_daily.main()

    assert call_order.index("official_fetch") < call_order.index("health_check")


def test_institutional_fetch_runs_before_health_check(monkeypatch: pytest.MonkeyPatch):
    call_order: list[str] = []
    _patch_all_steps(monkeypatch, call_order)

    run_daily.main()

    assert call_order.index("institutional_fetch") < call_order.index("health_check")


def test_all_steps_run_in_one_invocation(monkeypatch: pytest.MonkeyPatch):
    call_order: list[str] = []
    _patch_all_steps(monkeypatch, call_order)

    run_daily.main()

    assert set(call_order) == {
        "download",
        "convert",
        "corporate_actions",
        "parquet",
        "official_fetch",
        "monthly_revenue",
        "institutional_fetch",
        "value_chain",
        "symbol_meta",
        "health_check",
        "backup",
    }


def test_official_fetch_failure_does_not_prevent_health_check(monkeypatch: pytest.MonkeyPatch):
    call_order: list[str] = []
    _patch_all_steps(monkeypatch, call_order)

    def failing_official_fetch():
        call_order.append("official_fetch")
        raise RuntimeError("official endpoint unavailable")

    monkeypatch.setattr(run_daily, "run_official_daily_fetch", failing_official_fetch)

    run_daily.main()

    assert "health_check" in call_order


def test_a_failing_step_does_not_prevent_the_others(monkeypatch: pytest.MonkeyPatch):
    call_order: list[str] = []
    _patch_all_steps(monkeypatch, call_order)
    monkeypatch.setattr(run_daily.download_stock, "main", lambda **_kw: (_ for _ in ()).throw(RuntimeError("boom")))

    run_daily.main()

    # Conversion is skipped too: a raised download step left nothing new to
    # convert, same as a clean run that downloaded zero symbols (see
    # test_conversion_is_skipped_when_nothing_new_downloaded). The other
    # steps, which don't depend on download's output, still run.
    assert set(call_order) == {
        "corporate_actions",
        "parquet",
        "official_fetch",
        "monthly_revenue",
        "institutional_fetch",
        "value_chain",
        "symbol_meta",
        "health_check",
    }


def test_conversion_is_skipped_when_nothing_new_downloaded(monkeypatch: pytest.MonkeyPatch):
    # download_stock.main() returns 0 when every symbol was already up to
    # date (e.g. a non-trading day) -- running the minute-to-daily conversion
    # over unchanged files would just waste CPU, so it must not run.
    call_order: list[str] = []
    _patch_all_steps(monkeypatch, call_order, downloaded_count=0)

    run_daily.main()

    assert "convert" not in call_order
    assert set(call_order) == {
        "download",
        "corporate_actions",
        "parquet",
        "official_fetch",
        "monthly_revenue",
        "institutional_fetch",
        "value_chain",
        "symbol_meta",
        "health_check",
        "backup",
    }


def test_conversion_runs_when_new_data_was_downloaded(monkeypatch: pytest.MonkeyPatch):
    call_order: list[str] = []
    _patch_all_steps(monkeypatch, call_order, downloaded_count=3)

    run_daily.main()

    assert "convert" in call_order


def test_a_clean_run_exits_zero_and_backs_up(monkeypatch: pytest.MonkeyPatch):
    call_order: list[str] = []
    _patch_all_steps(monkeypatch, call_order)

    assert run_daily.main() == 0
    assert "backup" in call_order


def test_a_failed_step_makes_the_run_exit_non_zero(monkeypatch: pytest.MonkeyPatch):
    # An unattended scheduler can only notice a bad night if the process says
    # so; every step logging its own exception and exiting 0 is a monitoring
    # blind spot, not failure isolation.
    call_order: list[str] = []
    _patch_all_steps(monkeypatch, call_order)
    monkeypatch.setattr(run_daily, "run_official_daily_fetch", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    assert run_daily.main() == 1


def test_backup_is_skipped_when_a_cache_writing_step_failed(monkeypatch: pytest.MonkeyPatch):
    # A failed download/convert/parquet can leave a truncated file. Mirroring
    # that over a good backup copy is the one way an additive backup can still
    # lose data, so the backup must not run.
    call_order: list[str] = []
    _patch_all_steps(monkeypatch, call_order)
    monkeypatch.setattr(run_daily, "build_price_parquet", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("boom")))

    assert run_daily.main() == 1
    assert "backup" not in call_order


def test_backup_still_runs_when_only_a_non_cache_step_failed(monkeypatch: pytest.MonkeyPatch):
    # The official-price fetch is specified to degrade to "no official data
    # refreshed this run" rather than abort, so it must not cost the night's
    # backup of data that is already on disk and correct.
    call_order: list[str] = []
    _patch_all_steps(monkeypatch, call_order)
    monkeypatch.setattr(run_daily, "run_official_daily_fetch", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    run_daily.main()

    assert "backup" in call_order


def test_value_chain_failure_does_not_prevent_health_check_or_backup(monkeypatch: pytest.MonkeyPatch):
    call_order: list[str] = []
    _patch_all_steps(monkeypatch, call_order)

    def failing_value_chain_refresh():
        call_order.append("value_chain")
        raise RuntimeError("classification source unavailable")

    monkeypatch.setattr(run_daily, "refresh_value_chain_classification", failing_value_chain_refresh)

    assert run_daily.main() == 1
    assert "health_check" in call_order
    assert "backup" in call_order


def test_symbol_meta_failure_does_not_prevent_health_check_or_backup(monkeypatch: pytest.MonkeyPatch):
    call_order: list[str] = []
    _patch_all_steps(monkeypatch, call_order)

    def failing_symbol_meta_refresh():
        call_order.append("symbol_meta")
        raise RuntimeError("isin page unavailable")

    monkeypatch.setattr(run_daily, "refresh_symbol_meta", failing_symbol_meta_refresh)

    assert run_daily.main() == 1
    assert call_order.index("symbol_meta") < call_order.index("health_check")
    assert "backup" in call_order


def test_monthly_revenue_failure_does_not_prevent_later_steps_or_backup(monkeypatch: pytest.MonkeyPatch):
    call_order: list[str] = []
    _patch_all_steps(monkeypatch, call_order)

    def failing_monthly_revenue_refresh():
        call_order.append("monthly_revenue")
        raise RuntimeError("monthly revenue source unavailable")

    monkeypatch.setattr(run_daily, "refresh_monthly_revenue", failing_monthly_revenue_refresh)

    assert run_daily.main() == 1
    assert "value_chain" in call_order
    assert "health_check" in call_order
    assert "backup" in call_order


def test_institutional_failure_does_not_prevent_health_check_or_backup(monkeypatch: pytest.MonkeyPatch):
    call_order: list[str] = []
    _patch_all_steps(monkeypatch, call_order)

    def failing_institutional_fetch():
        call_order.append("institutional_fetch")
        raise RuntimeError("institutional source unavailable")

    monkeypatch.setattr(run_daily, "run_institutional_fetch", failing_institutional_fetch)

    assert run_daily.main() == 1
    assert "health_check" in call_order
    assert "backup" in call_order


def test_downloading_nothing_is_not_a_failure(monkeypatch: pytest.MonkeyPatch):
    # A non-trading day downloads zero symbols. Treating that as a failure
    # would silently stop backups on every weekend and holiday.
    call_order: list[str] = []
    _patch_all_steps(monkeypatch, call_order, downloaded_count=0)

    assert run_daily.main() == 0
    assert "backup" in call_order


def test_an_aborted_download_fails_the_run_and_skips_the_backup(monkeypatch: pytest.MonkeyPatch):
    # download_stock gives up when the API stops answering. That is a failed
    # cache-writing step: the run must exit non-zero and must not copy a
    # half-updated cache over a good backup.
    call_order: list[str] = []
    _patch_all_steps(monkeypatch, call_order)

    def aborting_download(**_kw):
        call_order.append("download")
        raise run_daily.download_stock.DownloadAborted("API stopped answering", updated_symbol_count=3)

    monkeypatch.setattr(run_daily.download_stock, "main", aborting_download)

    assert run_daily.main() == 1
    assert "backup" not in call_order
    # The symbols fetched before the abort are already on disk, so their minute
    # bars still have to be converted -- otherwise they sit unconverted until
    # some later run happens to touch them again.
    assert "convert" in call_order
    assert {
        "corporate_actions",
        "parquet",
        "official_fetch",
        "monthly_revenue",
        "institutional_fetch",
        "value_chain",
        "symbol_meta",
        "health_check",
    } <= set(call_order)


def test_an_aborted_download_that_fetched_nothing_skips_conversion(monkeypatch: pytest.MonkeyPatch):
    # Aborting before any symbol was written leaves nothing to convert.
    call_order: list[str] = []
    _patch_all_steps(monkeypatch, call_order)

    def aborting_download(**_kw):
        call_order.append("download")
        raise run_daily.download_stock.DownloadAborted("API stopped answering")

    monkeypatch.setattr(run_daily.download_stock, "main", aborting_download)

    assert run_daily.main() == 1
    assert "convert" not in call_order


def test_a_corporate_action_failure_does_not_cost_the_night_s_backup(monkeypatch: pytest.MonkeyPatch):
    # Step 3 writes only corporate_actions.sqlite, one transaction per fetched
    # range, so it cannot leave a truncated price file -- the risk the whole
    # skip-the-backup rule exists for. On 2026-08-19 a transient TWSE read
    # timeout in this step cost a night's backup of the price cache, which is
    # the expensive-to-reacquire data the backup exists to protect.
    call_order: list[str] = []
    _patch_all_steps(monkeypatch, call_order)
    monkeypatch.setattr(run_daily, "_refresh_corporate_actions", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    assert run_daily.main() == 1
    assert "backup" in call_order


def test_corporate_action_refresh_closes_out_the_previous_year(monkeypatch: pytest.MonkeyPatch):
    # The forced current-year fetch ends at today, so a year only ever gets
    # whatever its last run covered. Without a look back, a machine asleep over
    # the new-year break loses those days permanently: from January the year has
    # rolled over and nothing revisits it.
    requested: list[tuple[str, int, bool]] = []
    monkeypatch.setattr(run_daily, "run_fetch", lambda args: requested.append((args.source, args.start_year, args.force)))

    run_daily._refresh_corporate_actions(today=datetime.date(2027, 1, 3))

    for source in ("twt49u", "twtauu", "twtcau"):
        assert (source, 2026, False) in requested, "the previous year must be closed out, uncached"
        assert (source, 2027, True) in requested, "the current year must still be force-refreshed"
    # The close-out must come first, so a year is complete before the new one is
    # appended to it.
    assert requested.index(("twt49u", 2026, False)) < requested.index(("twt49u", 2027, True))


def test_corporate_action_refresh_asks_for_whole_years_only(monkeypatch: pytest.MonkeyPatch):
    # The previous-year request must be a full 0101-1231 range: that is what
    # makes it a stable cache key the fetcher can skip on every later run.
    requested: list[tuple[int, int]] = []
    monkeypatch.setattr(run_daily, "run_fetch", lambda args: requested.append((args.start_year, args.end_year)))

    run_daily._refresh_corporate_actions(today=datetime.date(2027, 1, 3))

    assert all(start == end for start, end in requested)


def test_one_markets_corporate_action_failure_does_not_cost_the_others(monkeypatch: pytest.MonkeyPatch):
    # The corporate-action sources span two exchanges and `sorted()` interleaves them
    # (etfrvs, etfsplit, exdailyq, revivt, twt49u, twtauu, twtcau), so without
    # per-source isolation a TPEx outage would abort every TWSE source that happens
    # to sort after it -- costing the market that was working fine.
    attempted: list[str] = []

    def flaky_run_fetch(args: argparse.Namespace) -> None:
        attempted.append(args.source)
        if args.source.startswith("etf") or args.source in ("exdailyq", "revivt"):
            raise RuntimeError("TPEx is down")

    monkeypatch.setattr(run_daily, "run_fetch", flaky_run_fetch)

    with pytest.raises(RuntimeError) as excinfo:
        run_daily._refresh_corporate_actions(today=datetime.date(2026, 8, 29))

    # Every TWSE source was still attempted, for both years.
    for source in ("twt49u", "twtauu", "twtcau"):
        assert attempted.count(source) == 2, f"{source} must still be refreshed"
    # And the step still reports failure, so run_daily records it.
    assert "exdailyq" in str(excinfo.value)


def test_a_clean_corporate_action_refresh_raises_nothing(monkeypatch: pytest.MonkeyPatch):
    attempted: list[str] = []
    monkeypatch.setattr(run_daily, "run_fetch", lambda args: attempted.append(args.source))

    run_daily._refresh_corporate_actions(today=datetime.date(2026, 8, 29))

    assert len(attempted) == 2 * len(run_daily.SOURCE_CONFIGS)


def test_parquet_rebuild_runs_after_the_official_fetch(monkeypatch: pytest.MonkeyPatch):
    # The cache takes its values from the official artifact now. Building it first
    # would leave the newest bar Shioaji-sourced and flip it to official the next
    # day -- and the two do not publish the same volume, so that is a definition
    # change on the freshest bar every single day, exactly where a volume-surge
    # rule looks. This is the same failure the TPEx seam fix addressed, one layer up.
    call_order: list[str] = []
    _patch_all_steps(monkeypatch, call_order)

    run_daily.main()

    assert call_order.index("official_fetch") < call_order.index("parquet")
