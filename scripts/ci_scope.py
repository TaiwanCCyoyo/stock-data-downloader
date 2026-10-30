"""Choose full offline validation unless every changed path is reviewed prose."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Contracts and unknown documents deliberately remain full-suite inputs.
PROSE_PATHS = {"README.md", "AGENTS.md", "docs/README.md", "docs/zh-TW/README.md", "docs/en/git-workflow.md"}


def changed_paths(data: bytes) -> list[str]:
    """Keep both sides of renames/copies; reject incomplete Git output."""
    fields = data.split(b"\0")
    if fields.pop() != b"":
        raise ValueError("Truncated NUL-delimited diff")
    paths: list[str] = []
    index = 0
    while index < len(fields):
        status = fields[index].decode("ascii")
        index += 1
        if not status or status[0] not in "ACDMRTUXB":
            raise ValueError(f"Unsupported change status: {status}")
        count = 2 if status[0] in "RC" else 1
        if index + count > len(fields):
            raise ValueError("Missing changed path")
        for field in fields[index : index + count]:
            if not field:
                raise ValueError("Empty changed path")
            paths.append(os.fsdecode(field))
        index += count
    return paths


def needs_full_tests(paths: list[str]) -> bool:
    return not paths or any(path not in PROSE_PATHS for path in paths)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, timeout=60).stdout.decode("ascii").strip()


def select(base: str, head: str) -> dict[str, object]:
    head_sha = git("rev-parse", "--verify", "--end-of-options", f"{head}^{{commit}}")
    base_sha = git("rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}")
    merge_base = git("merge-base", base_sha, head_sha)
    diff = subprocess.run(
        ["git", "diff", "--name-status", "--find-renames", "-z", merge_base, head_sha, "--"],
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout
    paths = changed_paths(diff)
    return {"full": needs_full_tests(paths), "merge_base": merge_base, "head": head_sha, "paths": paths}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=Path(".tmp/ci-plan.json"))
    args = parser.parse_args()
    if not args.full and not args.base:
        parser.error("--base or --full is required")
    plan = {"full": True, "paths": []} if args.full else select(args.base, args.head)
    args.plan.parent.mkdir(parents=True, exist_ok=True)
    args.plan.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    with args.output.open("a", encoding="utf-8") as output:
        output.write(f"full={str(plan['full']).lower()}\n")
        if "merge_base" in plan:
            output.write(f"merge_base={plan['merge_base']}\nhead={plan['head']}\n")
    sys.stdout.write(json.dumps(plan, indent=2) + "\n")


if __name__ == "__main__":
    main()
