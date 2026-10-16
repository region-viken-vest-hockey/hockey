"""Per-user distribution layout and deterministic version metadata.

Used by the packaged Windows launcher (``packaging/windows``) and by tests.
This module decides *where* files live. It never interprets scheduling,
verification or publication rules; those stay in the application packages.

Layout contract (see ``docs/windows-distribution.md``):

* application files (replaceable on update): ``<install dir>`` under the
  per-user ``%LOCALAPPDATA%\\Programs`` tree, no administrator rights needed;
* mutable operator data (canonical season state, exports, pipeline work
  directory): ``<data root>`` under ``%LOCALAPPDATA%\\RVV Miniputt\\data``,
  or the directory named by ``RVV_MINIPUTT_DATA_DIR``.

The data root must never be inside the application install directory, so an
update or uninstall that replaces the application cannot touch operator data.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, MutableMapping
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

from . import __version__

APP_NAME = "RVV Miniputt"
PACKAGE_NAME = "rvv-miniputt"
DATA_ROOT_ENV_VAR = "RVV_MINIPUTT_DATA_DIR"
# Mirrors ``season.baseline_overlay.SEASON_ROOT_ENV_VAR``; duplicated as a
# string so this module stays free of application imports.
SEASON_ROOT_ENV_VAR = "RVV_CANONICAL_SEASON_ROOT"


def default_data_root(
    env: Mapping[str, str],
    *,
    windows: bool,
    home: PurePath,
) -> PurePath:
    """Return the mutable data root for an operator on this platform.

    An explicit ``RVV_MINIPUTT_DATA_DIR`` wins. Otherwise Windows uses
    ``%LOCALAPPDATA%\\RVV Miniputt\\data`` (falling back to the profile's
    ``AppData\\Local``) and other platforms use ``~/.local/share/rvv-miniputt``.
    """
    flavour = PureWindowsPath if windows else PurePosixPath
    override = env.get(DATA_ROOT_ENV_VAR)
    if override:
        return flavour(override)
    if windows:
        local_app_data = env.get("LOCALAPPDATA") or str(flavour(home) / "AppData" / "Local")
        return flavour(local_app_data) / APP_NAME / "data"
    return flavour(home) / ".local" / "share" / PACKAGE_NAME


UPDATE_CACHE_DIR_NAME = "update-cache"


def default_update_cache_dir() -> Path:
    """Per-user cache of the last signature-verified release manifest (never season state)."""
    root = default_data_root(os.environ, windows=os.name == "nt", home=Path.home())
    return Path(root) / UPDATE_CACHE_DIR_NAME


def ensure_data_outside_application(app_dir: Path, data_root: Path) -> None:
    """Fail closed when mutable data would live inside the application directory."""
    app = app_dir.resolve()
    data = data_root.resolve()
    if data == app or app in data.parents:
        raise ValueError(
            f"Operator data root {data} must not be inside the application directory {app}; "
            f"set {DATA_ROOT_ENV_VAR} to a location outside the install folder."
        )


def prepare_working_root(data_root: Path, env: MutableMapping[str, str]) -> Path:
    """Create the data root, point canonical season state at it, and make it the working directory.

    The application resolves ``season/``, ``export/`` and ``.pipeline/`` relative
    to the current directory, so changing into the data root keeps every
    mutable path outside the application files. An explicitly set season root
    is respected.
    """
    data_root.mkdir(parents=True, exist_ok=True)
    env.setdefault(SEASON_ROOT_ENV_VAR, str(data_root / "season"))
    os.chdir(data_root)
    return data_root


def version_metadata(*, frozen: bool | None = None) -> dict[str, str]:
    """Return stable, build-time-independent version metadata.

    No timestamps, host names or git state are included, so the same source
    tree always produces the same dictionary.
    """
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    return {
        "name": PACKAGE_NAME,
        "version": __version__,
        "distribution": "packaged" if is_frozen else "source",
    }
