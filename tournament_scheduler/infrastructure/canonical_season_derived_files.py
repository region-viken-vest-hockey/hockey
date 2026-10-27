"""Derived season artifacts that a canonical commit must not delete (#853).

The canonical write replaces the whole season directory. A plain commit does
not regenerate derived projections such as ``change-log.md``, so they are
carried into the staged tree to survive the swap. A carried projection whose
recorded canonical revision no longer matches is marked stale in place, so it
can never be read as describing the current state. Canonical layout files are
never listed here: migrations rely on them being replaced.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Mapping

from tournament_scheduler.season.state import canonical_state_revision

DERIVED_SEASON_FILES = ("change-log.md",)

_REVISION_LINE = re.compile(r"^Canonical state revision: `([^`]*)`", re.MULTILINE)
_STALE_BANNER = "> Stale projection: "


def carry_forward_derived_files(
    season_directory: Path,
    staging: Path,
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
) -> list[Path]:
    """Copy derived season artifacts into the staged tree, marking stale ones.

    The commit installs ``schedule`` and ``decisions``. A projection generated
    for a different canonical revision gets a stale banner, which names both
    revisions and how to regenerate it.
    """

    revision = canonical_state_revision(schedule, decisions)
    carried: list[Path] = []
    for name in DERIVED_SEASON_FILES:
        source = season_directory / name
        if not source.is_file():
            continue
        target = staging / name
        text = source.read_text(encoding="utf-8")
        recorded = _recorded_revision(text)
        # Only a projection whose known revision matches the installed one is
        # current; unknown provenance is treated as stale.
        if revision and recorded == revision:
            shutil.copyfile(source, target)
        else:
            target.write_text(_mark_stale(text, recorded or "unknown", revision or "unknown"), encoding="utf-8")
        carried.append(target)
    return carried


def _recorded_revision(text: str) -> str | None:
    match = _REVISION_LINE.search(text)
    return match.group(1) if match else None


def _mark_stale(text: str, recorded: str, current: str) -> str:
    """Return ``text`` with exactly one stale banner for ``current``."""

    body = "".join(line for line in text.splitlines(keepends=True) if not line.startswith(_STALE_BANNER))
    banner = (
        f"{_STALE_BANNER}generated for canonical revision `{recorded}`; the season is now at "
        f"`{current}`. Regenerate with `rvv-miniputt season changes --write` before relying on it.\n\n"
    )
    title_end = body.find("\n") + 1
    if title_end <= 0:
        return banner + body
    return body[:title_end] + "\n" + banner + body[title_end:].lstrip("\n")
