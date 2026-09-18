"""Compatibility facade over the canonical-season store and mutation service.

The durable promoted-season boundary used to live entirely in this module. It
now has one persistence owner
(:mod:`tournament_scheduler.infrastructure.canonical_season_store`) and one
mutation/verification/reconciliation lifecycle owner
(:class:`tournament_scheduler.application.canonical_season_service.CanonicalSeasonService`).

The functions here are thin, stable facades so existing CLI/planning/maintenance
callers keep their signatures; they contain no independent persistence or
mutation implementation.
"""

from __future__ import annotations

import os
from typing import Any

from tournament_scheduler.application.canonical_season_service import (
    APPROVED_STATUS,
    PENDING_REVIEW_STATUS,
    STALE_APPROVAL_STATUS,
    CanonicalSeasonService,
)
from tournament_scheduler.canonical_state import (
    CANONICAL_STATE_REVISION_KEY,
    PARTICIPATION_ACCEPTANCES_KEY,
    PARTICIPATION_ACCEPTANCE_PREFIX,
    canonical_state_revision,
    participation_acceptance_id,
    schedule_fingerprint,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    DECISIONS_SCHEMA_VERSION,
    DEFAULT_SEASON_ROOT,
    SEASON_STATE_SCHEMA_VERSION,
    SeasonStateError,
    decisions_path,
    load_decisions,
    load_json,
    load_schedule,
    schedule_path,
    season_dir,
    season_id_from_plan,
)

__all__ = [
    "APPROVED_STATUS",
    "CANONICAL_STATE_REVISION_KEY",
    "DECISIONS_SCHEMA_VERSION",
    "DEFAULT_SEASON_ROOT",
    "PARTICIPATION_ACCEPTANCES_KEY",
    "PARTICIPATION_ACCEPTANCE_PREFIX",
    "PENDING_REVIEW_STATUS",
    "SEASON_STATE_SCHEMA_VERSION",
    "STALE_APPROVAL_STATUS",
    "SeasonStateError",
    "apply_candidate",
    "approval_report",
    "approve_tournament",
    "canonical_state_revision",
    "decisions_path",
    "effective_config_from_verification_problem",
    "load_decisions",
    "load_json",
    "load_participation_acceptances",
    "load_schedule",
    "move_tournament",
    "participation_acceptance_id",
    "planning_checkpoint_from_schedule",
    "promote_from_stage3",
    "record_participation_acceptance",
    "revoke_participation_acceptance",
    "schedule_fingerprint",
    "schedule_path",
    "season_dir",
    "season_id_from_plan",
    "unapprove_tournament",
]


def _service(root: str | os.PathLike[str]) -> CanonicalSeasonService:
    return CanonicalSeasonService(root=root)


def planning_checkpoint_from_schedule(schedule: dict[str, Any]) -> dict[str, Any]:
    """Return a Stage-4-compatible planning checkpoint from canonical state."""

    return {
        "plan": dict(schedule["plan"]),
        "canonical_state": {
            "season": schedule.get("season"),
            "revision": schedule.get("revision"),
            "fingerprint": schedule.get("fingerprint"),
            "promoted_from": schedule.get("promoted_from", {}),
            "verification_context": schedule.get("verification_context"),
        },
    }


def effective_config_from_verification_problem(problem: dict[str, Any] | None) -> dict[str, Any]:
    """Return the export-facing config fields preserved in a planning problem.

    Canonical season export must not reload mutable Stage 1/2 checkpoints merely
    to recover display/export settings. The normalized verification problem is
    the durable context promoted with the season, and carries the fields Stage 4
    needs for deterministic duration metadata and report labels.
    """

    if not isinstance(problem, dict):
        return {}
    config: dict[str, Any] = {}
    for key in (
        "start_date",
        "end_date",
        "age_groups",
        "round_length_minutes",
        "ice_time_minutes",
        "rounds_per_tournament",
        "parallel_games",
        "participation_targets_by_age_group",
    ):
        value = problem.get(key)
        if value is not None:
            config[key] = value
    config["age_groups_from_input"] = bool(problem.get("age_groups"))
    return config


def promote_from_stage3(
    *,
    work_dir: str | os.PathLike[str] = ".pipeline",
    season: str | None = None,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    actor: str | None = None,
    force: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Promote the reviewed Stage 4 candidate into canonical season state."""

    return _service(root).promote(work_dir=work_dir, season=season, actor=actor, force=force)


def move_tournament(
    *,
    season: str,
    tournament_id: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    date: str | None = None,
    arena: str | None = None,
    host_club: str | None = None,
    start_time: str | None = None,
    problem: dict[str, Any] | None = None,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
    allow_cross_half: bool = False,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Apply or preview a bounded placement mutation to canonical state."""

    return _service(root).move_tournament(
        season=season,
        tournament_id=tournament_id,
        date=date,
        arena=arena,
        host_club=host_club,
        start_time=start_time,
        problem=problem,
        actor=actor,
        note=note,
        dry_run=dry_run,
        allow_cross_half=allow_cross_half,
        run_id=run_id,
    )


def approve_tournament(
    *,
    season: str,
    tournament_id: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    actor: str | None = None,
    note: str = "",
    placement_locked: bool = True,
    participants_locked: bool = False,
    problem: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Approve/lock one tournament after canonical hard verification."""

    return _service(root).approve_tournament(
        season=season,
        tournament_id=tournament_id,
        actor=actor,
        note=note,
        placement_locked=placement_locked,
        participants_locked=participants_locked,
        problem=problem,
    )


def unapprove_tournament(
    *,
    season: str,
    tournament_id: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Explicitly revoke approval and all locks for one canonical tournament."""

    return _service(root).unapprove_tournament(
        season=season, tournament_id=tournament_id, actor=actor, note=note
    )


def apply_candidate(
    *,
    season: str,
    candidate: dict[str, Any],
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    problem: dict[str, Any] | None = None,
    actor: str | None = None,
    change_weights: dict[str, float] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Apply a verified replan candidate to canonical season state."""

    return _service(root).apply_candidate(
        season=season,
        candidate=candidate,
        problem=problem,
        actor=actor,
        change_weights=change_weights,
    )


def approval_report(
    season: str,
    *,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
) -> dict[str, Any]:
    """Read-only approval/lock status for every canonical tournament."""

    return _service(root).approval_report(season)


def load_participation_acceptances(
    season: str,
    *,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
) -> list[dict[str, Any]]:
    """Return the active (non-revoked) operator participation acceptances."""

    return _service(root).load_participation_acceptances(season)


def record_participation_acceptance(
    *,
    season: str,
    club: str,
    label: str,
    age_group: str,
    scope: str,
    direction: str,
    actual: int,
    target: int,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Persist an explicit operator acceptance of one participation deviation."""

    return _service(root).record_participation_acceptance(
        season=season,
        club=club,
        label=label,
        age_group=age_group,
        scope=scope,
        direction=direction,
        actual=actual,
        target=target,
        actor=actor,
        note=note,
    )


def revoke_participation_acceptance(
    *,
    season: str,
    club: str,
    label: str,
    age_group: str,
    scope: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Revoke one active participation acceptance, preserving its audit trail."""

    return _service(root).revoke_participation_acceptance(
        season=season,
        club=club,
        label=label,
        age_group=age_group,
        scope=scope,
        actor=actor,
        note=note,
    )
