"""Per-team consequences of a repair candidate, from its before and after plans.

A repair changes a few tournaments. Every team that plays in a changed tournament,
before or after the repair, is an affected team. Each one is evaluated with the same
team-schedule comparison the batch and replacement dry-runs use, so a repair cannot
hide a serious regression for one team behind a better season-wide aggregate.

This module reads plans only; it writes nothing and owns no gate.
"""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.participation.team_schedule_quality import compare_team_schedule_consequence

from .booked_fairness import impacted_booked_fairness
from .replacement import _affected_participation_counts


def repair_team_consequences(
    before_plan: Mapping[str, Any],
    after_plan: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
    decisions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return planned and booked consequences for the teams a repair changes.

    ``booked_fairness`` is the batch owner's booked-reality projection, run against
    the before and after plans with the same canonical decisions. A repair that
    moves a confirmed tournament therefore stops counting it as booked.
    """

    identities = _affected_identities(before_plan, after_plan)
    if not identities:
        return {"participation_counts": {}, "team_consequences": {}, "booked_fairness": None}
    changed_ids = sorted(_changed_tournament_ids(before_plan, after_plan))
    return {
        "participation_counts": _affected_participation_counts(
            before_plan, after_plan, tuple(identities), problem
        ),
        "team_consequences": {
            ":".join(identity): compare_team_schedule_consequence(
                before_plan, after_plan, identity, problem=problem
            )
            for identity in identities
        },
        "booked_fairness": impacted_booked_fairness(
            before_plan=before_plan,
            candidate_plan=after_plan,
            before_decisions=decisions,
            after_decisions=decisions,
            problem=problem,
            changed_ids=changed_ids,
        ),
    }


def _changed_tournament_ids(before_plan: Mapping[str, Any], after_plan: Mapping[str, Any]) -> set[str]:
    before = _tournaments_by_id(before_plan)
    after = _tournaments_by_id(after_plan)
    return {
        tournament_id
        for tournament_id in set(before) | set(after)
        if before.get(tournament_id) != after.get(tournament_id)
    }


def _affected_identities(
    before_plan: Mapping[str, Any], after_plan: Mapping[str, Any]
) -> list[tuple[str, str, str]]:
    before = _tournaments_by_id(before_plan)
    after = _tournaments_by_id(after_plan)
    identities: set[tuple[str, str, str]] = set()
    for tournament_id in _changed_tournament_ids(before_plan, after_plan):
        for source in (before, after):
            tournament = source.get(tournament_id)
            if tournament is None:
                continue
            fallback_age_group = str(tournament.get("age_group") or "")
            for team in tournament.get("teams", []) or []:
                if bool((team or {}).get("guest", False)):
                    continue
                identities.add(
                    (
                        str((team or {}).get("club") or ""),
                        str((team or {}).get("label") or ""),
                        str((team or {}).get("age_group") or fallback_age_group),
                    )
                )
    return sorted(identities)


def _tournaments_by_id(plan: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    # Cancelled records are kept: a repair that cancels or revives a tournament
    # changes its teams' participation, and the counts helper already skips
    # cancelled tournaments when it measures that participation.
    return {
        str(tournament["id"]): tournament
        for tournament in plan.get("tournaments", []) or []
        if isinstance(tournament, Mapping)
    }
