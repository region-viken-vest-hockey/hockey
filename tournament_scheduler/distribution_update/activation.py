"""Rollback-safe activation of a verified release inside the application folder.

Only directories inside the install root are renamed. Operator data is checked
for separation and never touched, so activating or rolling back a release
cannot change canonical season state, exports or the local repository.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .. import distribution
from .errors import ActivationError

PREVIOUS_SUFFIX = ".previous"

# Indirection so tests can inject a failing rename at each step.
_rename = os.replace


def previous_release_path(slot: Path) -> Path:
    return slot.with_name(slot.name + PREVIOUS_SUFFIX)


def activate_release(
    staged_app: Path,
    slot: Path,
    *,
    install_root: Path,
    data_root: Path,
) -> None:
    """Swap ``staged_app`` into ``slot``, keeping the previous release for rollback.

    Only directories inside ``install_root`` are renamed; the data root is checked
    for separation and never touched. The sequence is:

    1. recover from an interrupted earlier swap (slot missing, previous present);
    2. drop a stale previous release left by a completed swap;
    3. move the active release aside as ``<slot>.previous``;
    4. move the staged release into ``slot``; on failure, move the previous release back.
    """
    distribution.ensure_data_outside_application(install_root, data_root)
    data = data_root.resolve()
    if data == slot.resolve() or slot.resolve() in data.parents:
        raise ActivationError("operator data root must not be inside the application slot")
    if not staged_app.is_dir():
        raise ActivationError(f"staged release {staged_app} is not a directory")

    previous = previous_release_path(slot)
    if not slot.exists() and previous.exists():
        _rename(previous, slot)
    if slot.exists() and previous.exists():
        shutil.rmtree(previous)

    had_active = slot.exists()
    if had_active:
        _rename(slot, previous)
    try:
        _rename(staged_app, slot)
    except OSError as exc:
        if had_active:
            _rename(previous, slot)
        raise ActivationError(f"could not activate staged release: {exc}") from exc


def rollback_release(slot: Path) -> None:
    """Restore the previous release after a failed post-update check."""
    previous = previous_release_path(slot)
    if not previous.exists():
        raise ActivationError(f"no previous release to restore at {previous}")
    failed = slot.with_name(slot.name + ".failed")
    if failed.exists():
        shutil.rmtree(failed)
    if slot.exists():
        _rename(slot, failed)
    _rename(previous, slot)
    if failed.exists():
        shutil.rmtree(failed)


def confirm_release(slot: Path) -> None:
    """Discard the previous release once the new one has passed its post-update check."""
    previous = previous_release_path(slot)
    if previous.exists():
        shutil.rmtree(previous)
