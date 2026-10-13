"""Retained-evidence carry-forward for the canonical season directory swap.

Immutable, content-addressed evidence is carried into each staged season tree by
hardlink (with a copy fallback) so a mutation never drops evidence referenced by
decision history. New evidence is written into the same staged tree before the
swap. The atomic swap itself stays in :mod:`canonical_season_store`.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Mapping

from tournament_scheduler.infrastructure.canonical_season_layout import SeasonStateError


def _carry_forward_evidence(existing_evidence: Path, staging_evidence: Path) -> list[Path]:
    """Carry retained evidence forward into the staged tree without bulk copying.

    Retained evidence is content-addressed and strictly create-once (never
    modified in place), so its files are immutable and safe to hardlink.
    Hardlinks avoid re-reading and re-copying the retained bytes on every
    canonical mutation. When hardlinks are unavailable (for example a
    cross-device staging directory or a filesystem without link support) each
    file is copied instead, so evidence is never dropped. Returns the files that
    were actually copied and therefore still need their contents fsynced
    (hardlinked files are already durable on disk).
    """

    copied: list[Path] = []
    for src in sorted(existing_evidence.rglob("*")):
        if src.is_symlink() or not src.is_file():
            continue
        relative = src.relative_to(existing_evidence)
        dst = staging_evidence / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
            copied.append(dst)
    return copied


def _write_extra_evidence(
    staging: Path, extra_evidence: Mapping[str, bytes]
) -> list[Path]:
    """Write caller-supplied evidence files into the staged season tree.

    Keys are paths relative to the season directory. Files are written with the
    same stage+fsync+replace discipline as the canonical files, inside the same
    locked swap as ``schedule.json``/``decisions.json``, so a reference to a
    newly archived artifact and the artifact itself are never installed
    separately. An already carried-forward file is only accepted when its bytes
    are identical, so a hardlinked active file is never truncated. Returns the
    newly written files.
    """

    written: list[Path] = []
    for relative_path, content in extra_evidence.items():
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise SeasonStateError(
                f"Refusing canonical commit: illegal evidence path {relative_path!r}"
            )
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() == content:
                continue
            raise SeasonStateError(
                "Refusing canonical commit: staged evidence "
                f"{relative_path} already exists with different content"
            )
        staging_fd, staging_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        os.close(staging_fd)
        temp = Path(staging_name)
        try:
            temp.write_bytes(content)
            with temp.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)
        written.append(target)
    return written
