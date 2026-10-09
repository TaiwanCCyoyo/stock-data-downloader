"""Capture only annual-row identities explicitly listed in a detail plan."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from shp_utils.corporate_detail_capture import CorporateDetailCaptureError, capture_corporate_details


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-requests", required=True, type=int)
    parser.add_argument("--max-seconds", required=True, type=float)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    try:
        manifest = capture_corporate_details(
            plan_path=args.plan,
            output_dir=args.output,
            max_requests=args.max_requests,
            max_seconds=args.max_seconds,
            timeout=args.timeout,
            interval=args.interval,
        )
    except (CorporateDetailCaptureError, FileExistsError, OSError) as error:
        raise SystemExit(str(error)) from error
    sys.stdout.write(f"{args.output}\t{manifest['status']}\n")
    if manifest["status"] != "complete":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
