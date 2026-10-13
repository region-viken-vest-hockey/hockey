#!/usr/bin/env python3
"""Reject tracked paths that collide on common case-insensitive filesystems."""

from __future__ import annotations

import subprocess
import sys
import unicodedata
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def collision_key(path: str) -> str:
    """Return the portable identity used by case-insensitive normalized filesystems."""
    return unicodedata.normalize("NFC", path).casefold()


def find_collisions(paths: Iterable[str]) -> list[tuple[str, ...]]:
    """Group distinct tracked paths with the same portable identity."""
    by_key: dict[str, list[str]] = {}
    for path in paths:
        by_key.setdefault(collision_key(path), []).append(path)
    return [tuple(sorted(group)) for _, group in sorted(by_key.items()) if len(group) > 1]


def tracked_paths() -> list[str]:
    """Read the complete tracked path set from Git, independent of the worktree FS."""
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "--cached"],
        check=True,
        stdout=subprocess.PIPE,
    )
    try:
        return [raw.decode("utf-8") for raw in result.stdout.split(b"\0") if raw]
    except UnicodeDecodeError as exc:
        raise ValueError("tracked paths must be valid UTF-8") from exc


def main(paths: Iterable[str] | None = None) -> int:
    try:
        collisions = find_collisions(tracked_paths() if paths is None else paths)
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f"Tracked-path collision check could not inspect Git paths: {exc}", file=sys.stderr)
        return 2

    if collisions:
        print(
            "Tracked-path collision check failed: paths must be unique after "
            "NFC normalization and case folding.",
            file=sys.stderr,
        )
        for group in collisions:
            print("  colliding group:", file=sys.stderr)
            for path in group:
                print(f"    {ascii(path)}", file=sys.stderr)
        return 1

    print("Tracked-path collision check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
