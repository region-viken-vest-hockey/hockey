"""Single resolver for shared repository assets that the application reads at runtime.

Shared agent assets such as ``.agents/skills/rvv/SKILL.md`` live at the repository
root, outside the ``tournament_scheduler`` package. Code must not build those
paths from its own ``__file__``, because a frozen PyInstaller build has no such
source layout. Every such read goes through :func:`repository_resource`.

* Source checkout: resolves against the repository root.
* Frozen (PyInstaller onedir): resolves against ``sys._MEIPASS``, where
  ``packaging/windows/rvv-miniputt.spec`` bundles the same relative paths as
  explicit data resources.

Only read-only, immutable package resources belong here. Mutable operator data
goes under the data root (see ``distribution.py``).
"""

from __future__ import annotations

import sys
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parents[1]


def package_base_dir() -> Path:
    """Directory that holds the bundled repository resources for this process."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return _SOURCE_ROOT


def repository_resource(*parts: str) -> Path:
    """Return the path of a shared repository resource given its repository-relative parts."""
    if not parts:
        raise ValueError("a repository resource path needs at least one component")
    return package_base_dir().joinpath(*parts)
