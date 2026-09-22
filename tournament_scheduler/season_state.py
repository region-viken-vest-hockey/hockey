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
    export_context_path,
    load_decisions,
    load_export_context,
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
    "add_banned_date",
    "add_request_constraint",
    "allow_holiday_date",
    "apply_candidate",
    "approval_report",
    "approve_tournament",
    "banned_date_report",
    "batch_maintenance",
    "canonical_state_revision",
    "calendar_booking_candidates",
    "calendar_booking_findings",
    "change_protection_report",
    "confirm_calendar_booking",
    "decisions_path",
    "effective_config_from_verification_problem",
    "export_context_path",
    "fill_guest_slot",
    "guest_slot_candidates",
    "guest_slot_report",
    "holiday_date_exception_report",
    "load_decisions",
    "load_export_context",
    "load_json",
    "load_participation_acceptances",
    "load_schedule",
    "move_tournament",
    "normalize_arena_identities",
    "normalize_placements",
    "swap_participants",
    "participation_acceptance_id",
    "planning_checkpoint_from_schedule",
    "promote_from_stage3",
    "record_participation_acceptance",
    "refresh_calendars",
    "release_banned_dates",
    "release_change_protections",
    "disallow_holiday_dates",
    "release_guest_slot",
    "release_request_constraints",
    "request_constraint_report",
    "reserve_guest_slot",
    "revoke_participation_acceptance",
    "schedule_fingerprint",
    "schedule_path",
    "season_baseline_advance",
    "season_baseline_create",
    "season_baseline_replace",
    "season_baseline_show",
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
    request_id: str | None = None,
    allow_manual_placement: bool = False,
    allow_host_confirmation: bool = False,
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
        request_id=request_id,
        allow_manual_placement=allow_manual_placement,
        allow_host_confirmation=allow_host_confirmation,
    )


def swap_participants(
    *,
    season: str,
    tournament_a_id: str,
    team_a_label: str,
    tournament_b_id: str,
    team_b_label: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    problem: dict[str, Any] | None = None,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Swap one participant between two same-age canonical tournaments."""

    return _service(root).swap_participants(
        season=season,
        tournament_a_id=tournament_a_id,
        team_a_label=team_a_label,
        tournament_b_id=tournament_b_id,
        team_b_label=team_b_label,
        problem=problem,
        actor=actor,
        note=note,
        dry_run=dry_run,
        request_id=request_id,
    )


def season_baseline_show(
    *,
    season: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
) -> dict[str, Any]:
    return _service(root).season_baseline_show(season)


def season_baseline_create(
    *,
    season: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    return _service(root).season_baseline_create(season=season, actor=actor, note=note)


def season_baseline_replace(
    *,
    season: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    return _service(root).season_baseline_create(
        season=season, actor=actor, note=note, replace=True
    )


def season_baseline_advance(
    *,
    season: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    return _service(root).season_baseline_advance(season=season, actor=actor, note=note)


def refresh_calendars(
    *,
    season: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    input_path: str | os.PathLike[str] = "input.xlsx",
    work_dir: str | os.PathLike[str] | None = None,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
    allow_missing_sources: bool = False,
) -> dict[str, Any]:
    return _service(root).refresh_calendars(
        season=season,
        input_path=input_path,
        work_dir=work_dir,
        actor=actor,
        note=note,
        dry_run=dry_run,
        allow_missing_sources=allow_missing_sources,
    )


def batch_maintenance(
    *,
    season: str,
    operations: list[dict[str, Any]],
    scope: list[str] | None = None,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    problem: dict[str, Any] | None = None,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
    request_id: str | None = None,
    allow_manual_placement: bool = False,
    allow_host_confirmation: bool = False,
) -> dict[str, Any]:
    """Atomically compose several scoped canonical mutations in one commit."""

    return _service(root).batch_maintenance(
        season=season,
        operations=operations,
        scope=scope,
        problem=problem,
        actor=actor,
        note=note,
        dry_run=dry_run,
        request_id=request_id,
        allow_manual_placement=allow_manual_placement,
        allow_host_confirmation=allow_host_confirmation,
    )


def calendar_booking_candidates(
    *,
    season: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    club: str | None = None,
    problem: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _service(root).calendar_booking_candidates(season=season, club=club, problem=problem)


def calendar_booking_findings(
    *,
    season: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    problem: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _service(root).calendar_booking_findings(season=season, problem=problem)


def confirm_calendar_booking(
    *,
    season: str,
    event_fingerprint: str,
    tournament_id: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    actor: str | None = None,
    note: str = "",
    problem: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    return _service(root).confirm_calendar_booking(
        season=season,
        event_fingerprint=event_fingerprint,
        tournament_id=tournament_id,
        actor=actor,
        note=note,
        problem=problem,
        dry_run=dry_run,
    )


def change_protection_report(
    season: str,
    *,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    include_released: bool = False,
) -> dict[str, Any]:
    """Return accepted-change protections for a canonical season."""

    return _service(root).change_protection_report(
        season,
        include_released=include_released,
    )


def release_change_protections(
    *,
    season: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    protection_ids: list[str] | None = None,
    request_id: str | None = None,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Release protections when a newer request explicitly supersedes them."""

    return _service(root).release_change_protections(
        season=season,
        protection_ids=protection_ids,
        request_id=request_id,
        actor=actor,
        note=note,
    )


def request_constraint_report(
    season: str,
    *,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    include_released: bool = False,
) -> dict[str, Any]:
    """Return typed request constraints with their derived satisfaction status."""

    return _service(root).request_constraint_report(
        season,
        include_released=include_released,
    )


def add_request_constraint(
    *,
    season: str,
    type: str,
    request_id: str,
    teams: list[dict[str, Any]] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_days: int | None = None,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Persist one validated typed request constraint as a decision-only write."""

    return _service(root).add_request_constraint(
        season=season,
        type=type,
        request_id=request_id,
        teams=teams,
        date_from=date_from,
        date_to=date_to,
        min_days=min_days,
        actor=actor,
        note=note,
    )


def release_request_constraints(
    *,
    season: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    constraint_ids: list[str] | None = None,
    request_id: str | None = None,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Release request constraints when a newer request explicitly supersedes them."""

    return _service(root).release_request_constraints(
        season=season,
        constraint_ids=constraint_ids,
        request_id=request_id,
        actor=actor,
        note=note,
    )


def allow_holiday_date(
    *,
    season: str,
    date: str,
    reason: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    actor: str | None = None,
) -> dict[str, Any]:
    """Persist one season-level exception to a derived holiday exclusion."""

    return _service(root).allow_holiday_date(
        season=season,
        date=date,
        reason=reason,
        actor=actor,
    )


def disallow_holiday_dates(
    *,
    season: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    dates: list[str] | None = None,
    exception_ids: list[str] | None = None,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Release active holiday-date exceptions with audit history."""

    return _service(root).disallow_holiday_dates(
        season=season,
        dates=dates,
        exception_ids=exception_ids,
        actor=actor,
        note=note,
    )


def holiday_date_exception_report(
    season: str,
    *,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    include_released: bool = False,
) -> dict[str, Any]:
    """Return active holiday-date exceptions and provenance."""

    return _service(root).holiday_date_exception_report(
        season,
        include_released=include_released,
    )


def add_banned_date(
    *,
    season: str,
    date: str,
    request_id: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Persist one global operator date ban as a policy/decision-only write."""

    return _service(root).add_banned_date(
        season=season,
        date=date,
        request_id=request_id,
        actor=actor,
        note=note,
    )


def release_banned_dates(
    *,
    season: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    date_ids: list[str] | None = None,
    dates: list[str] | None = None,
    request_id: str | None = None,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Explicitly remove active operator banned dates with audit history."""

    return _service(root).release_banned_dates(
        season=season,
        date_ids=date_ids,
        dates=dates,
        request_id=request_id,
        actor=actor,
        note=note,
    )


def banned_date_report(
    season: str,
    *,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    include_released: bool = False,
) -> dict[str, Any]:
    """Return active operator banned dates with affected tournament ids."""

    return _service(root).banned_date_report(
        season,
        include_released=include_released,
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
    allow_guest_slot_changes: bool = False,
    allow_manual_placement: bool = False,
    allow_host_confirmation: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Apply a verified replan candidate to canonical season state."""

    return _service(root).apply_candidate(
        season=season,
        candidate=candidate,
        problem=problem,
        actor=actor,
        change_weights=change_weights,
        allow_guest_slot_changes=allow_guest_slot_changes,
        allow_manual_placement=allow_manual_placement,
        allow_host_confirmation=allow_host_confirmation,
    )


def approval_report(
    season: str,
    *,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
) -> dict[str, Any]:
    """Read-only approval/lock status for every canonical tournament."""

    return _service(root).approval_report(season)



def guest_slot_report(
    season: str,
    *,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
) -> dict[str, Any]:
    """Read-only lifecycle status of every reserved guest place."""

    return _service(root).guest_slot_report(season)


def guest_slot_candidates(
    *,
    season: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    age_groups: list[str] | tuple[str, ...] | None = None,
    max_per_tournament: int = 1,
    problem: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic candidate facts for a policy-level reservation request."""

    return _service(root).guest_slot_candidates(
        season=season,
        age_groups=age_groups,
        max_per_tournament=max_per_tournament,
        problem=problem,
    )


def reserve_guest_slot(
    *,
    season: str,
    tournament_id: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    count: int = 1,
    displaced_teams: list[str] | None = None,
    problem: dict[str, Any] | None = None,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Reserve one or more guest places on one canonical tournament."""

    return _service(root).reserve_guest_slot(
        season=season,
        tournament_id=tournament_id,
        count=count,
        displaced_teams=displaced_teams,
        problem=problem,
        actor=actor,
        note=note,
        dry_run=dry_run,
    )


def fill_guest_slot(
    *,
    season: str,
    tournament_id: str,
    slot_id: str | None,
    external_team: dict[str, Any],
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    problem: dict[str, Any] | None = None,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Accept an external team into a reserved place and regenerate games."""

    return _service(root).fill_guest_slot(
        season=season,
        tournament_id=tournament_id,
        slot_id=slot_id,
        external_team=external_team,
        problem=problem,
        actor=actor,
        note=note,
    )


def release_guest_slot(
    *,
    season: str,
    tournament_id: str,
    slot_id: str | None = None,
    replacement_team: dict[str, Any] | None = None,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    problem: dict[str, Any] | None = None,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Release a reservation, optionally filling it with a real RVV team."""

    return _service(root).release_guest_slot(
        season=season,
        tournament_id=tournament_id,
        slot_id=slot_id,
        replacement_team=replacement_team,
        problem=problem,
        actor=actor,
        note=note,
        dry_run=dry_run,
    )


def normalize_placements(
    *,
    season: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    problem: dict[str, Any] | None = None,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Upgrade a canonical plan to the placed/provisional/unplaced state model."""

    return _service(root).normalize_placements(
        season=season,
        problem=problem,
        actor=actor,
        note=note,
        dry_run=dry_run,
    )


def normalize_arena_identities(
    *,
    season: str,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Re-emit the canonical schedulable arena across a promoted season."""

    return _service(root).normalize_arena_identities(
        season=season,
        actor=actor,
        note=note,
        dry_run=dry_run,
    )


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
