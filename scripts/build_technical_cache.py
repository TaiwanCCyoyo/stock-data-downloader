"""Build or verify a pinned, local-only technical-components cache generation."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from config import config
from shp_utils.technical_cache import build_cache, verify_manifest

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Local daily-price Parquet; custom inputs require --price-basis")
    parser.add_argument("--price-basis", help="Explicit caller declaration, not a certification of adjustment or PIT correctness")
    parser.add_argument("--output-dir", type=Path, default=Path(config.DATA_DIR) / "technical_features")
    parser.add_argument("--verify", type=Path, metavar="MANIFEST", help="Verify an existing generation without generating or downloading data")
    args = parser.parse_args(argv)
    if args.verify:
        if args.input is not None or args.price_basis is not None:
            parser.error("--verify cannot be combined with input or basis arguments")
        manifest = verify_manifest(args.verify)
        logger.info("Verified technical generation %s (%s)", args.verify, manifest.get("schema_version"))
        return 0
    if args.input is None and args.price_basis is not None:
        parser.error("--price-basis requires an explicit --input")
    if args.input is not None and (not args.price_basis or not args.price_basis.strip()):
        parser.error("a custom --input requires an explicit --price-basis")
    source = args.input if args.input is not None else Path(config.DATA_DIR) / "price_daily.parquet"
    basis = args.price_basis or "raw-unadjusted"
    result = build_cache(pd.read_parquet(source), args.output_dir, price_basis=basis)
    logger.info(
        "Technical manifest %s; computed=%s reused=%s bytes_written=%s",
        result.manifest_path,
        result.computed_symbols,
        result.reused_symbols,
        result.bytes_written,
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
