"""Resolve a canonical season plan by its recorded revision.

A publication manifest records the canonical revision it published. Recovering a
legacy published baseline -- one written before manifests carried a
``schedule_projection`` -- needs the canonical plan *as it was at that
revision*. The current canonical plan is not a substitute: placement fields and
membership have changed, so binding legacy rows against it would reconstruct
identity from row order instead of recovering it.

Resolution order:

1. ``<season_root>/revisions/<season>/<revision>.json`` -- a durable snapshot
   of the canonical schedule payload for that revision.
2. the committed history of ``season/<season>/schedule.json`` in the repository
   that owns the season root.

The function returns ``None`` when the revision cannot be resolved
unambiguously. Callers must fail closed; this module never guesses identity.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any, Mapping

REVISIONS_DIRNAME = "revisions"


def revision_snapshot_path(
    season: str,
    revision: str,
    *,
    season_root: str | Path = "season",
) -> Path:
    """Return the durable revision snapshot path for *season*/*revision*.

    Snapshots live next to -- not inside -- the season directory because the
    canonical-season store installs a season by swapping the whole
    ``season/<season>`` directory; anything placed inside it does not survive an
    ordinary canonical commit.
    """

    return Path(season_root) / REVISIONS_DIRNAME / season / f"{revision}.json"


def _schedule_payload_for_revision(payload: Any, *, revision: str) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    if str(payload.get("revision") or "") != revision:
        return None
    return dict(payload)


def _plan_from_schedule_payload(payload: Any, *, revision: str) -> dict[str, Any] | None:
    schedule = _schedule_payload_for_revision(payload, revision=revision)
    if schedule is None:
        return None
    plan = schedule.get("plan")
    return dict(plan) if isinstance(plan, Mapping) else None


def _load_durable_snapshot(
    season: str,
    revision: str,
    *,
    season_root: str | Path,
) -> dict[str, Any] | None:
    path = revision_snapshot_path(season, revision, season_root=season_root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    schedule = _schedule_payload_for_revision(payload, revision=revision)
    return schedule


def _git(args: list[str], *, cwd: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:  # git not installed
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _load_committed_snapshot(
    season: str,
    revision: str,
    *,
    season_root: str | Path,
) -> dict[str, Any] | None:
    start = Path(season_root)
    if not start.exists():
        start = Path.cwd()
    top = _git(["rev-parse", "--show-toplevel"], cwd=start)
    if not top or not top.strip():
        return None
    repo_root = Path(top.strip())
    season_file = (Path(season_root) / season / "schedule.json").resolve()
    try:
        rel_path = season_file.relative_to(repo_root)
    except ValueError:
        return None
    rel = rel_path.as_posix()
    commits = _git(["rev-list", "HEAD", "--", rel], cwd=repo_root)
    if not commits:
        return None
    for commit in commits.splitlines():
        commit = commit.strip()
        if not commit:
            continue
        blob = _git(["show", f"{commit}:{rel}"], cwd=repo_root)
        if not blob:
            continue
        try:
            payload = json.loads(blob)
        except json.JSONDecodeError:
            continue
        schedule = _schedule_payload_for_revision(payload, revision=revision)
        if schedule is not None:
            return schedule
    return None


def load_canonical_schedule_at_revision(
    season: str,
    revision: str,
    *,
    season_root: str | Path = "season",
) -> dict[str, Any] | None:
    """Return the full canonical schedule payload for *revision*, or ``None``.

    The whole schedule (including its bound ``verification_context``) is
    returned, not only the plan, because the versioned operational projection
    needs the historical occupied interval. ``None`` means the revision cannot
    be resolved from durable snapshots or committed history; it is never a
    signal to fall back to row order or to the current canonical plan.
    """

    revision = str(revision or "").strip()
    season = str(season or "").strip()
    if not revision or not season:
        return None
    if any(separator in revision for separator in ("/", "\\", "..")):
        return None
    if any(separator in season for separator in ("/", "\\", "..")):
        return None
    durable = _load_durable_snapshot(season, revision, season_root=season_root)
    if durable is not None:
        return durable
    return _load_committed_snapshot(season, revision, season_root=season_root)


def load_canonical_plan_at_revision(
    season: str,
    revision: str,
    *,
    season_root: str | Path = "season",
) -> dict[str, Any] | None:
    """Return the canonical plan recorded for *revision*, or ``None``."""

    schedule = load_canonical_schedule_at_revision(season, revision, season_root=season_root)
    if not isinstance(schedule, Mapping):
        return None
    plan = schedule.get("plan")
    return dict(plan) if isinstance(plan, Mapping) else None


__all__ = [
    "REVISIONS_DIRNAME",
    "load_canonical_plan_at_revision",
    "load_canonical_schedule_at_revision",
    "revision_snapshot_path",
]
