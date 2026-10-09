"""Opt-in bounded MOPS material-announcement capture; no scheduled integration."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from shp_utils.mops_material_evidence import capture

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", required=True)
    parser.add_argument("--roc-year", type=int, required=True)
    parser.add_argument("--roc-month", type=int, required=True)
    parser.add_argument("--max-details", type=int, default=10)
    parser.add_argument("--output-dir", type=Path, help="Fresh directory; existing paths are refused")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        manifest = capture(
            args.company_id,
            args.roc_year,
            args.roc_month,
            output_dir=args.output_dir,
            max_details=args.max_details,
        )
    except (ValueError, OSError) as exc:
        logger.error("Capture did not complete: %s", type(exc).__name__)
        return 2
    logger.info("Capture status=%s details=%s output=%s", manifest["status"], manifest["captured_details"], manifest["output_dir"])
    return 0 if manifest["status"] in {"complete", "source_empty"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
