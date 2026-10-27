"""Derived season artifacts that a canonical commit must not delete (#853).

The canonical write replaces the whole season directory. A plain commit does
not regenerate derived projections such as ``change-log.md``, so they are copied
into the staged tree to survive the swap. Canonical layout files are never
listed here: migrations rely on them being replaced.
"""

from __future__ import annotations

import shutil
from pathlib import Path

DERIVED_SEASON_FILES = ("change-log.md",)


def carry_forward_derived_files(season_directory: Path, staging: Path) -> list[Path]:
    """Copy derived season artifacts into the staged tree so the swap keeps them."""

    carried: list[Path] = []
    for name in DERIVED_SEASON_FILES:
        source = season_directory / name
        if not source.is_file():
            continue
        target = staging / name
        shutil.copyfile(source, target)
        carried.append(target)
    return carried
