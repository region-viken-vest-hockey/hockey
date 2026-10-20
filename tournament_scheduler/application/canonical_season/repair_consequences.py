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

from .replacement import _affected_participation_counts


def repair_team_consequences(
    before_plan: Mapping[str, Any],
    after_plan: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return ``participation_counts`` and ``team_consequences`` for the changed teams."""

    identities = _affected_identities(before_plan, after_plan)
    if not identities:
        return {"participation_counts": {}, "team_consequences": {}}
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
    }


def _affected_identities(
    before_plan: Mapping[str, Any], after_plan: Mapping[str, Any]
) -> list[tuple[str, str, str]]:
    before = _tournaments_by_id(before_plan)
    after = _tournaments_by_id(after_plan)
    changed_ids = {
        tournament_id
        for tournament_id in set(before) | set(after)
        if before.get(tournament_id) != after.get(tournament_id)
    }
    identities: set[tuple[str, str, str]] = set()
    for tournament_id in changed_ids:
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
    return {
        str(tournament["id"]): tournament
        for tournament in plan.get("tournaments", []) or []
        if isinstance(tournament, Mapping) and not tournament.get("cancelled")
    }
