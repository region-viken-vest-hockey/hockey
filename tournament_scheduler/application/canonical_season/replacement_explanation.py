"""Read-only ineligibility explanation for one proposed participant replacement.

Each exclusion is tied to the canonical owner that decides it: roster and
registered-pool preconditions, search filters, or the replacement dry-run gates.
A legal pair is then compared with the bounded pool for the same removed team.
Nothing here writes, and the result never claims exhaustive infeasibility.
"""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.season.request_constraints import team_identity
from tournament_scheduler.season.state import canonical_state_revision

from .replacement import replace_participant
from .replacement_refusals import ReplacementRefusal
from .replacement_comparison import (
    CLASS_HARD_INELIGIBLE,
    CLASS_OUT_OF_SCOPE,
    compare_candidates,
    exclusion_reason,
    gate_reasons,
    pair_key,
)
from .replacement_search import _find_tournament, search_participant_replacements
from .replacement_search_evidence import eligible_incoming
from .shared import _resolve_plan_problem

EXPLANATION_CONTRACT = "replacement-ineligibility-explanation/1"

STATUS_LEGAL_PARETO_CHOICE = "legal_pareto_choice"
STATUS_LEGAL_DOMINATED = "legal_dominated"


def explain_replacement_ineligibility(
    service,
    *,
    season: str,
    tournament_id: str,
    remove_team_label: str,
    add_team_label: str,
    eligible_clubs: list[str] | None = None,
    eligible_team_labels: list[str] | None = None,
) -> dict[str, Any]:
    """Explain why one proposed participant replacement is or is not available.

    Preconditions are checked first and reported as out-of-scope or hard
    ineligible. Otherwise the proposed pair goes through the replacement dry-run
    gates, and each failed gate is reported with its canonical owner. A legal
    pair is then compared with the bounded pool for the same removed team to
    show whether another searched candidate dominates it.
    """

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule.get("plan") or {}
    tournament = _find_tournament(plan, tournament_id)
    age_group = str(tournament.get("age_group") or "")
    problem = _resolve_plan_problem(schedule, None, decisions)
    registered = [
        team
        for team in (problem or {}).get("teams") or []
        if isinstance(team, Mapping)
        and str(team.get("age_group") or "") == age_group
        and not team.get("guest")
    ]
    participants = {
        team_identity(team, age_group)[1]: team_identity(team, age_group)
        for team in tournament.get("teams", []) or []
        if not bool((team or {}).get("guest", False))
    }
    existing = set(participants.values())
    clubs = {str(item) for item in (eligible_clubs or [])}
    labels = {str(item) for item in (eligible_team_labels or [])}

    reasons: list[dict[str, Any]] = []
    if remove_team_label not in participants:
        reasons.append(
            exclusion_reason(
                "not_a_removable_participant",
                CLASS_OUT_OF_SCOPE,
                "canonical_season.tournament_roster",
                [remove_team_label],
            )
        )

    add_registered = [team for team in registered if str(team.get("label") or "") == add_team_label]
    if not add_registered:
        reasons.append(
            exclusion_reason(
                "not_a_registered_same_age_team",
                CLASS_HARD_INELIGIBLE,
                "canonical_season.registered_team_pool",
                [add_team_label],
            )
        )
    else:
        add_identity = team_identity(add_registered[0], age_group)
        if add_identity in existing:
            reasons.append(
                exclusion_reason(
                    "already_participant",
                    CLASS_HARD_INELIGIBLE,
                    "canonical_season.tournament_roster",
                    [add_team_label],
                )
            )
        elif add_identity not in {
            team_identity(team, age_group)
            for team in eligible_incoming(problem or {}, existing, age_group, set(), set())
        }:
            reasons.append(
                exclusion_reason(
                    "not_eligible_incoming",
                    CLASS_HARD_INELIGIBLE,
                    "canonical_season.registered_team_pool",
                    [add_team_label],
                )
            )
        elif (clubs or labels) and add_identity not in {
            team_identity(team, age_group)
            for team in eligible_incoming(problem or {}, existing, age_group, clubs, labels)
        }:
            reasons.append(
                exclusion_reason(
                    "outside_search_filters",
                    CLASS_OUT_OF_SCOPE,
                    "canonical_season.replacement_search_filters",
                    [add_team_label],
                )
            )

    verdict: dict[str, Any] | None = None
    if not reasons:
        try:
            preview = replace_participant(
                service,
                season=season,
                tournament_id=tournament_id,
                remove_team_label=remove_team_label,
                add_team_label=add_team_label,
                dry_run=True,
            )
        except ReplacementRefusal as exc:
            record = exc.record()
            reasons.append(
                exclusion_reason(
                    record["code"],
                    CLASS_HARD_INELIGIBLE,
                    record["owner"],
                    record["evidence"],
                    message=record["message"],
                )
            )
        else:
            verdict = preview["verdict"]
            if not verdict.get("applicable"):
                details = preview.get("replacement") or {}
                evidence = {
                    "change_protection_violations": details.get("existing_change_protection_violations") or [],
                    "request_constraint_regressions": details.get("request_constraint_regressions") or [],
                }
                reasons.extend(
                    gate_reasons(
                        verdict.get("blockers") or [],
                        details.get("refusals"),
                        evidence,
                    )
                )

    booking_evidence: dict[str, Any] | None = None
    dominated_by: list[str] = []
    tied_with: list[str] = []
    if not reasons and verdict is not None:
        pool = search_participant_replacements(
            service,
            season=season,
            tournament_id=tournament_id,
            replace_team_label=remove_team_label,
            eligible_clubs=eligible_clubs,
            eligible_team_labels=eligible_team_labels,
        )
        booking_evidence = pool["booking_evidence"]
        comparison = compare_candidates(pool["candidates"])
        pair = f"{remove_team_label}->{add_team_label}"
        option = next(
            (item for item in comparison["options"] if pair_key(item) == pair),
            None,
        )
        if option is None:
            reasons.append(
                exclusion_reason(
                    "not_in_searched_pool",
                    CLASS_OUT_OF_SCOPE,
                    "canonical_season.replacement_search_filters",
                    [pair],
                )
            )
        else:
            dominated_by = option["dominated_by"]
            tied_with = option["tied_on_objectives_with"]

    if any(item["classification"] == CLASS_HARD_INELIGIBLE for item in reasons):
        status = CLASS_HARD_INELIGIBLE
    elif reasons:
        status = CLASS_OUT_OF_SCOPE
    elif dominated_by:
        status = STATUS_LEGAL_DOMINATED
    else:
        status = STATUS_LEGAL_PARETO_CHOICE

    return {
        "contract": EXPLANATION_CONTRACT,
        "season": season,
        "canonical_state_revision": canonical_state_revision(schedule, decisions),
        "tournament_id": tournament_id,
        "remove_team": remove_team_label,
        "add_team": add_team_label,
        "status": status,
        "reasons": reasons,
        "dominated_by": dominated_by,
        "tied_on_objectives_with": tied_with,
        "evidence_status": (
            "provisional"
            if booking_evidence and booking_evidence.get("provisional")
            else "complete"
        ),
        "booking_evidence": booking_evidence,
        "scope": {
            "pair": "single proposed replacement pair",
            "dominance_pool": "replacement candidates for the removed team under the given filters",
            "exhaustive": False,
        },
        "read_only": True,
    }


__all__ = ["EXPLANATION_CONTRACT", "explain_replacement_ineligibility"]
