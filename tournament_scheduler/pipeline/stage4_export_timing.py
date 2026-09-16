"""Build-timestamp resolution and old-export pruning for Stage 4 exports."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .export_lifecycle import prune_draft_exports
from .stage4_export_errors import Stage4Error

DEFAULT_EXPORT_DIR = "export"
DEFAULT_BASENAME = "season_plan"

# Matches the "%Y-%m-%dT%H%M" directory name this module generates below.
# Callers (e.g. a stage-by-stage orchestrator that picks one export dir up
# front to keep a run's logs and export together) sometimes pass an
# already-timestamped --export-dir. Detecting that here keeps a second,
# nested timestamp from being appended on top of it.
_TIMESTAMP_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{4}$")

# Keep this many draft/transient timestamped export runs on disk. Published
# lifecycle manifests and unclassified legacy exports are protected from this
# normal rolling cleanup.
MAX_KEPT_EXPORTS = 3


def _prune_old_exports(export_root: Path, *, keep: int = MAX_KEPT_EXPORTS) -> list[str]:
    """Delete old draft timestamped exports, preserving protected revisions.

    Directory names sort chronologically (``YYYY-MM-DDTHHMM``). Non-
    timestamped siblings (e.g. ``review_packets``, ``activities``), published
    exports and unclassified legacy exports are left alone.
    """
    if keep <= 0 or not export_root.is_dir():
        return []
    runs = sorted(
        (p for p in export_root.iterdir() if p.is_dir() and _TIMESTAMP_DIR_RE.match(p.name)),
        key=lambda p: p.name,
    )
    return prune_draft_exports(runs, keep=keep)


def _resolve_build_timestamp(build_timestamp: str | int | float | datetime | None = None) -> datetime:
    """Return the canonical UTC content timestamp for a Stage 4 export.

    ``build_timestamp`` wins when provided. Otherwise ``SOURCE_DATE_EPOCH``
    is honored for reproducible builds, falling back to the current wall
    clock. Naive datetimes/ISO strings are treated as UTC because the value
    describes generated content, not a local operator audit moment.
    """
    raw: str | int | float | datetime | None = build_timestamp
    if raw is None:
        raw = os.environ.get("SOURCE_DATE_EPOCH")

    if raw is None or raw == "":
        return datetime.now(timezone.utc).replace(microsecond=0)

    if isinstance(raw, datetime):
        moment = raw
    elif isinstance(raw, (int, float)):
        moment = datetime.fromtimestamp(float(raw), tz=timezone.utc)
    else:
        value = str(raw).strip()
        if not value:
            return datetime.now(timezone.utc).replace(microsecond=0)
        if re.fullmatch(r"\d+(?:\.\d+)?", value):
            moment = datetime.fromtimestamp(float(value), tz=timezone.utc)
        else:
            try:
                moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise Stage4Error(f"Ugyldig build timestamp '{raw}': {exc}") from exc

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0)
