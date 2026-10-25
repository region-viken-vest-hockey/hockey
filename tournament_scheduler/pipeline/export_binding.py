"""Bind a publish request to the exact reviewed export (issue #682).

The publish action calls this before it reads any export artifact, so a preview
and an apply both refuse an export whose bytes or canonical revision differ from
what was reviewed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tournament_scheduler.publication.export_lifecycle import (
    export_content_sha256,
    read_export_manifest,
)


def assert_export_binding(
    export_dir: str | Path,
    *,
    expected_export_content_sha256: str,
    expected_canonical_revision: str,
) -> dict[str, Any]:
    """Refuse unless *export_dir* holds the bytes that were reviewed (issue #682).

    The content hash is recomputed from the artifacts on disk. The manifest is not
    trusted for it, because an edited artifact leaves the manifest unchanged.
    """
    current = read_export_manifest(export_dir)
    if current is None:
        raise ValueError(f"Export lifecycle manifest missing in {export_dir}")
    actual_revision = current.get("canonical_revision")
    if actual_revision != expected_canonical_revision:
        raise ValueError(
            "Canonical revision mismatch: "
            f"export was built at {actual_revision!r}, expected {expected_canonical_revision!r}"
        )
    actual_content = export_content_sha256(export_dir)
    if actual_content != expected_export_content_sha256:
        raise ValueError(
            "Export content mismatch: "
            f"artifacts on disk hash to {actual_content!r}, expected {expected_export_content_sha256!r}"
        )
    return current
