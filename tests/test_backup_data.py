import datetime
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import backup_data
import pytest
from backup_data import (
    BackupError,
    build_quarterly_archives,
    mirror_data_cache,
    quarter_label,
    resolve_backup_dir,
    run_backup,
    verify_mirror,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    """A repo-shaped source: <repo>/data with one file of each class."""
    source = tmp_path / "repo" / "data"
    source.mkdir(parents=True)
    (source / "2330_day.csv").write_text("day bars\n", encoding="utf-8")
    (source / "2330_min.csv").write_text("minute bars\n", encoding="utf-8")
    (source / "stock_category.json5").write_text("{}\n", encoding="utf-8")
    (source / "corporate_actions.sqlite").write_text("corporate actions\n", encoding="utf-8")
    (source / "monthly_revenue.sqlite").write_text("monthly revenue\n", encoding="utf-8")
    (source / "symbol_meta.sqlite").write_text("symbol meta\n", encoding="utf-8")
    (source / "official_daily.sqlite").write_text("official daily\n", encoding="utf-8")
    (source / "raw").mkdir()
    (source / "raw" / "staging.json").write_text("{}\n", encoding="utf-8")
    return source


def test_mirror_copies_every_file_including_subdirectories(data_dir: Path, tmp_path: Path) -> None:
    dest = tmp_path / "backup"

    copied, verified = mirror_data_cache(data_dir, dest)

    assert copied == 8
    assert verified == 8
    assert (dest / "data" / "2330_day.csv").read_text(encoding="utf-8") == "day bars\n"
    assert (dest / "data" / "raw" / "staging.json").is_file()


def _pretend_link(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    original = backup_data._is_link_or_reparse
    monkeypatch.setattr(backup_data, "_is_link_or_reparse", lambda path: path == target or original(path))


def test_a_link_inside_the_cache_is_refused_without_costing_the_rest(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Refusing to follow a link must not become another all-or-nothing abort.

    raw/staging.json sorts before symbol_meta.sqlite, so an abort here would take
    the files after it down too -- the failure mirror_data_cache stopped having.
    """
    _pretend_link(monkeypatch, data_dir / "raw" / "staging.json")
    dest = tmp_path / "backup"

    with pytest.raises(backup_data.BackupError, match="could not be mirrored"):
        mirror_data_cache(data_dir, dest)

    assert "link or reparse point: raw" in caplog.text
    assert not (dest / "data" / "raw" / "staging.json").exists()
    assert (dest / "data" / "2330_day.csv").is_file()
    assert (dest / "data" / "symbol_meta.sqlite").is_file()


def test_a_link_at_the_data_root_is_fatal(monkeypatch: pytest.MonkeyPatch, data_dir: Path, tmp_path: Path) -> None:
    """rglob follows it, so every path beneath would look ordinary while the mirror described another directory."""
    _pretend_link(monkeypatch, data_dir)

    with pytest.raises(backup_data.BackupError, match="is itself a link"):
        mirror_data_cache(data_dir, tmp_path / "backup")


def test_verify_does_not_report_a_refused_link_as_missing(monkeypatch: pytest.MonkeyPatch, data_dir: Path, tmp_path: Path) -> None:
    """A path the mirror refuses is not a path the mirror is missing."""
    dest = tmp_path / "backup"
    mirror_data_cache(data_dir, dest)
    (dest / "data" / "raw" / "staging.json").unlink()
    _pretend_link(monkeypatch, data_dir / "raw" / "staging.json")

    assert verify_mirror(data_dir, dest / "data") == []


def test_mirror_keeps_a_file_the_source_has_lost(data_dir: Path, tmp_path: Path) -> None:
    """The property the whole design exists for: deletions must not propagate.

    A conventional mirror would remove the file from the backup too, turning
    one accidental deletion into two lost copies.
    """
    dest = tmp_path / "backup"
    mirror_data_cache(data_dir, dest)

    (data_dir / "2330_min.csv").unlink()
    mirror_data_cache(data_dir, dest)

    assert (dest / "data" / "2330_min.csv").read_text(encoding="utf-8") == "minute bars\n"


def test_mirror_is_incremental_on_a_second_run(data_dir: Path, tmp_path: Path) -> None:
    dest = tmp_path / "backup"
    mirror_data_cache(data_dir, dest)

    copied, _ = mirror_data_cache(data_dir, dest)

    assert copied == 0


def test_mirror_recopies_a_changed_file(data_dir: Path, tmp_path: Path) -> None:
    dest = tmp_path / "backup"
    mirror_data_cache(data_dir, dest)

    (data_dir / "2330_day.csv").write_text("day bars, plus today\n", encoding="utf-8")
    copied, _ = mirror_data_cache(data_dir, dest)

    assert copied == 1
    assert (dest / "data" / "2330_day.csv").read_text(encoding="utf-8") == "day bars, plus today\n"


def test_mirror_refuses_a_destination_inside_the_repo(data_dir: Path) -> None:
    inside = data_dir.parent / "backups"

    with pytest.raises(BackupError, match="is not a backup"):
        mirror_data_cache(data_dir, inside)


def test_verify_reports_a_missing_file(data_dir: Path, tmp_path: Path) -> None:
    dest = tmp_path / "backup"
    mirror_data_cache(data_dir, dest)
    (dest / "data" / "2330_day.csv").unlink()

    problems = verify_mirror(data_dir, dest / "data")

    assert any("missing: 2330_day.csv" in problem for problem in problems)


def test_verify_reports_a_size_mismatch(data_dir: Path, tmp_path: Path) -> None:
    dest = tmp_path / "backup"
    mirror_data_cache(data_dir, dest)
    (dest / "data" / "2330_day.csv").write_text("truncated", encoding="utf-8")

    problems = verify_mirror(data_dir, dest / "data")

    assert any("size mismatch: 2330_day.csv" in problem for problem in problems)


def test_run_backup_verify_only_raises_when_the_backup_is_incomplete(data_dir: Path, tmp_path: Path) -> None:
    dest = tmp_path / "backup"
    mirror_data_cache(data_dir, dest)
    (dest / "data" / "2330_min.csv").unlink()

    with pytest.raises(BackupError, match="verification failed"):
        run_backup(dest, data_dir=data_dir, verify_only=True)


def test_run_backup_without_a_destination_is_a_skip_not_a_failure(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHIOAJI_BACKUP_DIR", raising=False)

    assert run_backup(None, data_dir=data_dir) is False


def test_resolve_backup_dir_falls_back_to_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHIOAJI_BACKUP_DIR", str(tmp_path))

    assert resolve_backup_dir() == tmp_path.resolve()


def test_resolve_backup_dir_prefers_an_explicit_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHIOAJI_BACKUP_DIR", str(tmp_path / "from_env"))

    assert resolve_backup_dir(tmp_path / "explicit") == (tmp_path / "explicit").resolve()


def test_quarter_label_maps_months_to_quarters() -> None:
    assert quarter_label(datetime.date(2026, 1, 31)) == "2026Q1"
    assert quarter_label(datetime.date(2026, 3, 1)) == "2026Q1"
    assert quarter_label(datetime.date(2026, 4, 1)) == "2026Q2"
    assert quarter_label(datetime.date(2026, 12, 31)) == "2026Q4"


def test_archives_split_daily_and_minute_bars(data_dir: Path, tmp_path: Path) -> None:
    dest = tmp_path / "backup"
    mirror_data_cache(data_dir, dest)

    written = build_quarterly_archives(dest, today=datetime.date(2026, 8, 14))

    names = {path.name for path in written}
    assert names == {"data_day_2026Q3.tar.gz", "data_min_2026Q3.tar.gz", "data_meta_2026Q3.tar.gz"}

    with tarfile.open(dest / "archive" / "data_day_2026Q3.tar.gz") as tar:
        assert sorted(tar.getnames()) == ["2330_day.csv", "stock_category.json5"]
    with tarfile.open(dest / "archive" / "data_min_2026Q3.tar.gz") as tar:
        assert tar.getnames() == ["2330_min.csv"]


def test_archives_include_metadata_databases_but_not_the_point_in_time_stores(data_dir: Path, tmp_path: Path) -> None:
    """The small SQLite artifacts are cheap to keep; the point-in-time stores are mirror-only.

    official_daily.sqlite, monthly_revenue.sqlite and institutional.sqlite are all
    WAL databases, and the raw responses beside the latter two follow the same rule
    so that one policy covers the whole point-in-time side rather than one per
    pipeline.
    """
    (data_dir / "institutional.sqlite").write_text("institutional\n", encoding="utf-8")
    (data_dir / "value_chain_classification.json").write_text('{"schema_version": 1}\n', encoding="utf-8")
    history_dir = data_dir / "value_chain_classification_history"
    history_dir.mkdir()
    (history_dir / "2026-08-14T120000Z.json").write_text('{"schema_version": 1}\n', encoding="utf-8")
    revenue_raw_dir = data_dir / "raw" / "monthly_revenue" / "mops_openapi_latest" / "TWSE" / "latest"
    revenue_raw_dir.mkdir(parents=True)
    (revenue_raw_dir / "20260814T120000Z_abc123.json").write_text("[]\n", encoding="utf-8")
    institutional_raw_dir = data_dir / "raw" / "institutional" / "TWSE"
    institutional_raw_dir.mkdir(parents=True)
    (institutional_raw_dir / "20260814T120000Z_def456.json").write_text("[]\n", encoding="utf-8")
    dest = tmp_path / "backup"
    mirror_data_cache(data_dir, dest)

    build_quarterly_archives(dest, today=datetime.date(2026, 8, 14))

    with tarfile.open(dest / "archive" / "data_meta_2026Q3.tar.gz") as tar:
        assert sorted(tar.getnames()) == [
            "corporate_actions.sqlite",
            "symbol_meta.sqlite",
            "value_chain_classification.json",
            "value_chain_classification_history/2026-08-14T120000Z.json",
        ]

    # Mirror-only, but genuinely mirrored: excluded from the archives is not
    # excluded from the backup.
    for relative in (
        "monthly_revenue.sqlite",
        "institutional.sqlite",
        "raw/monthly_revenue/mops_openapi_latest/TWSE/latest/20260814T120000Z_abc123.json",
        "raw/institutional/TWSE/20260814T120000Z_def456.json",
    ):
        assert (dest / "data" / relative).is_file()

    archived: set[str] = set()
    for path in (dest / "archive").glob("*.tar.gz"):
        with tarfile.open(path) as tar:
            archived.update(tar.getnames())
    assert not [name for name in archived if name.startswith("raw/")]
    for excluded in ("official_daily.sqlite", "monthly_revenue.sqlite", "institutional.sqlite"):
        assert excluded not in archived


def test_archive_round_trips(data_dir: Path, tmp_path: Path) -> None:
    dest = tmp_path / "backup"
    mirror_data_cache(data_dir, dest)
    build_quarterly_archives(dest, today=datetime.date(2026, 8, 14))

    restore = tmp_path / "restore"
    restore.mkdir()
    with tarfile.open(dest / "archive" / "data_min_2026Q3.tar.gz") as tar:
        tar.extractall(restore, filter="data")

    assert (restore / "2330_min.csv").read_text(encoding="utf-8") == "minute bars\n"


def test_archiving_without_a_mirror_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="No mirror to archive"):
        build_quarterly_archives(tmp_path / "empty")


def test_verify_accepts_a_copy_whose_source_moved_on_after_it(data_dir: Path, tmp_path: Path) -> None:
    """A growing source is not a bad backup.

    official_daily.sqlite and its -wal sidecar are written all evening by the
    historical backfill, so by the time the copy is verified the source has
    advanced. On 2026-08-20 that failed the whole nightly backup over a file
    that had copied perfectly well.
    """
    dest = tmp_path / "backup"
    mirror_data_cache(data_dir, dest)

    source_file = data_dir / "official_daily.sqlite"
    source_file.write_text("official daily, plus everything since\n", encoding="utf-8")
    now = time.time()
    os.utime(source_file, (now, now))
    os.utime(dest / "data" / "official_daily.sqlite", (now - 60, now - 60))

    assert verify_mirror(data_dir, dest / "data") == []


def test_verify_still_reports_a_short_copy_of_an_unchanged_source(data_dir: Path, tmp_path: Path) -> None:
    """The tolerance must not swallow a genuinely broken copy."""
    dest = tmp_path / "backup"
    mirror_data_cache(data_dir, dest)

    truncated = dest / "data" / "official_daily.sqlite"
    truncated.write_text("trunc", encoding="utf-8")
    source_mtime = (data_dir / "official_daily.sqlite").stat().st_mtime
    os.utime(truncated, (source_mtime, source_mtime))

    problems = verify_mirror(data_dir, dest / "data")

    assert any("size mismatch: official_daily.sqlite" in problem for problem in problems)


def test_the_sqlite_shm_sidecar_is_not_mirrored(data_dir: Path, tmp_path: Path) -> None:
    """-shm is shared memory, rebuilt on open, and locked while a writer is attached.

    Copying it protects nothing and is the one file in the cache guaranteed to
    be unreadable exactly when the nightly backup runs.
    """
    (data_dir / "official_daily.sqlite-shm").write_text("shared memory\n", encoding="utf-8")
    (data_dir / "official_daily.sqlite-wal").write_text("write-ahead log\n", encoding="utf-8")
    dest = tmp_path / "backup"

    copied, verified = mirror_data_cache(data_dir, dest)

    assert not (dest / "data" / "official_daily.sqlite-shm").exists()
    assert (dest / "data" / "official_daily.sqlite-wal").is_file()
    assert copied == verified == 9


def test_a_locked_file_does_not_cost_the_mirror_the_files_after_it(data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The 2026-08-24 failure: one lock, and everything sorting after it went unmirrored.

    official_daily.sqlite sorts before price_daily.parquet, so a sharing
    violation on the database left the daily bars -- the thing the backup exists
    for -- four days stale. The run must still fail, but only after copying
    everything it could.
    """
    (data_dir / "price_daily.parquet").write_text("daily bars\n", encoding="utf-8")
    dest = tmp_path / "backup"
    real_copy = backup_data.shutil.copy2

    def refuse_the_database(src: Path, dst: Path) -> str:
        if Path(src).name == "official_daily.sqlite":
            raise PermissionError(33, "The process cannot access the file because another process has locked it")
        return str(real_copy(src, dst))

    monkeypatch.setattr(backup_data.shutil, "copy2", refuse_the_database)

    with pytest.raises(BackupError, match="could not be mirrored"):
        mirror_data_cache(data_dir, dest)

    assert not (dest / "data" / "official_daily.sqlite").exists()
    assert (dest / "data" / "price_daily.parquet").is_file()
    assert (dest / "data" / "raw" / "staging.json").is_file()


def test_verify_does_not_report_a_skipped_file_as_missing(data_dir: Path, tmp_path: Path) -> None:
    """A file the caller already knows it could not read is reported once, by the caller."""
    dest = tmp_path / "backup"
    mirror_data_cache(data_dir, dest)
    (dest / "data" / "official_daily.sqlite").unlink()

    assert verify_mirror(data_dir, dest / "data") != []
    assert verify_mirror(data_dir, dest / "data", skip=[Path("official_daily.sqlite")]) == []


@pytest.mark.parametrize("vanishes_on_listing", [1, 2], ids=["before-copy", "before-verify"])
def test_a_file_that_vanishes_after_listing_does_not_fail_the_backup(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, vanishes_on_listing: int
) -> None:
    """The backfill deletes its lock file on exit, which can race the mirror.

    On 2026-09-18 the daily backup failed with FileNotFoundError on
    .official_backfill.lock: listed, mirrored, then gone by verification.
    """
    lock = data_dir / ".official_backfill.lock"
    lock.write_text("pid", encoding="utf-8")
    original = backup_data._relative_files
    calls = {"n": 0}

    def listing_then_delete(root: Path):
        result = original(root)
        calls["n"] += 1
        if calls["n"] == vanishes_on_listing:
            lock.unlink()
        return result

    monkeypatch.setattr(backup_data, "_relative_files", listing_then_delete)

    mirror_data_cache(data_dir, tmp_path / "backup")

    assert (tmp_path / "backup" / "data" / "2330_day.csv").is_file()


def test_a_file_that_vanishes_while_being_listed_is_skipped(data_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """The window inside _relative_files, after is_file() and before the link and resolve checks."""
    lock = data_dir / ".official_backfill.lock"
    lock.write_text("pid", encoding="utf-8")
    real_check = backup_data._is_link_or_reparse

    def vanish_first(path: Path) -> bool:
        if path == lock and lock.exists():
            lock.unlink()
        return real_check(path)

    monkeypatch.setattr(backup_data, "_is_link_or_reparse", vanish_first)

    with caplog.at_level("INFO", logger="backup_data"):
        relatives, refused = backup_data._relative_files(data_dir)

    assert ".official_backfill.lock disappeared while it was listed" in caplog.text
    assert Path(".official_backfill.lock") not in relatives
    assert refused == []
    assert Path("2330_day.csv") in relatives


def test_verify_skips_an_unmirrored_file_that_vanished(data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient file listed but never mirrored, then gone, is not a missing mirror file."""
    dest = tmp_path / "backup"
    mirror_data_cache(data_dir, dest)
    ghost = data_dir / ".official_backfill.lock"
    ghost.write_text("pid", encoding="utf-8")
    original = backup_data._relative_files

    def list_then_delete(root: Path):
        result = original(root)
        ghost.unlink()
        return result

    monkeypatch.setattr(backup_data, "_relative_files", list_then_delete)

    assert verify_mirror(data_dir, dest / "data") == []


def test_importing_the_module_does_not_configure_logging(tmp_path: Path) -> None:
    """setup_logging attaches a file handler to the root logger.

    Doing that at import time made merely importing this module redirect every
    log record in the process into logs/backup_data_<date>.log -- which is how
    the real log directory ended up collecting pytest output, tracebacks from
    tests that raise on purpose included.
    """
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(REPO_ROOT), str(REPO_ROOT / "scripts")]))
    subprocess.run([sys.executable, "-c", "import backup_data"], cwd=tmp_path, env=env, check=True)

    assert not (tmp_path / "logs").exists()


def _configure_external_data_dir(monkeypatch: pytest.MonkeyPatch, source: Path) -> None:
    monkeypatch.setattr(backup_data.config, "DATA_DIR", source.resolve())
    monkeypatch.setattr(backup_data.config, "DATA_DIR_CONFIGURED", True)


def test_mirror_accepts_a_sibling_of_a_configured_external_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An external root's parent is not a checkout; it can be a whole volume."""
    source = tmp_path / "volume" / "stock-data"
    source.mkdir(parents=True)
    (source / "2330_day.csv").write_text("day bars\n", encoding="utf-8")
    _configure_external_data_dir(monkeypatch, source)

    copied, verified = mirror_data_cache(source, tmp_path / "volume" / "backup")

    assert (copied, verified) == (1, 1)


def test_mirror_refuses_a_destination_inside_a_configured_external_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "stock-data"
    source.mkdir()
    (source / "2330_day.csv").write_text("day bars\n", encoding="utf-8")
    _configure_external_data_dir(monkeypatch, source)

    with pytest.raises(BackupError, match="is not a backup"):
        mirror_data_cache(source, source / "backup")
    assert not (source / "backup").exists()


def test_mirror_refuses_a_destination_inside_the_checkout_for_an_external_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "stock-data"
    source.mkdir()
    (source / "2330_day.csv").write_text("day bars\n", encoding="utf-8")
    _configure_external_data_dir(monkeypatch, source)
    checkout = tmp_path / "checkout"
    monkeypatch.setattr(backup_data.config, "REPO_ROOT", checkout)

    with pytest.raises(BackupError, match="is not a backup"):
        mirror_data_cache(source, checkout / "backup")
