"""First-class team retirement for published-season maintenance.

A team retirement is a scoped published-season maintenance operation that
cancels the team's future hosting obligations and withdraws it from away
tournaments, with optional rebalancing of affected away tournaments.

This is distinct from:
- ``remove-participant``: one-tournament roster removal without pool change
- ``participation-withdrawal``: durable age-group ineligibility without hosting cancellation

A retirement combines both: hosted tournaments are cancelled as hosting
obligations, and away participation is withdrawn with a durable
age-group-scoped withdrawal record.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from tournament_scheduler.canonical_baseline import (
    build_canonical_baseline,
    change_cost,
    verify_canonical_locks,
)
from tournament_scheduler.canonical_state import (
    CANONICAL_STATE_REVISION_KEY,
    CHANGE_PROTECTIONS_KEY,
    PARTICIPATION_WITHDRAWALS_KEY,
    canonical_state_revision,
    schedule_fingerprint,
)
from tournament_scheduler.change_protections import (
    ACTIVE as CHANGE_PROTECTION_ACTIVE,
    MUST_NOT_PARTICIPATE,
    build_net_roster_protections,
    protection_violations,
)
from tournament_scheduler.hosting_responsibility import (
    unexplained_responsibility_transfers,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    SeasonStateError,
)
from tournament_scheduler.participation_withdrawals import (
    ACTIVE as WITHDRAWAL_ACTIVE,
    SCOPE_AGE_GROUP,
    build_withdrawal_records,
    effective_from_for_tournaments,
    project_into_problem,
)
from tournament_scheduler.plan_derived_state import reconcile_plan_derived_state
from tournament_scheduler.planning_contract import verify_candidate
from tournament_scheduler.request_constraints import compare_request_constraint_violations

from .shared import (
    _append_decision_history,
    _now_iso,
    _operator_identity,
    _regenerate_tournament_games,
    _resolve_plan_problem,
)
from .withdrawal import _apply_remove_participant_to_plan


def _transform_plan_for_retirement(
    plan: dict[str, Any],
    *,
    identity: tuple[str, str, str],
    effective_from: str,
    hosted_tournaments: list[HostedTournament],
    away_tournaments: list[AwayTournament],
    rebalance_proposals: list[RebalanceProposal],
    accept_rebalance: bool,
    problem: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Pure plan transformation for team retirement.

    Returns (cancelled_hosted, withdrawn_away, rebalance_applied).
    """
    # Cancel hosted tournaments
    cancelled_hosted: list[dict[str, Any]] = []
    for hosted_t in hosted_tournaments:
        result = _cancel_hosted_tournament(
            plan,
            tournament_id=hosted_t.tournament_id,
            retiring_team_identity=identity,
        )
        cancelled_hosted.append(
            {
                "tournament_id": hosted_t.tournament_id,
                "date": hosted_t.date,
                "arena": hosted_t.arena,
                "host_club": hosted_t.host_club,
                "was_on_roster": result["retired_team_was_on_roster"],
                "before_fingerprint": hosted_t.before_fingerprint,
            }
        )

    # Withdraw from away tournaments
    withdrawn_away: list[dict[str, Any]] = []
    for away_t in away_tournaments:
        result = _withdraw_from_away_tournament(
            plan,
            tournament_id=away_t.tournament_id,
            retiring_team_identity=identity,
            problem=problem,
        )
        withdrawn_away.append(
            {
                "tournament_id": away_t.tournament_id,
                "date": away_t.date,
                "arena": away_t.arena,
                "host_club": away_t.host_club,
                "before_fingerprint": away_t.before_fingerprint,
                "regenerated_games": len(result["tournament"].get("games") or []),
            }
        )

    # Apply rebalance if accepted
    rebalance_applied: list[dict[str, Any]] = []
    if accept_rebalance and rebalance_proposals:
        rebalance_applied = _apply_rebalance_proposals(plan, rebalance_proposals, problem)

    return cancelled_hosted, withdrawn_away, rebalance_applied


@dataclass
class HostedTournament:
    """A tournament the retiring team/club is responsible for hosting."""

    tournament_id: str
    date: str
    arena: str
    host_club: str
    teams: list[Mapping[str, Any]]
    before_fingerprint: str


@dataclass
class AwayTournament:
    """A tournament hosted by another club where the retiring team participates."""

    tournament_id: str
    date: str
    arena: str
    host_club: str
    teams: list[Mapping[str, Any]]
    before_fingerprint: str


@dataclass
class RebalanceProposal:
    """A candidate team to fill a vacancy in an affected away tournament."""

    tournament_id: str
    vacancy_host_club: str
    candidate_club: str
    candidate_label: str
    candidate_age_group: str
    reason: str
    # Metrics for comparing alternatives
    fairness_before: Mapping[str, float] = field(default_factory=dict)
    fairness_after: Mapping[str, float] = field(default_factory=dict)
    spacing_ok: bool = True
    travel_impact: float = 0.0
    respects_locks: bool = True
    respects_protections: bool = True


@dataclass
class RetirementClassification:
    """Complete classification of a retirement's affected tournaments."""

    hosted_tournaments: list[HostedTournament]
    away_tournaments: list[AwayTournament]
    rebalance_proposals: list[RebalanceProposal]
    unresolved_vacancies: list[str]  # tournament_ids with no viable rebalance
    effective_from: str
    age_group: str
    retiring_club: str
    retiring_team_label: str
    retiring_team_identity: tuple[str, str, str]


def _identify_retiring_team(
    plan: Mapping[str, Any],
    *,
    club: str,
    team_label: str,
    age_group: str,
    effective_from: str,
) -> tuple[tuple[str, str, str], list[Mapping[str, Any]]]:
    """Find the retiring team's canonical identity and all future tournaments.

    Returns (identity, future_tournaments) where future_tournaments are all
    non-cancelled tournaments in the age_group on/after effective_from where
    the team participates.
    """
    tournaments = plan.get("tournaments", []) or []
    future: list[Mapping[str, Any]] = []
    identity: tuple[str, str, str] | None = None

    for tournament in tournaments:
        if not isinstance(tournament, Mapping):
            continue
        if tournament.get("cancelled"):
            continue
        if str(tournament.get("age_group") or "") != age_group:
            continue
        tournament_date = str(tournament.get("date") or "")
        if effective_from and tournament_date and tournament_date < effective_from:
            continue

        for team in tournament.get("teams", []) or []:
            if not isinstance(team, Mapping):
                continue
            if str(team.get("club") or "") == club and str(team.get("label") or "") == team_label:
                if identity is None:
                    identity = (
                        str(team.get("club") or ""),
                        str(team.get("label") or ""),
                        str(team.get("age_group") or age_group),
                    )
                future.append(tournament)
                break

    if identity is None:
        raise SeasonStateError(
            f"No future participation found for {club} / {team_label} in {age_group} "
            f"on/after {effective_from}"
        )

    return identity, future


def _classify_hosted_vs_away(
    future_tournaments: Sequence[Mapping[str, Any]],
    *,
    retiring_club: str,
    retiring_team_label: str,
) -> tuple[list[HostedTournament], list[AwayTournament]]:
    """Classify future tournaments as hosted obligations vs away participation."""
    hosted: list[HostedTournament] = []
    away: list[AwayTournament] = []

    for tournament in future_tournaments:
        host_club = str(tournament.get("host_club") or "")
        tournament_id = str(tournament.get("id") or "")
        if host_club == retiring_club:
            hosted.append(
                HostedTournament(
                    tournament_id=tournament_id,
                    date=str(tournament.get("date") or ""),
                    arena=str(tournament.get("arena") or ""),
                    host_club=host_club,
                    teams=list(tournament.get("teams", []) or []),
                    before_fingerprint="",
                )
            )
        else:
            away.append(
                AwayTournament(
                    tournament_id=tournament_id,
                    date=str(tournament.get("date") or ""),
                    arena=str(tournament.get("arena") or ""),
                    host_club=host_club,
                    teams=list(tournament.get("teams", []) or []),
                    before_fingerprint="",
                )
            )

    return hosted, away


def _compute_before_fingerprints(
    tournaments: list[HostedTournament] | list[AwayTournament],
    decisions: Mapping[str, Any],
) -> None:
    """Populate before_fingerprint for each tournament using approval_fingerprint."""
    from tournament_scheduler.canonical_baseline import approval_fingerprint

    for tournament in tournaments:
        decision = decisions.get("decisions", {}).get(tournament.tournament_id, {})
        tournament.before_fingerprint = approval_fingerprint(
            {
                "id": tournament.tournament_id,
                "teams": tournament.teams,
                **decision,
            }
        )


def _cancel_hosted_tournament(
    plan: dict[str, Any],
    *,
    tournament_id: str,
    retiring_team_identity: tuple[str, str, str],
) -> dict[str, Any]:
    """Cancel a hosted tournament (mark cancelled, preserve provenance)."""
    tournaments = plan.get("tournaments", []) or []
    target = next(
        (t for t in tournaments if str(t.get("id") or "") == tournament_id), None
    )
    if target is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    if target.get("cancelled"):
        raise SeasonStateError(f"Tournament {tournament_id} is already cancelled")

    # Record the retiring team's presence as provenance before cancelling
    retiring_team_on_roster = any(
        str(team.get("club") or "") == retiring_team_identity[0]
        and str(team.get("label") or "") == retiring_team_identity[1]
        for team in target.get("teams", []) or []
    )

    target["cancelled"] = True
    target["cancellation_reason"] = "team_retirement"
    target["retired_team_identity"] = {
        "club": retiring_team_identity[0],
        "label": retiring_team_identity[1],
        "age_group": retiring_team_identity[2],
    }
    target["retired_team_was_host"] = retiring_team_on_roster

    # Clear games since tournament is cancelled
    target["games"] = []

    return {
        "tournament": target,
        "retired_team_was_on_roster": retiring_team_on_roster,
    }


def _withdraw_from_away_tournament(
    plan: dict[str, Any],
    *,
    tournament_id: str,
    retiring_team_identity: tuple[str, str, str],
    problem: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Remove retiring team from an away tournament (participant removal)."""
    return _apply_remove_participant_to_plan(
        plan,
        tournament_id=tournament_id,
        remove_team_label=retiring_team_identity[1],
        problem=problem,
    )


def _generate_rebalance_proposals(
    plan: dict[str, Any],
    problem: Mapping[str, Any],
    away_tournaments: list[AwayTournament],
    *,
    retiring_team_identity: tuple[str, str, str],
) -> tuple[list[RebalanceProposal], list[str]]:
    """Generate rebalance proposals for affected away tournaments.

    Returns (proposals, unresolved_vacancy_tournament_ids).
    """
    proposals: list[RebalanceProposal] = []
    unresolved: list[str] = []

    # Get all registered teams in the age group except the retiring one
    age_group = retiring_team_identity[2]
    registered_teams = [
        team
        for team in problem.get("teams", []) or []
        if isinstance(team, Mapping)
        and str(team.get("age_group") or "") == age_group
        and not (
            str(team.get("club") or "") == retiring_team_identity[0]
            and str(team.get("label") or "") == retiring_team_identity[1]
        )
    ]

    for away in away_tournaments:
        # Check if tournament would be underfilled after withdrawal
        current_teams = [
            team
            for team in away.teams
            if not (
                str(team.get("club") or "") == retiring_team_identity[0]
                and str(team.get("label") or "") == retiring_team_identity[1]
            )
        ]

        # Count required participants based on parallel_games
        parallel_games = problem.get("parallel_games", {}).get(age_group, 2)
        min_teams = parallel_games * 2  # round-robin needs at least 2*parallel teams
        # Actually, for round-robin with parallel games, we need at least parallel_games * 2 teams
        # But the verifier will enforce the exact legal shape

        # Find eligible replacement teams
        eligible_candidates = []
        for candidate in registered_teams:
            candidate_identity = (
                str(candidate.get("club") or ""),
                str(candidate.get("label") or ""),
                str(candidate.get("age_group") or ""),
            )
            # Check if candidate is already in this tournament
            if any(
                str(t.get("club") or "") == candidate_identity[0]
                and str(t.get("label") or "") == candidate_identity[1]
                for t in current_teams
            ):
                continue
            # Check spacing, travel, etc. - simplified for now
            eligible_candidates.append(candidate)

        if not eligible_candidates:
            unresolved.append(away.tournament_id)
            continue

        # For now, select the first eligible candidate as a basic proposal
        # A full implementation would use participant_selection.select_participants_for_tournament
        # with fairness, spacing, travel scoring
        candidate = eligible_candidates[0]
        proposals.append(
            RebalanceProposal(
                tournament_id=away.tournament_id,
                vacancy_host_club=away.host_club,
                candidate_club=str(candidate.get("club") or ""),
                candidate_label=str(candidate.get("label") or ""),
                candidate_age_group=str(candidate.get("age_group") or age_group),
                reason="retirement_vacancy_rebalance",
            )
        )

    return proposals, unresolved


def _apply_rebalance_proposals(
    plan: dict[str, Any],
    proposals: list[RebalanceProposal],
    problem: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Apply accepted rebalance proposals to the plan."""
    applied: list[dict[str, Any]] = []
    for proposal in proposals:
        tournament_id = proposal.tournament_id
        team = {
            "club": proposal.candidate_club,
            "label": proposal.candidate_label,
            "age_group": proposal.candidate_age_group,
            "guest": False,
        }
        # Find and update the tournament
        tournaments = plan.get("tournaments", []) or []
        target = next(
            (t for t in tournaments if str(t.get("id") or "") == tournament_id), None
        )
        if target is None:
            continue
        if target.get("cancelled"):
            continue

        # Add the team
        teams = target.setdefault("teams", [])
        if not any(
            str(t.get("club") or "") == proposal.candidate_club
            and str(t.get("label") or "") == proposal.candidate_label
            for t in teams
        ):
            teams.append(team)
            _regenerate_tournament_games(target, problem)
            applied.append(
                {
                    "tournament_id": tournament_id,
                    "added_team": team,
                }
            )

    return applied


def preview_retire_team(
    service,
    *,
    season: str,
    club: str,
    team_label: str,
    age_group: str,
    effective_from: str,
    request_id: str | None = None,
    actor: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Dry-run preview of a team retirement.

    Classifies all affected tournaments, shows hosted cancellations, away
    withdrawals, rebalance proposals, and material consequences.
    """
    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    problem = _resolve_plan_problem(schedule, None, decisions)
    if problem is None:
        raise SeasonStateError(
            "Team retirement requires a promoted verification-context problem"
        )

    before_plan = schedule.get("plan") or {}
    plan = copy.deepcopy(before_plan)

    # Identify retiring team and future tournaments
    identity, future_tournaments = _identify_retiring_team(
        plan,
        club=club,
        team_label=team_label,
        age_group=age_group,
        effective_from=effective_from,
    )

    # Classify hosted vs away
    hosted, away = _classify_hosted_vs_away(
        future_tournaments,
        retiring_club=club,
        retiring_team_label=team_label,
    )

    # Compute before fingerprints
    _compute_before_fingerprints(hosted, decisions)
    _compute_before_fingerprints(away, decisions)

    # Preview: rebalance proposals
    rebalance_proposals, unresolved = _generate_rebalance_proposals(
        plan, problem, away, retiring_team_identity=identity
    )

    rebalance_preview = [
        {
            "tournament_id": p.tournament_id,
            "vacancy_host_club": p.vacancy_host_club,
            "candidate_club": p.candidate_club,
            "candidate_label": p.candidate_label,
            "candidate_age_group": p.candidate_age_group,
            "reason": p.reason,
        }
        for p in rebalance_proposals
    ]

    # Transform plan for preview (apply all changes)
    cancelled_hosted, withdrawn_away, _ = _transform_plan_for_retirement(
        plan,
        identity=identity,
        effective_from=effective_from,
        hosted_tournaments=hosted,
        away_tournaments=away,
        rebalance_proposals=rebalance_proposals,
        accept_rebalance=False,
        problem=problem,
    )

    hosted_preview = cancelled_hosted
    away_preview = withdrawn_away

    # Build withdrawal record for preview
    resolved_request_id = str(request_id or "").strip()
    if not resolved_request_id:
        raise SeasonStateError(
            "A stable --request-id is required so the operation is auditable and revision-bound"
        )

    request_actor = _operator_identity(actor)
    created_at = _now_iso()
    before_canonical_revision = canonical_state_revision(schedule, decisions)

    withdrawal_records = build_withdrawal_records(
        team={"club": identity[0], "label": identity[1], "age_group": identity[2]},
        tournament_ids=[t.tournament_id for t in away],
        request_id=resolved_request_id,
        actor=request_actor,
        note=note,
        created_at=created_at,
        source_revision=before_canonical_revision,
        effective_from=effective_from_for_tournaments(
            plan, [t.tournament_id for t in away]
        ),
    )

    # Verify the candidate plan
    verification_problem = project_into_problem(problem, records=withdrawal_records, plan=plan)
    result = verify_candidate(plan, verification_problem)

    # Check hosting responsibility
    transfers = unexplained_responsibility_transfers(before_plan, plan, verification_problem)

    # Change protections
    existing_protection_violations = protection_violations(plan, decisions)

    # Request constraints
    constraint_comparison = compare_request_constraint_violations(
        before_plan, plan, decisions
    )

    # Cost
    baseline = build_canonical_baseline(schedule, decisions)
    cost = change_cost(baseline, plan)

    # Lock violations
    lock_violations = verify_canonical_locks(baseline, plan)

    return {
        "season": season,
        "dry_run": True,
        "current_revision": schedule.get("revision"),
        "current_fingerprint": schedule.get("fingerprint"),
        "candidate_revision": schedule_fingerprint(plan),
        "verification_result": result,
        "change_cost": cost,
        "classification": {
            "retiring_team": {
                "club": identity[0],
                "label": identity[1],
                "age_group": identity[2],
            },
            "effective_from": effective_from,
            "hosted_tournaments": {
                "cancel": hosted_preview,
            },
            "away_participation": {
                "remove_from": away_preview,
            },
            "rebalance": {
                "proposals": rebalance_preview,
                "unresolved_vacancies": unresolved,
            },
            "hosting_responsibility": {
                "transfers": transfers,
                "ok": not transfers,
            },
            "verification": {
                "ok": result.get("ok", True),
                "violations": result.get("violations", []),
            },
            "change_protections": {
                "violations": existing_protection_violations,
                "ok": not existing_protection_violations,
            },
            "request_constraints": {
                "regressions": constraint_comparison["regressions"],
                "acceptable": constraint_comparison["acceptable"],
            },
            "can_apply": bool(
                result.get("ok", True)
                and not transfers
                and not existing_protection_violations
                and constraint_comparison["acceptable"]
            ),
        },
    }


def apply_retire_team(
    service,
    *,
    season: str,
    club: str,
    team_label: str,
    age_group: str,
    effective_from: str,
    request_id: str,
    actor: str | None = None,
    note: str = "",
    accept_rebalance: bool = False,
    rebalance_proposals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Apply a team retirement atomically.

    This is the single canonical mutation boundary for team retirement.
    """
    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    problem = _resolve_plan_problem(schedule, None, decisions)
    if problem is None:
        raise SeasonStateError(
            "Team retirement requires a promoted verification-context problem"
        )

    before_plan = schedule.get("plan") or {}
    plan = copy.deepcopy(before_plan)

    # Identify retiring team and future tournaments
    identity, future_tournaments = _identify_retiring_team(
        plan,
        club=club,
        team_label=team_label,
        age_group=age_group,
        effective_from=effective_from,
    )

    # Classify hosted vs away
    hosted, away = _classify_hosted_vs_away(
        future_tournaments,
        retiring_club=club,
        retiring_team_label=team_label,
    )

    # Compute before fingerprints
    _compute_before_fingerprints(hosted, decisions)
    _compute_before_fingerprints(away, decisions)

    # Convert rebalance proposals to RebalanceProposal objects
    rebalance_proposal_objects: list[RebalanceProposal] = []
    if rebalance_proposals:
        for p in rebalance_proposals:
            if isinstance(p, RebalanceProposal):
                rebalance_proposal_objects.append(p)
            else:
                rebalance_proposal_objects.append(
                    RebalanceProposal(
                        tournament_id=p["tournament_id"],
                        vacancy_host_club=p["vacancy_host_club"],
                        candidate_club=p["candidate_club"],
                        candidate_label=p["candidate_label"],
                        candidate_age_group=p["candidate_age_group"],
                        reason=p.get("reason", "retirement_vacancy_rebalance"),
                    )
                )

    # Transform plan (apply all changes)
    cancelled_hosted, withdrawn_away, rebalance_applied = _transform_plan_for_retirement(
        plan,
        identity=identity,
        effective_from=effective_from,
        hosted_tournaments=hosted,
        away_tournaments=away,
        rebalance_proposals=rebalance_proposal_objects,
        accept_rebalance=accept_rebalance,
        problem=problem,
    )

    # Build withdrawal record
    resolved_request_id = str(request_id or "").strip()
    if not resolved_request_id:
        raise SeasonStateError(
            "A stable --request-id is required so the operation is auditable and revision-bound"
        )

    request_actor = _operator_identity(actor)
    created_at = _now_iso()
    before_canonical_revision = canonical_state_revision(schedule, decisions)

    withdrawal_records = build_withdrawal_records(
        team={"club": identity[0], "label": identity[1], "age_group": identity[2]},
        tournament_ids=[t.tournament_id for t in away],
        request_id=resolved_request_id,
        actor=request_actor,
        note=note,
        created_at=created_at,
        source_revision=before_canonical_revision,
        effective_from=effective_from_for_tournaments(
            plan, [t.tournament_id for t in away]
        ),
    )

    # Verify the candidate plan
    verification_problem = project_into_problem(problem, records=withdrawal_records, plan=plan)
    result = verify_candidate(plan, verification_problem)

    if not result.get("ok", True):
        messages = "; ".join(
            str(v.get("message") or v.get("code")) for v in result.get("violations", [])
        )
        raise SeasonStateError(
            f"Refusing team retirement: candidate fails hard verification: {messages}"
        )

    # Check hosting responsibility
    transfers = unexplained_responsibility_transfers(before_plan, plan, verification_problem)
    if transfers:
        messages = "; ".join(str(entry.get("message")) for entry in transfers)
        raise SeasonStateError(
            f"Refusing team retirement: candidate transfers hosting responsibility: {messages}"
        )

    # Change protections
    existing_protection_violations = protection_violations(plan, decisions)
    if existing_protection_violations:
        messages = "; ".join(str(item.get("message")) for item in existing_protection_violations)
        raise SeasonStateError(
            "Refusing team retirement: it would undo an accepted change: " + messages
        )

    # Request constraints
    constraint_comparison = compare_request_constraint_violations(
        before_plan, plan, decisions
    )
    if constraint_comparison["regressions"]:
        messages = "; ".join(
            str(item.get("message")) for item in constraint_comparison["regressions"]
        )
        raise SeasonStateError(
            "Refusing team retirement: it violates an active request constraint: " + messages
        )

    # Build new protections
    new_protections = build_net_roster_protections(
        before_plan=before_plan,
        after_plan=plan,
        tournament_ids=[t["tournament_id"] for t in cancelled_hosted + withdrawn_away],
        request_id=resolved_request_id,
        actor=request_actor,
        note=note,
        created_at=created_at,
        source_revision=before_canonical_revision,
        source_event="team_retirement",
    )

    # Apply through canonical mutation service
    from .scoped_mutation import authorize_team_retirement

    # Convert rebalance_proposals (list of dicts) to RebalanceProposal objects for transformation
    rebalance_proposal_objects: list[RebalanceProposal] = []
    rebalance_proposals_dict = []
    if accept_rebalance and rebalance_proposals:
        for p in rebalance_proposals:
            if isinstance(p, RebalanceProposal):
                obj = p
                d = {
                    "tournament_id": p.tournament_id,
                    "vacancy_host_club": p.vacancy_host_club,
                    "candidate_club": p.candidate_club,
                    "candidate_label": p.candidate_label,
                    "candidate_age_group": p.candidate_age_group,
                    "reason": p.reason,
                }
            else:
                obj = RebalanceProposal(
                    tournament_id=p["tournament_id"],
                    vacancy_host_club=p["vacancy_host_club"],
                    candidate_club=p["candidate_club"],
                    candidate_label=p["candidate_label"],
                    candidate_age_group=p["candidate_age_group"],
                    reason=p.get("reason", "retirement_vacancy_rebalance"),
                )
                d = p
            rebalance_proposal_objects.append(obj)
            rebalance_proposals_dict.append(d)

    scoped_authorization = authorize_team_retirement(
        schedule=schedule,
        decisions=decisions,
        candidate=plan,
        cancelled_hosted_tournament_ids=[t["tournament_id"] for t in cancelled_hosted],
        withdrawn_away_tournament_ids=[t["tournament_id"] for t in withdrawn_away],
        rebalance_applied=rebalance_applied,
        rebalance_proposals=rebalance_proposals_dict,
        retiring_team_identity=identity,
        request_id=resolved_request_id,
        actor=request_actor,
        note=note,
        created_at=created_at,
    )

    updated_schedule, updated_decisions, applied_cost = service.apply_candidate(
        season=season,
        candidate=plan,
        problem=verification_problem,
        actor=actor,
        operation="team_retirement",
        _scoped_authorization=scoped_authorization,
        _new_change_protections=new_protections,
        _new_participation_withdrawals=withdrawal_records,
        _history_event={
            "event": "team_retirement",
            "tournament_id": "",
            "note": note,
            "details": {
                "retiring_team": {"club": identity[0], "label": identity[1], "age_group": identity[2]},
                "effective_from": effective_from,
                "cancelled_hosted": cancelled_hosted,
                "withdrawn_away": withdrawn_away,
                "rebalance_applied": rebalance_applied,
                "request_id": resolved_request_id,
            },
        },
    )

    return {
        "season": season,
        "dry_run": False,
        "revision": updated_schedule.get("revision"),
        "canonical_state_revision": canonical_state_revision(updated_schedule, updated_decisions),
        "verification_result": result,
        "change_cost": applied_cost,
        "retirement": {
            "retiring_team": {"club": identity[0], "label": identity[1], "age_group": identity[2]},
            "effective_from": effective_from,
            "cancelled_hosted": cancelled_hosted,
            "withdrawn_away": withdrawn_away,
            "rebalance_applied": rebalance_applied,
            "request_id": resolved_request_id,
        },
    }


__all__ = [
    "HostedTournament",
    "AwayTournament",
    "RebalanceProposal",
    "RetirementClassification",
    "preview_retire_team",
    "apply_retire_team",
]