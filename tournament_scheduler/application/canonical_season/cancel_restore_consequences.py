"""Participation consequences of the cancel/restore operations in a batch.

A cancellation removes one appearance from every non-guest participant of the
cancelled tournament and a restoration gives it back. This module owns the
appearance counting, the attribution of a ``participation_count_changed``
regression to exactly those appearances, and the per-team consequence analysis
of restored tournaments.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from tournament_scheduler.participation.withdrawals import team_identity


def _cancelled_appearances(
    plan: Mapping[str, Any],
    tournament_ids: list[str],
) -> Counter[tuple[str, str, str]]:
    """Count each non-guest team's appearances in the named cancellations.

    A team may equally be a deliberate batch removal (withdrawn from the whole
    age group); that identity is tracked separately so its own shortfall stays
    exempt. This count is the only participation loss a cancellation acceptance
    may waive for a retained team.
    """

    wanted = {str(tournament_id) for tournament_id in tournament_ids}
    appearances: Counter[tuple[str, str, str]] = Counter()
    for tournament in plan.get("tournaments", []) or []:
        if str(tournament.get("id") or "") not in wanted:
            continue
        age_group = str(tournament.get("age_group") or "")
        for team in tournament.get("teams", []) or []:
            if bool(team.get("guest", False)):
                continue
            appearances[
                (
                    str(team.get("club") or ""),
                    str(team.get("label") or ""),
                    str(team.get("age_group") or age_group),
                )
            ] += 1
    return appearances


def _participation_acceptance_scope(
    team_consequences: Mapping[str, Mapping[str, Any]],
    cancelled_appearances: Mapping[tuple[str, str, str], int],
    restored_appearances: Mapping[tuple[str, str, str], int] | None = None,
) -> set[tuple[str, str, str]]:
    """Return the identities whose whole count delta the batch's cancel/restore explains.

    ``participation_count_changed`` is only attributable when the team's
    before->final tournament-count change equals the net number of appearances
    it lost to the batch's cancelled tournaments and regained from its restored
    tournaments. A team whose count changed by any other amount is excluded so
    its combined change cannot be waived by a single cancellation/restoration
    acceptance.
    """

    from tournament_scheduler.participation.team_schedule_quality import (
        PARTICIPATION_COUNT_CHANGED,
        consequence_team_identity,
    )

    eligible: set[tuple[str, str, str]] = set()
    for analysis in team_consequences.values():
        regression = next(
            (
                item
                for item in analysis.get("material_regressions") or []
                if item.get("code") == PARTICIPATION_COUNT_CHANGED
            ),
            None,
        )
        if regression is None:
            continue
        identity = consequence_team_identity(analysis)
        delta = int(regression.get("after") or 0) - int(regression.get("before") or 0)
        expected = (restored_appearances or {}).get(identity, 0) - cancelled_appearances.get(
            identity, 0
        )
        if delta != 0 and expected == delta:
            eligible.add(identity)
    return eligible


def restoration_team_consequences(
    before_plan: Mapping[str, Any],
    candidate_plan: Mapping[str, Any],
    restored_ids: list[str],
    *,
    problem: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the before->final consequence of each restored tournament's teams.

    A restoration adds an appearance for every non-guest participant; each such
    team gets the same analysis as a swap so a restored tournament cannot
    silently worsen a team's schedule.
    """

    from tournament_scheduler.participation.team_schedule_quality import (
        compare_team_schedule_consequence,
    )

    wanted = set(restored_ids)
    consequences: dict[str, Any] = {}
    for tournament in candidate_plan.get("tournaments", []) or []:
        if str(tournament.get("id") or "") not in wanted:
            continue
        for team in tournament.get("teams", []) or []:
            if team.get("guest"):
                continue
            identity = team_identity(team, str(tournament.get("age_group") or ""))
            key = "|".join(str(part) for part in identity)
            if key in consequences:
                continue
            consequences[key] = compare_team_schedule_consequence(
                before_plan, candidate_plan, tuple(identity), problem=problem
            )
    return consequences
