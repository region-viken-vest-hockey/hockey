"""Staged download, SHA-256 verification and safe extraction of a release archive.

Everything here happens inside a staging directory; nothing is activated until
:mod:`activation` is called with the extracted application folder.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import zipfile
from pathlib import Path, PurePosixPath

from .check import Transport
from .errors import IntegrityError
from .manifest import Artifact

APP_FOLDER_NAME = "rvv-miniputt"


def verify_file(path: Path, artifact: Artifact) -> None:
    actual_size = path.stat().st_size
    if actual_size != artifact.size:
        raise IntegrityError(f"{artifact.filename}: size {actual_size} does not match manifest {artifact.size}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if not hmac.compare_digest(digest.hexdigest(), artifact.sha256):
        raise IntegrityError(f"{artifact.filename}: SHA-256 does not match the manifest")


def download_verified(artifact: Artifact, staging_dir: Path, transport: Transport) -> Path:
    """Download into ``staging_dir`` and return the verified archive path.

    The archive only appears under its final name after its size and SHA-256 match.
    Any failure removes the partial file, so an interrupted download cannot be activated.
    """
    staging_dir.mkdir(parents=True, exist_ok=True)
    partial = staging_dir / (artifact.filename + ".partial")
    final = staging_dir / artifact.filename
    try:
        transport.download(artifact.url, partial, artifact.size)
        verify_file(partial, artifact)
        os.replace(partial, final)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return final


def extract_release(archive: Path, dest: Path) -> Path:
    """Extract a release archive into a new directory and return the application folder inside it."""
    if dest.exists():
        raise IntegrityError(f"extraction target {dest} already exists")
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            name = member.filename
            parts = PurePosixPath(name).parts
            if (
                not parts
                or PurePosixPath(name).is_absolute()
                or ".." in parts
                or "\\" in name
                or ":" in parts[0]
            ):
                raise IntegrityError(f"unsafe archive member {name!r}")
        dest.mkdir(parents=True)
        bundle.extractall(dest)
    app_folder = dest / APP_FOLDER_NAME
    if not app_folder.is_dir():
        raise IntegrityError(f"archive does not contain the {APP_FOLDER_NAME}/ application folder")
    return app_folder
