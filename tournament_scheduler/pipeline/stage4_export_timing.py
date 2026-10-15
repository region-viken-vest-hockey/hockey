"""Build-timestamp resolution and old-export pruning for Stage 4 exports."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from tournament_scheduler.infrastructure.canonical_season_store import load_decisions
from tournament_scheduler.publication.export_lifecycle import (
    canonical_export_references,
    prune_draft_exports,
)
from .stage4_export_errors import Stage4Error

logger = logging.getLogger(__name__)

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


def canonical_export_references_for_season(
    season: str | None,
    *,
    season_root: str | os.PathLike[str] | None,
) -> set[str]:
    """Return export directory names that canonical state of *season* references.

    Fails closed: a season that is named without a resolvable canonical root
    cannot prove which exports canonical state still relies on, so the caller
    must not prune.
    """
    if not season:
        return set()
    if season_root is None:
        raise Stage4Error(
            f"Kan ikke verifisere kanoniske eksportreferanser for sesong {season}; eksportopprydding stoppet"
        )
    return canonical_export_references(load_decisions(season, root=season_root))


def _prune_draft_exports_for_export(
    export_root: Path,
    canonical_season: str | None,
    resolved: dict | None,
) -> list[str]:
    """Prune drafts for a finished export; on any doubt delete nothing.

    Pruning only removes disposable drafts, so skipping it is always safe. An
    unprovable retention decision therefore logs a warning instead of failing
    the export.
    """
    try:
        return _prune_old_exports(
            export_root,
            canonical_season=canonical_season,
            season_root=resolved["root"] if resolved else None,
        )
    except Exception as exc:  # noqa: BLE001 - retention fails closed, export continues
        logger.warning("Eksportopprydding hoppet over, ingen eksporter slettet: %s", exc)
        return []


def _git_tracked_export_names(export_root: Path) -> set[str]:
    """Return export directory names that are committed to the repository.

    A committed export is repository-retained evidence, not a disposable draft.
    Outside a git work tree there is nothing committed to protect.
    """
    try:
        probe = subprocess.run(
            ["git", "-C", str(export_root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode != 0 or probe.stdout.strip() != "true":
            return set()
        listed = subprocess.run(
            ["git", "-C", str(export_root), "ls-files", "-z", "--", "."],
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise Stage4Error(f"Kan ikke verifisere git-sporede eksporter: {exc}") from exc
    if listed.returncode != 0:
        detail = listed.stderr.decode("utf-8", errors="replace").strip()
        raise Stage4Error(f"Kan ikke liste git-sporede eksporter: {detail}")
    return {Path(os.fsdecode(raw)).parts[0] for raw in listed.stdout.split(b"\0") if raw}


def _prune_old_exports(
    export_root: Path,
    *,
    keep: int = MAX_KEPT_EXPORTS,
    canonical_season: str | None = None,
    season_root: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Delete old draft timestamped exports, preserving protected revisions.

    Directory names sort chronologically (``YYYY-MM-DDTHHMM``). Non-
    timestamped siblings (e.g. ``review_packets``, ``activities``), published
    exports and unclassified legacy exports are left alone. Exports referenced
    by canonical season state and exports committed to git are retained too:
    they are repository evidence, not disposable drafts. Any failure to prove
    those references raises before anything is deleted.
    """
    if keep <= 0 or not export_root.is_dir():
        return []
    runs = sorted(
        (p for p in export_root.iterdir() if p.is_dir() and _TIMESTAMP_DIR_RE.match(p.name)),
        key=lambda p: p.name,
    )
    retained = canonical_export_references_for_season(canonical_season, season_root=season_root)
    retained |= _git_tracked_export_names(export_root)
    return prune_draft_exports(runs, keep=keep, retained_names=retained)


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
