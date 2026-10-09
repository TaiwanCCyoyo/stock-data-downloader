"""Check UTF-8 and decode corruption without rewriting or displaying file contents."""

import argparse
import logging
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def check_file_hygiene(filepath: str) -> bool:
    """Accept multilingual UTF-8; missing/deleted files have nothing to check."""
    try:
        content = Path(filepath).read_text(encoding="utf-8")
    except FileNotFoundError:
        return True
    except UnicodeDecodeError as exc:
        LOGGER.error("%s: invalid UTF-8 at byte %s", filepath, exc.start)
        return False
    except OSError as exc:
        LOGGER.error("%s: cannot read file (errno %s)", filepath, exc.errno)
        return False

    valid = True
    if content.startswith("\ufeff"):
        LOGGER.error("%s: UTF-8 BOM is not supported; use UTF-8 without BOM", filepath)
        valid = False
    for number, line in enumerate(content.splitlines(), start=1):
        if "\ufffd" in line:
            LOGGER.error("%s:%s: Unicode replacement character; recover the original text", filepath, number)
            valid = False
    return valid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", nargs="+", required=True, dest="files")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR, format="%(message)s")
    results = [check_file_hygiene(filepath) for filepath in args.files]
    return 0 if all(results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
