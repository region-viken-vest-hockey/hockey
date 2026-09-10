#!/usr/bin/env python3
"""Enforce a 300-line-per-file guideline for tournament_scheduler/, ratcheted.

A file over 300 lines usually means it is doing too much and should be split
along SOLID lines. This check does not require the whole codebase to comply
today -- files already over the limit when this check was introduced are
grandfathered in scripts/file-length-baseline.txt at their *current* line
count, and may never grow past that recorded count. Any file not in the
baseline (new or currently-compliant) must stay at or under 300 lines.

Usage: scripts/check_file_length.py
Exit status is non-zero if any file violates its allowed limit.
"""

from __future__ import annotations

import sys
from pathlib import Path

LIMIT = 300
ROOT = Path(__file__).resolve().parent.parent
TARGET_DIR = ROOT / "tournament_scheduler"
BASELINE_PATH = ROOT / "scripts" / "file-length-baseline.txt"


def load_baseline() -> dict[str, int]:
    baseline: dict[str, int] = {}
    for line in BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        path, _, count = line.partition("\t")
        baseline[path] = int(count)
    return baseline


def main() -> int:
    baseline = load_baseline()
    violations: list[str] = []
    stale_baseline_entries: list[str] = []

    for py_file in sorted(TARGET_DIR.rglob("*.py")):
        rel = py_file.relative_to(ROOT).as_posix()
        line_count = sum(1 for _ in py_file.open("r", encoding="utf-8", errors="replace"))
        allowed = baseline.get(rel, LIMIT)

        if line_count > allowed:
            if rel in baseline:
                violations.append(
                    f"{rel}: {line_count} lines, grew past its baseline of {allowed} "
                    "(shrink it back down, or if the growth is unavoidable, split the "
                    "file instead of raising the baseline)"
                )
            else:
                violations.append(
                    f"{rel}: {line_count} lines, exceeds the {LIMIT}-line guideline "
                    "(split into smaller, single-responsibility modules)"
                )
        elif rel in baseline and line_count <= LIMIT:
            stale_baseline_entries.append(rel)

    if stale_baseline_entries:
        print("Notice: these files are back at or under the limit -- remove them from")
        print(f"{BASELINE_PATH.relative_to(ROOT)}:")
        for rel in stale_baseline_entries:
            print(f"  {rel}")
        print()

    if violations:
        print(f"File-length check failed ({len(violations)} violation(s)):")
        for message in violations:
            print(f"  {message}")
        return 1

    print(f"File-length check passed ({len(baseline)} pre-existing file(s) grandfathered).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
