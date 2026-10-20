"""Import a published corporate-action supplement batch into corporate_actions.sqlite.

Adds rows to four additive tables (corporate_action_supplement_batches,
corporate_action_source_documents, corporate_action_terms, trading_interruptions).
Existing corporate_actions rows are never read or changed. A batch is imported
once; re-running with identical content is a no-op and changed content is refused.

    uv run python scripts/import_corporate_action_supplement.py --batch annual-winner-20261013-v1 --dry-run
    uv run python scripts/import_corporate_action_supplement.py --batch annual-winner-20261013-v1
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from config import config
from shp_utils.corporate_action_supplement import SupplementError, extract, in_producer_write_window, load_batch, summarize, write

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch", required=True, help="Directory name under <DATA_DIR>/corporate_action_supplements/")
    parser.add_argument("--data-dir", type=Path, default=config.DATA_DIR, help="Defaults to config.DATA_DIR")
    parser.add_argument("--db", type=Path, help="Defaults to <data-dir>/corporate_actions.sqlite")
    parser.add_argument("--dry-run", action="store_true", help="Verify and extract only; print the row summary")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        batch = load_batch(args.data_dir, args.batch)
        rows = extract(batch)
    except (SupplementError, OSError, KeyError) as exc:
        logger.error("Batch %s cannot be imported: %s", args.batch, exc)
        return 2
    summary = summarize(rows)
    sys.stdout.write(json.dumps({"batch_id": batch.batch_id, **summary}, ensure_ascii=False, indent=2) + "\n")
    if args.dry_run:
        return 0

    now = datetime.now(timezone.utc)
    if in_producer_write_window(now):
        logger.error("Refusing to write during the weekday 18:00-18:45 Asia/Taipei producer window")
        return 3
    db_path = args.db or Path(args.data_dir) / "corporate_actions.sqlite"
    if not db_path.is_file():
        logger.error("Database not found: %s (the importer never creates it)", db_path)
        return 2
    try:
        with closing(sqlite3.connect(db_path, timeout=30, isolation_level=None)) as conn:
            outcome = write(conn, batch, rows, imported_at=now.isoformat(timespec="seconds"))
    except (SupplementError, sqlite3.Error) as exc:
        logger.error("Import of %s failed and was rolled back: %s", batch.batch_id, exc)
        return 1
    logger.info("Batch %s %s in %s", batch.batch_id, outcome, db_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
