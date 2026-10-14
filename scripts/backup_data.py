"""
Backup Data Module

Mirrors the data cache to a location outside this repository, and optionally
builds quarterly archives of it for offsite storage.

The mirror is ADDITIVE: it copies new and changed files and never deletes
from the destination. That is deliberate, not an oversight. A conventional
mirror replicates a source-side deletion into the backup, which turns one
accident into two lost copies -- and this cache is expensive to reacquire,
because the price data behind it comes from a quota-limited API. Refusing to
delete is what makes this a backup rather than a second way to lose the data.
The cost is that files removed upstream (delisted symbols) linger in the
backup, which is the right trade: stale files cost disk, missing files cost
days.

Nothing in this module deletes anything, on either side. Retention, if any,
is a deliberate manual act wherever the archives are stored.

Every copy is followed by a verification pass checking that each source file
exists in the destination with a matching byte count, because a backup that
has not been checked is only an assumption. The one thing it does not treat as
a failure is a source that has moved on since it was copied -- some of this
cache is written continuously -- see verify_mirror.
"""

import argparse
import datetime
import logging
import os
import shutil
import stat
import tarfile
from collections.abc import Iterable
from pathlib import Path

from dotenv import load_dotenv

from config import config
from shp_utils.logging_utils import setup_logging

# Configured by main(), not at import: setup_logging attaches a file handler to
# the root logger, so doing it here made merely importing this module redirect
# every log record in the process into logs/backup_data_<date>.log. Under
# pytest that meant the real log directory collected test output, including
# tracebacks from tests that raise on purpose.
logger = logging.getLogger(__name__)

BACKUP_DIR_ENV = "SHIOAJI_BACKUP_DIR"

# The mirror copies everything -- the bulk is the most expensive part to
# reacquire. The archives exist to be uploaded somewhere with a retention
# window, so they are split: daily bars are a few tens of MB and are what
# downstream consumers read, while minute bars dominate the volume. Splitting
# lets a retention policy keep many quarters of the former and few of the
# latter.
#
# data_meta holds the small artifacts that describe the bars rather than
# containing them (corporate actions, symbol metadata, and append-only
# value-chain classification observations). They are small enough to ride along
# at every retention tier.
#
# The point-in-time side of the cache is not archived at all: not
# official_daily.sqlite, monthly_revenue.sqlite or institutional.sqlite, and not
# the raw responses stored beside the latter two. The databases are excluded
# because they are written in WAL mode, so archiving a main database file alone
# would silently drop whatever is still in its -wal sidecar; doing it safely
# needs a SQLite checkpoint step first, which is a separate change. Their raw
# evidence is excluded so that one rule covers the whole of it rather than one
# rule per pipeline -- revenue and institutional evidence were otherwise going
# to differ only by which one happened to land first. All of it stays covered by
# the file-for-file mirror, which is additive and verified every run, and by
# nothing else. Note that these raw snapshots are not re-fetchable: they record
# when an observation first reached this machine. Wanting them offsite is a
# reasonable future decision, and it should be made for all of them at once.
ARCHIVE_GROUPS: dict[str, tuple[str, ...]] = {
    "data_day": ("*_day.csv", "*.json5"),
    "data_min": ("*_min.csv",),
    "data_meta": (
        "corporate_actions.sqlite",
        "symbol_meta.sqlite",
        "value_chain_classification.json",
        "value_chain_classification_history/*.json",
    ),
}


class BackupError(RuntimeError):
    """Raised when a backup cannot be performed, or fails verification."""


def resolve_backup_dir(explicit: str | Path | None = None) -> Path | None:
    """Resolve the backup root from an explicit value or the environment.

    Returns None when neither is set; callers treat that as "no backup
    configured on this machine" rather than an error. The destination is
    necessarily an absolute path outside this repository, so there is no
    sensible default, and no repo-relative one would do: a backup stored
    inside the tree it backs up shares that tree's fate.
    """
    value = explicit if explicit is not None else os.environ.get(BACKUP_DIR_ENV)
    if not value:
        return None
    return Path(value).expanduser().resolve()


def _assert_destination_outside(source: Path, dest_root: Path) -> None:
    # The in-repository default cache shares its fate with the checkout around
    # it, so that whole tree is excluded. A configured external data directory
    # has no such enclosing tree; its parent may be a whole volume, which would
    # refuse every destination on it.
    guarded = [source, config.REPO_ROOT.resolve()]
    if not (config.DATA_DIR_CONFIGURED and source == config.DATA_DIR):
        guarded.append(source.parent)
    for root in guarded:
        if dest_root == root or root in dest_root.parents:
            raise BackupError(f"Backup destination {dest_root} is inside {root}. A backup stored in the tree it backs up is not a backup.")
    # The mirror itself lands in <dest_root>/data. If that is the source, or
    # either contains the other, every file "already exists" and verification
    # compares the cache with itself -- a reported success with no backup.
    dest_data = dest_root / "data"
    if dest_data == source or source in dest_data.parents or dest_data in source.parents:
        raise BackupError(f"Backup mirror {dest_data} overlaps the data directory {source}. A backup stored in the tree it backs up is not a backup.")


# SQLite's -shm sidecar is shared memory, not data: it is rebuilt from the -wal
# file every time the database is opened, and it is held under a byte-range lock
# for as long as any writer is attached. Mirroring it buys nothing and costs
# everything -- on 2026-08-24 and 2026-08-25 CopyFile2 hit that lock on
# official_daily.sqlite-shm while the historical backfill was running, and
# because the copy loop stopped at the first failure, every file sorting after
# it went unmirrored. price_daily.parquet was one of them, and sat four days
# stale in the backup as a result.
#
# The -wal sidecar is a different thing entirely -- committed data not yet folded
# into the main database -- so it stays in the mirror.
_UNMIRRORED_SUFFIXES = ("-shm",)


def _relative_files(root: Path) -> tuple[list[Path], list[Path]]:
    """Return (files to mirror, links refused).

    A link inside the cache is refused rather than followed. A symlink or an NTFS
    junction makes one directory an alias for another, and copying through one
    would file the target's bytes under the link's path -- a quiet lie about what
    the cache held. This repository has already lost about 4000 rate-limited CSVs
    to a recursive delete that traversed such a link, so noticing one matters.

    Refusing is not aborting. An earlier version raised from here, which put every
    file in the cache behind the first odd one -- the same all-or-nothing shape
    that mirror_data_cache stopped having, one commit earlier, for exactly the
    same reason. The caller mirrors everything else and reports these at the end,
    so the run still fails and the data is still protected.

    A link at `root` itself does raise, and is the case that actually happened
    here: rglob follows it transparently, so every path beneath it looks ordinary
    while the mirror quietly describes some other directory. There is nothing
    partial to salvage from that -- the source is not what the caller thinks it
    is -- so it is the one link worth stopping for.
    """
    if _is_link_or_reparse(root):
        raise BackupError(f"Backup source {root} is itself a link or reparse point; mirroring through it would describe a different directory.")

    resolved_root = root.resolve(strict=True)
    relatives: list[Path] = []
    refused: set[Path] = set()
    for path in root.rglob("*"):
        if not path.is_file() or path.name.endswith(_UNMIRRORED_SUFFIXES):
            continue
        relative = path.relative_to(root)
        try:
            current = root
            for part in relative.parts:
                current /= part
                if _is_link_or_reparse(current):
                    # Reported by the caller, not here: verify_mirror walks this same
                    # list a second time, so logging at this level would say
                    # everything twice.
                    refused.add(current.relative_to(root))
                    break
            else:
                if resolved_root in path.resolve(strict=True).parents:
                    relatives.append(relative)
                else:
                    # Belt and braces: with links refused above, nothing should still
                    # resolve outside the root. If something does, it is not understood
                    # well enough to copy, so it joins them.
                    refused.add(relative)
        except FileNotFoundError:
            # A transient file (the backfill's lock) removed while it was being classified.
            logger.info("backup source %s disappeared while it was listed; skipping", relative)
            continue
    return sorted(relatives), sorted(refused)


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


# shutil.copy2 preserves mtime, so a good copy and its source carry the same
# timestamp. Filesystems with coarse timestamp granularity can still round them
# apart by a second, which is not evidence of anything.
_MTIME_TOLERANCE_SECONDS = 1.0


def verify_mirror(source: Path, dest_data: Path, *, skip: Iterable[Path] = ()) -> list[str]:
    """Return problems found: source files missing from, or sized differently in, the mirror.

    A size difference is only evidence of a bad copy when the source has not
    moved since that copy was taken. Some of what lives under `data/` is written
    continuously -- `official_daily.sqlite` and its `-wal` sidecar grow all
    evening while the historical backfill runs -- so comparing a copy against a
    source that has since advanced reports a mismatch for a file that copied
    perfectly well. On 2026-08-20 that failed the nightly backup over
    `official_daily.sqlite-wal`, hours after the price data it exists to protect
    had already been mirrored and verified.

    So a file whose source is newer than the destination copy is reported as
    stale, not as a failure: the copy is intact and simply one snapshot behind.
    A short copy with a matching timestamp is still a failure, and a missing
    file always is.

    `skip` names files the caller already knows it could not read this run; they
    are reported by the caller as unreadable rather than a second time here as
    missing or short.
    """
    skipped = set(skip)
    problems: list[str] = []
    for relative in _relative_files(source)[0]:
        if relative in skipped:
            continue
        counterpart = dest_data / relative
        if not counterpart.is_file():
            if not (source / relative).exists():
                logger.info("backup source %s disappeared after it was listed; nothing to verify", relative)
                continue
            problems.append(f"missing: {relative}")
            continue
        try:
            source_stat = (source / relative).stat()
        except FileNotFoundError:
            # A transient file (a lock) listed earlier and removed since. Nothing to protect.
            logger.info("backup source %s disappeared after it was listed; nothing to verify", relative)
            continue
        dest_stat = counterpart.stat()
        if dest_stat.st_size == source_stat.st_size:
            continue
        if source_stat.st_mtime > dest_stat.st_mtime + _MTIME_TOLERANCE_SECONDS:
            logger.info("backup is one snapshot behind on %s: the source changed after it was copied", relative)
            continue
        problems.append(f"size mismatch: {relative}")
    return problems


def _raise_for_problems(problems: list[str], where: Path) -> None:
    if not problems:
        return
    for problem in problems[:10]:
        logger.error("backup verification: %s", problem)
    raise BackupError(f"Backup verification failed: {len(problems)} problem(s) in {where}")


def mirror_data_cache(source: Path, dest_root: Path) -> tuple[int, int]:
    """Copy new and changed files from source into dest_root/data, then verify.

    Returns (files_copied, files_verified).
    """
    source = source.resolve()
    if not source.is_dir():
        raise BackupError(f"Data directory not found: {source}")
    _assert_destination_outside(source, dest_root)

    dest_data = dest_root / "data"
    dest_data.mkdir(parents=True, exist_ok=True)

    copied = 0
    unreadable: list[Path] = []
    relatives, refused = _relative_files(source)
    for relative in relatives:
        src_file = source / relative
        dst_file = dest_data / relative
        try:
            src_stat = src_file.stat()
        except FileNotFoundError:
            # Removed between listing and copying, as the backfill's lock file is on exit.
            logger.info("backup source %s disappeared after it was listed; skipping", relative)
            continue
        except OSError as error:
            unreadable.append(relative)
            logger.warning("backup could not copy %s: %s", relative, error)
            continue
        try:
            if dst_file.is_file():
                dst_stat = dst_file.stat()
                # Same size and not older than the source means already mirrored.
                if dst_stat.st_size == src_stat.st_size and dst_stat.st_mtime >= src_stat.st_mtime:
                    continue
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)
        except OSError as error:
            if isinstance(error, FileNotFoundError) and not src_file.exists():
                logger.info("backup source %s disappeared while it was copied; skipping", relative)
                continue
            # One file another process holds open must not cost the mirror every
            # file that sorts after it. Record it, copy the rest, and fail at the
            # end -- so the failure stays visible, but the data that could be
            # protected has been protected by the time anyone reads about it.
            unreadable.append(relative)
            logger.warning("backup could not copy %s: %s", relative, error)
            continue
        copied += 1

    _raise_for_problems(verify_mirror(source, dest_data, skip=unreadable), dest_data)
    incomplete = [f"unreadable: {relative}" for relative in unreadable] + [f"link or reparse point: {relative}" for relative in refused]
    if incomplete:
        for problem in incomplete[:10]:
            logger.error("backup incomplete: %s", problem)
        raise BackupError(f"Backup incomplete: {len(incomplete)} path(s) could not be mirrored from {source}")
    logger.info("backup mirror OK: %d file(s) copied, %d verified in %s", copied, len(relatives), dest_data)
    return copied, len(relatives)


def quarter_label(today: datetime.date | None = None) -> str:
    day = today or datetime.date.today()
    return f"{day.year}Q{(day.month - 1) // 3 + 1}"


def build_quarterly_archives(dest_root: Path, today: datetime.date | None = None) -> list[Path]:
    """Build compressed archives of the mirror under dest_root/archive, split by data type.

    Built from the mirror rather than the live cache so a concurrent refresh
    cannot leave a half-written file inside an archive.
    """
    dest_data = dest_root / "data"
    if not dest_data.is_dir():
        raise BackupError(f"No mirror to archive at {dest_data}; run the mirror first.")

    archive_dir = dest_root / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    label = quarter_label(today)

    written: list[Path] = []
    for name, patterns in ARCHIVE_GROUPS.items():
        members = sorted({match for pattern in patterns for match in dest_data.glob(pattern)})
        if not members:
            logger.info("archive %s skipped: no files matched", name)
            continue

        out_path = archive_dir / f"{name}_{label}.tar.gz"
        logger.info("archiving %d file(s) -> %s", len(members), out_path)
        with tarfile.open(out_path, "w:gz") as tar:
            for member in members:
                tar.add(member, arcname=member.relative_to(dest_data).as_posix())
        logger.info("archive written: %s (%.1f MB)", out_path, out_path.stat().st_size / (1024 * 1024))
        written.append(out_path)

    return written


def run_backup(
    backup_dir: str | Path | None = None,
    *,
    data_dir: str | Path | None = None,
    archive: bool = False,
    verify_only: bool = False,
) -> bool:
    """Mirror (and optionally archive) the cache. Returns False when no destination is configured."""
    dest_root = resolve_backup_dir(backup_dir)
    if dest_root is None:
        logger.warning("backup skipped: no destination configured (pass --backup-dir or set %s)", BACKUP_DIR_ENV)
        return False

    source = Path(data_dir or config.DATA_DIR).resolve()

    if verify_only:
        _assert_destination_outside(source, dest_root)
        dest_data = dest_root / "data"
        _raise_for_problems(verify_mirror(source, dest_data), dest_data)
        logger.info("backup verification OK against %s", dest_data)
    else:
        mirror_data_cache(source, dest_root)

    if archive:
        build_quarterly_archives(dest_root)

    return True


def main() -> int:
    setup_logging("backup_data.log")
    # Load .env here too, not only in run_daily: the documented standalone
    # usage (`uv run backup_data.py --archive`) has to see SHIOAJI_BACKUP_DIR
    # the same way the scheduled run does, or it silently skips. This
    # checkout's own file only; see config.ENV_FILE.
    load_dotenv(config.ENV_FILE)

    parser = argparse.ArgumentParser(description="Mirror the data cache to a backup location outside this repository.")
    parser.add_argument("--backup-dir", default=None, help=f"Absolute path to the backup root. Defaults to ${BACKUP_DIR_ENV}.")
    parser.add_argument("--data-dir", default=None, help="Data directory to back up (defaults to config.DATA_DIR).")
    parser.add_argument("--archive", action="store_true", help="Also build quarterly split archives under <backup-dir>/archive.")
    parser.add_argument("--verify-only", action="store_true", help="Check the existing backup without copying.")
    args = parser.parse_args()

    try:
        configured = run_backup(args.backup_dir, data_dir=args.data_dir, archive=args.archive, verify_only=args.verify_only)
    except BackupError:
        logger.exception("backup failed")
        return 1
    return 0 if configured else 1


if __name__ == "__main__":
    raise SystemExit(main())
