"""The single canonical-season mutation service.

Every production write to a promoted season goes through
:class:`CanonicalSeasonService`. The service owns one deterministic lifecycle:

    load snapshot
      -> mutate (schedule and/or decisions)
      -> verify the full candidate
      -> reconcile derived projections and decisions
      -> append history
      -> compute the canonical-state revision
      -> write both files through CanonicalSeasonStore

Schedule-changing operations (``move_tournament`` / ``apply_candidate`` /
``promote``) and decision-only operations (``approve`` / ``unapprove`` /
participation accept/revoke) share that boundary, so a decision-only change
still produces a fresh ``canonical_state_revision`` and a rejected mutation
leaves both canonical files untouched.

This module holds no persistence mechanics (that is the store) and no
scheduling rules (those are domain providers/verifier); it is the application
layer that sequences them.
"""


from __future__ import annotations

import os
from typing import Any, Mapping

from tournament_scheduler.infrastructure.canonical_season_store import (
    CanonicalSeasonSnapshot,
    CanonicalSeasonStore,
    DEFAULT_SEASON_ROOT,
)
from .canonical_season import (
    approvals as _approvals,
    baseline as _baseline,
    batch as _batch,
    calendars as _calendars,
    candidates as _candidates,
    constraints as _constraints,
    guest_slots as _guest_slots,
    lifecycle as _lifecycle,
    normalization as _normalization,
    placements as _placements,
    replacement as _replacement,
    roster as _roster,
)
from .canonical_season.shared import (
    APPROVED_STATUS,
    STALE_APPROVAL_STATUS,
    PENDING_REVIEW_STATUS,
)


__all__ = [
    "APPROVED_STATUS",
    "CanonicalSeasonService",
    "PENDING_REVIEW_STATUS",
    "STALE_APPROVAL_STATUS",
]


class CanonicalSeasonService:
    """Application-layer mutation service over a canonical-season store."""

    def __init__(
        self,
        store: CanonicalSeasonStore | None = None,
        *,
        root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    ) -> None:
        self.store = store or CanonicalSeasonStore(root)

    def load(self, season: str) -> CanonicalSeasonSnapshot:
        return self.store.load(season)

    def _commit(self, snapshot: CanonicalSeasonSnapshot, *, require_absent: bool = False) -> CanonicalSeasonSnapshot:
        return _lifecycle._commit(self, snapshot=snapshot, require_absent=require_absent)

    def season_baseline_show(self, season: str) -> dict[str, Any]:
        return _baseline.season_baseline_show(self, season=season)

    def season_baseline_create(
        self,
        *,
        season: str,
        note: str = "",
        actor: str | None = None,
        replace: bool = False,
    ) -> dict[str, Any]:
        return _baseline.season_baseline_create(self, season=season, note=note, actor=actor, replace=replace)

    def season_baseline_advance(
        self,
        *,
        season: str,
        note: str = "",
        actor: str | None = None,
    ) -> dict[str, Any]:
        return _baseline.season_baseline_advance(self, season=season, note=note, actor=actor)

    def refresh_calendars(
        self,
        *,
        season: str,
        input_path: str | os.PathLike[str] = "input.xlsx",
        work_dir: str | os.PathLike[str] | None = None,
        actor: str | None = None,
        note: str = "",
        dry_run: bool = False,
        allow_missing_sources: bool = False,
    ) -> dict[str, Any]:
        return _calendars.refresh_calendars(self, season=season, input_path=input_path, work_dir=work_dir, actor=actor, note=note, dry_run=dry_run, allow_missing_sources=allow_missing_sources)

    def _assert_request_constraints_satisfied(
        self,
        plan: Mapping[str, Any],
        decisions: Mapping[str, Any],
        *,
        action: str,
    ) -> None:
        return _lifecycle._assert_request_constraints_satisfied(self, plan=plan, decisions=decisions, action=action)

    def request_constraint_report(
        self,
        season: str,
        *,
        include_released: bool = False,
    ) -> dict[str, Any]:
        return _constraints.request_constraint_report(self, season=season, include_released=include_released)

    def add_request_constraint(
        self,
        *,
        season: str,
        type: str,
        request_id: str,
        teams: list[Mapping[str, Any]] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        min_days: int | None = None,
        actor: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        return _constraints.add_request_constraint(self, season=season, type=type, request_id=request_id, teams=teams, date_from=date_from, date_to=date_to, min_days=min_days, actor=actor, note=note)

    def release_request_constraints(
        self,
        *,
        season: str,
        constraint_ids: list[str] | None = None,
        request_id: str | None = None,
        actor: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        return _constraints.release_request_constraints(self, season=season, constraint_ids=constraint_ids, request_id=request_id, actor=actor, note=note)

    def holiday_date_exception_report(
        self,
        season: str,
        *,
        include_released: bool = False,
    ) -> dict[str, Any]:
        return _constraints.holiday_date_exception_report(self, season=season, include_released=include_released)

    def allow_holiday_date(
        self,
        *,
        season: str,
        date: str,
        reason: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        return _constraints.allow_holiday_date(self, season=season, date=date, reason=reason, actor=actor)

    def disallow_holiday_dates(
        self,
        *,
        season: str,
        dates: list[str] | None = None,
        exception_ids: list[str] | None = None,
        actor: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        return _constraints.disallow_holiday_dates(self, season=season, dates=dates, exception_ids=exception_ids, actor=actor, note=note)

    def banned_date_report(
        self,
        season: str,
        *,
        include_released: bool = False,
    ) -> dict[str, Any]:
        return _constraints.banned_date_report(self, season=season, include_released=include_released)

    def add_banned_date(
        self,
        *,
        season: str,
        date: str,
        request_id: str,
        actor: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        return _constraints.add_banned_date(self, season=season, date=date, request_id=request_id, actor=actor, note=note)

    def release_banned_dates(
        self,
        *,
        season: str,
        date_ids: list[str] | None = None,
        dates: list[str] | None = None,
        request_id: str | None = None,
        actor: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        return _constraints.release_banned_dates(self, season=season, date_ids=date_ids, dates=dates, request_id=request_id, actor=actor, note=note)

    def promote(
        self,
        *,
        work_dir: str | os.PathLike[str] = ".pipeline",
        season: str | None = None,
        actor: str | None = None,
        force: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return _candidates.promote(self, work_dir=work_dir, season=season, actor=actor, force=force)

    def move_tournament(
        self,
        *,
        season: str,
        tournament_id: str,
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
        return _placements.move_tournament(self, season=season, tournament_id=tournament_id, date=date, arena=arena, host_club=host_club, start_time=start_time, problem=problem, actor=actor, note=note, dry_run=dry_run, allow_cross_half=allow_cross_half, run_id=run_id, request_id=request_id, allow_manual_placement=allow_manual_placement, allow_host_confirmation=allow_host_confirmation)

    def swap_participants(
        self,
        *,
        season: str,
        tournament_a_id: str,
        team_a_label: str,
        tournament_b_id: str,
        team_b_label: str,
        problem: dict[str, Any] | None = None,
        actor: str | None = None,
        note: str = "",
        dry_run: bool = False,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return _roster.swap_participants(self, season=season, tournament_a_id=tournament_a_id, team_a_label=team_a_label, tournament_b_id=tournament_b_id, team_b_label=team_b_label, problem=problem, actor=actor, note=note, dry_run=dry_run, request_id=request_id)

    def replace_participant(
        self,
        *,
        season: str,
        tournament_id: str,
        remove_team_label: str,
        add_team_label: str,
        problem: dict[str, Any] | None = None,
        actor: str | None = None,
        note: str = "",
        dry_run: bool = False,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return _replacement.replace_participant(self, season=season, tournament_id=tournament_id, remove_team_label=remove_team_label, add_team_label=add_team_label, problem=problem, actor=actor, note=note, dry_run=dry_run, request_id=request_id)

    def batch_maintenance(
        self,
        *,
        season: str,
        operations: list[Mapping[str, Any]],
        scope: list[str] | None = None,
        problem: dict[str, Any] | None = None,
        actor: str | None = None,
        note: str = "",
        dry_run: bool = False,
        request_id: str | None = None,
        allow_manual_placement: bool = False,
        allow_host_confirmation: bool = False,
    ) -> dict[str, Any]:
        return _batch.batch_maintenance(self, season=season, operations=operations, scope=scope, problem=problem, actor=actor, note=note, dry_run=dry_run, request_id=request_id, allow_manual_placement=allow_manual_placement, allow_host_confirmation=allow_host_confirmation)

    def apply_candidate(
        self,
        *,
        season: str,
        candidate: dict[str, Any],
        problem: dict[str, Any] | None = None,
        actor: str | None = None,
        change_weights: dict[str, float] | None = None,
        allow_guest_slot_changes: bool = False,
        _history_event: Mapping[str, Any] | None = None,
        _new_change_protections: list[dict[str, Any]] | None = None,
        allow_manual_placement: bool = False,
        allow_host_confirmation: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        return _candidates.apply_candidate(self, season=season, candidate=candidate, problem=problem, actor=actor, change_weights=change_weights, allow_guest_slot_changes=allow_guest_slot_changes, _history_event=_history_event, _new_change_protections=_new_change_protections, allow_manual_placement=allow_manual_placement, allow_host_confirmation=allow_host_confirmation)

    def normalize_placements(
        self,
        *,
        season: str,
        problem: dict[str, Any] | None = None,
        actor: str | None = None,
        note: str = "",
        dry_run: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return _normalization.normalize_placements(self, season=season, problem=problem, actor=actor, note=note, dry_run=dry_run)

    def normalize_arena_identities(
        self,
        *,
        season: str,
        actor: str | None = None,
        note: str = "",
        dry_run: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return _normalization.normalize_arena_identities(self, season=season, actor=actor, note=note, dry_run=dry_run)

    def guest_slot_report(self, season: str) -> dict[str, Any]:
        return _guest_slots.guest_slot_report(self, season=season)

    def guest_slot_candidates(
        self,
        *,
        season: str,
        age_groups: list[str] | tuple[str, ...] | None = None,
        max_per_tournament: int = 1,
        problem: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _guest_slots.guest_slot_candidates(self, season=season, age_groups=age_groups, max_per_tournament=max_per_tournament, problem=problem)

    @staticmethod
    def _displaceable_team_options(tournament: Mapping[str, Any]) -> list[dict[str, Any]]:
        return _guest_slots._displaceable_team_options(tournament=tournament)

    def reserve_guest_slot(
        self,
        *,
        season: str,
        tournament_id: str,
        count: int = 1,
        displaced_teams: list[str] | None = None,
        problem: dict[str, Any] | None = None,
        actor: str | None = None,
        note: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return _guest_slots.reserve_guest_slot(self, season=season, tournament_id=tournament_id, count=count, displaced_teams=displaced_teams, problem=problem, actor=actor, note=note, dry_run=dry_run)

    def fill_guest_slot(
        self,
        *,
        season: str,
        tournament_id: str,
        slot_id: str | None,
        external_team: dict[str, Any],
        problem: dict[str, Any] | None = None,
        actor: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        return _guest_slots.fill_guest_slot(self, season=season, tournament_id=tournament_id, slot_id=slot_id, external_team=external_team, problem=problem, actor=actor, note=note)

    def release_guest_slot(
        self,
        *,
        season: str,
        tournament_id: str,
        slot_id: str | None = None,
        replacement_team: dict[str, Any] | None = None,
        problem: dict[str, Any] | None = None,
        actor: str | None = None,
        note: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return _guest_slots.release_guest_slot(self, season=season, tournament_id=tournament_id, slot_id=slot_id, replacement_team=replacement_team, problem=problem, actor=actor, note=note, dry_run=dry_run)

    def change_protection_report(
        self,
        season: str,
        *,
        include_released: bool = False,
    ) -> dict[str, Any]:
        return _approvals.change_protection_report(self, season=season, include_released=include_released)

    def release_change_protections(
        self,
        *,
        season: str,
        protection_ids: list[str] | None = None,
        request_id: str | None = None,
        actor: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        return _approvals.release_change_protections(self, season=season, protection_ids=protection_ids, request_id=request_id, actor=actor, note=note)

    def calendar_booking_candidates(
        self,
        *,
        season: str,
        club: str | None = None,
        problem: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _calendars.calendar_booking_candidates(self, season=season, club=club, problem=problem)

    def calendar_booking_findings(self, *, season: str, problem: dict[str, Any] | None = None) -> dict[str, Any]:
        return _calendars.calendar_booking_findings(self, season=season, problem=problem)

    def confirm_calendar_booking(
        self,
        *,
        season: str,
        event_fingerprint: str,
        tournament_id: str,
        actor: str | None = None,
        note: str = "",
        problem: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return _calendars.confirm_calendar_booking(self, season=season, event_fingerprint=event_fingerprint, tournament_id=tournament_id, actor=actor, note=note, problem=problem, dry_run=dry_run)

    def approve_tournament(
        self,
        *,
        season: str,
        tournament_id: str,
        actor: str | None = None,
        note: str = "",
        placement_locked: bool = True,
        participants_locked: bool = False,
        problem: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _approvals.approve_tournament(self, season=season, tournament_id=tournament_id, actor=actor, note=note, placement_locked=placement_locked, participants_locked=participants_locked, problem=problem)

    def unapprove_tournament(
        self,
        *,
        season: str,
        tournament_id: str,
        actor: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        return _approvals.unapprove_tournament(self, season=season, tournament_id=tournament_id, actor=actor, note=note)

    def load_participation_acceptances(self, season: str) -> list[dict[str, Any]]:
        return _approvals.load_participation_acceptances(self, season=season)

    def record_participation_acceptance(
        self,
        *,
        season: str,
        club: str,
        label: str,
        age_group: str,
        scope: str,
        direction: str,
        actual: int,
        target: int,
        actor: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        return _approvals.record_participation_acceptance(self, season=season, club=club, label=label, age_group=age_group, scope=scope, direction=direction, actual=actual, target=target, actor=actor, note=note)

    def revoke_participation_acceptance(
        self,
        *,
        season: str,
        club: str,
        label: str,
        age_group: str,
        scope: str,
        actor: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        return _approvals.revoke_participation_acceptance(self, season=season, club=club, label=label, age_group=age_group, scope=scope, actor=actor, note=note)

    def approval_report(self, season: str) -> dict[str, Any]:
        return _approvals.approval_report(self, season=season)
