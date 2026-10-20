"""Single owner of same-age participant-swap mutation on an in-memory plan.

Shared by the direct ``swap_participants`` boundary and atomic batch
maintenance. Lock, protection, constraint, verification and consequence gates
stay with the callers.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.season.baseline_overlay import approval_fingerprint

from .shared import _regenerate_tournament_games


def _apply_swap_to_plan(
    plan: dict[str, Any],
    *,
    tournament_a_id: str,
    team_a_label: str,
    tournament_b_id: str,
    team_b_label: str,
    problem: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply one same-age participant swap to an in-memory plan.

    Single owner of roster-swap mutation semantics, shared by the direct
    ``swap_participants`` boundary and atomic batch maintenance: tournament
    existence/cancellation validation, the same-age restriction, participant
    lookup (guests are rejected), duplicate/no-op checks, the actual exchange
    and regeneration of both tournaments' games. Lock, protection, constraint,
    hard-verification, hosting-responsibility and consequence gates stay at the
    caller's boundary.
    """

    if tournament_a_id == tournament_b_id:
        raise SeasonStateError("Participant swap requires two different tournaments")

    tournaments = plan.get("tournaments", []) or []
    by_id = {
        str(tournament.get("id") or ""): tournament
        for tournament in tournaments
        if tournament.get("id")
    }
    tournament_a = by_id.get(tournament_a_id)
    tournament_b = by_id.get(tournament_b_id)
    if tournament_a is None:
        raise SeasonStateError(
            f"Unknown tournament id in canonical schedule: {tournament_a_id}"
        )
    if tournament_b is None:
        raise SeasonStateError(
            f"Unknown tournament id in canonical schedule: {tournament_b_id}"
        )
    for tournament_id, tournament in (
        (tournament_a_id, tournament_a),
        (tournament_b_id, tournament_b),
    ):
        if tournament.get("cancelled"):
            raise SeasonStateError(
                f"Tournament {tournament_id} is cancelled and cannot participate in a roster swap"
            )

    age_group_a = str(tournament_a.get("age_group") or "")
    age_group_b = str(tournament_b.get("age_group") or "")
    if not age_group_a or age_group_a != age_group_b:
        raise SeasonStateError(
            "Participant swap requires tournaments in the same age group; "
            f"got {age_group_a or '<missing>'} and {age_group_b or '<missing>'}"
        )

    def locate_team(
        tournament: dict[str, Any],
        *,
        tournament_id: str,
        label: str,
    ) -> tuple[int, dict[str, Any]]:
        matches = [
            (index, team)
            for index, team in enumerate(tournament.get("teams", []) or [])
            if str(team.get("label") or "") == label
        ]
        if not matches:
            raise SeasonStateError(
                f"Team {label!r} is not a participant in tournament {tournament_id}"
            )
        if len(matches) > 1:
            raise SeasonStateError(
                f"Team label {label!r} is ambiguous in tournament {tournament_id}"
            )
        index, team = matches[0]
        if bool(team.get("guest", False)):
            raise SeasonStateError(
                f"Team {label!r} in tournament {tournament_id} is a guest participant; "
                "use the guest-slot lifecycle instead"
            )
        return index, team

    index_a, team_a = locate_team(
        tournament_a, tournament_id=tournament_a_id, label=team_a_label
    )
    index_b, team_b = locate_team(
        tournament_b, tournament_id=tournament_b_id, label=team_b_label
    )

    def team_identity(team: Mapping[str, Any], fallback_age_group: str) -> tuple[str, str, str]:
        return (
            str(team.get("club") or ""),
            str(team.get("label") or ""),
            str(team.get("age_group") or fallback_age_group),
        )

    identity_a = team_identity(team_a, age_group_a)
    identity_b = team_identity(team_b, age_group_b)
    if identity_a == identity_b:
        raise SeasonStateError("Participant swap would be a no-op")

    for index, existing in enumerate(tournament_a.get("teams", []) or []):
        if index != index_a and team_identity(existing, age_group_a) == identity_b:
            raise SeasonStateError(
                f"Cannot swap {team_b_label!r} into {tournament_a_id}: "
                "that team already participates there"
            )
    for index, existing in enumerate(tournament_b.get("teams", []) or []):
        if index != index_b and team_identity(existing, age_group_b) == identity_a:
            raise SeasonStateError(
                f"Cannot swap {team_a_label!r} into {tournament_b_id}: "
                "that team already participates there"
            )

    before_fingerprint_a = approval_fingerprint(tournament_a)
    before_fingerprint_b = approval_fingerprint(tournament_b)
    original_team_a = copy.deepcopy(team_a)
    original_team_b = copy.deepcopy(team_b)
    tournament_a["teams"][index_a] = copy.deepcopy(team_b)
    tournament_b["teams"][index_b] = copy.deepcopy(team_a)
    _regenerate_tournament_games(tournament_a, problem)
    _regenerate_tournament_games(tournament_b, problem)
    return {
        "tournament_a": tournament_a,
        "tournament_b": tournament_b,
        "team_a": original_team_a,
        "team_b": original_team_b,
        "index_a": index_a,
        "index_b": index_b,
        "identity_a": identity_a,
        "identity_b": identity_b,
        "age_group": age_group_a,
        "before_fingerprint_a": before_fingerprint_a,
        "before_fingerprint_b": before_fingerprint_b,
    }
