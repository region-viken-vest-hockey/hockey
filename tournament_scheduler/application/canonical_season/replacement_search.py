"""Read-only participant-replacement discovery and lexicographic ranking.

Composes canonical capabilities without writing or applying a result.
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any, Iterable, Mapping

from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.participation.team_schedule_quality import (
    compare_team_schedule_consequence,
)
from tournament_scheduler.season.request_constraints import (
    count_host_sibling_preference_violations,
    team_identity,
)
from tournament_scheduler.season.state import canonical_state_revision

from .replacement import _apply_replace_participant_to_plan, replace_participant
from .replacement_refusals import ReplacementRefusal
from .replacement_search_pool import effective_spacing_delta
from .replacement_search_evidence import (
    booked_projection,
    eligible_incoming,
    metric_delta,
    opponent_travel,
    pool_row,
    rank_key,
    ranking_reason,
    team_dates,
    temporal_row,
)
from .shared import _resolve_plan_problem


def _find_tournament(plan: Mapping[str, Any], tournament_id: str) -> dict[str, Any]:
    tournament = next(
        (
            item
            for item in plan.get("tournaments", []) or []
            if str(item.get("id") or "") == tournament_id
        ),
        None,
    )
    if tournament is None:
        raise SeasonStateError(
            f"Unknown tournament id in canonical schedule: {tournament_id}"
        )
    if tournament.get("cancelled"):
        raise SeasonStateError(f"Tournament {tournament_id} is cancelled")
    return tournament


def _candidate_plan(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    tournament_id: str,
    remove: str,
    add: str,
) -> dict[str, Any]:
    candidate = copy.deepcopy(dict(plan))
    _apply_replace_participant_to_plan(
        candidate,
        tournament_id=tournament_id,
        remove_team_label=remove,
        add_team_label=add,
        problem=problem,
    )
    return candidate


def search_participant_replacements(
    service,
    *,
    season: str,
    tournament_id: str,
    replace_team_label: str | None = None,
    eligible_clubs: Iterable[str] | None = None,
    eligible_team_labels: Iterable[str] | None = None,
    legal_only: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return ranked, read-only one-tournament participant substitutions."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule.get("plan") or {}
    tournament = _find_tournament(plan, tournament_id)
    age_group = str(tournament.get("age_group") or "")
    problem = _resolve_plan_problem(schedule, None, decisions)
    if not problem or not (problem.get("teams") or []):
        raise SeasonStateError(
            "Replacement search requires the promoted verification context's registered team pool"
        )
    removable = [
        team
        for team in tournament.get("teams", []) or []
        if not bool((team or {}).get("guest", False))
        and (
            replace_team_label is None
            or str(team.get("label") or "") == replace_team_label
        )
    ]
    if replace_team_label is not None and not removable:
        raise SeasonStateError(
            f"Team {replace_team_label!r} is not a removable participant in tournament {tournament_id}"
        )
    existing = {
        team_identity(team, age_group) for team in tournament.get("teams", []) or []
    }
    clubs = {str(item) for item in (eligible_clubs or [])}
    labels = {str(item) for item in (eligible_team_labels or [])}
    incoming = eligible_incoming(problem, existing, age_group, clubs, labels)

    from .calendars import booking_status_report

    booking_report = booking_status_report(service, season=season, problem=dict(problem))
    booked_before, booking_evidence = booked_projection(
        plan, booking_report.get("tournaments") or [], age_group
    )
    target_is_booked = any(
        str(item.get("id") or "") == tournament_id
        for item in booked_before.get("tournaments", []) or []
    )
    booking_evidence["target_tournament_booked"] = target_is_booked
    if not target_is_booked:
        booking_evidence.update(
            provisional=True, target_excluded_reason="not_confirmed_booked"
        )

    preferences_before = count_host_sibling_preference_violations(plan, decisions)
    candidates: list[dict[str, Any]] = []
    for removed in removable:
        removed_identity = team_identity(removed, age_group)
        for added in incoming:
            added_identity = team_identity(added, age_group)
            try:
                preview = replace_participant(
                    service,
                    season=season,
                    tournament_id=tournament_id,
                    remove_team_label=removed_identity[1],
                    add_team_label=added_identity[1],
                    dry_run=True,
                )
                candidate_plan = _candidate_plan(
                    plan, problem, tournament_id, removed_identity[1], added_identity[1]
                )
                booked_after = (
                    _candidate_plan(
                        booked_before,
                        problem,
                        tournament_id,
                        removed_identity[1],
                        added_identity[1],
                    )
                    if target_is_booked
                    else copy.deepcopy(booked_before)
                )
            except ReplacementRefusal as exc:
                preview = {
                    "verdict": {"status": "blocked", "applicable": False, "checks": {}, "blockers": [str(exc)]},
                    "replacement": {"refusals": [exc.record()]},
                }
                candidate_plan, booked_after = copy.deepcopy(plan), copy.deepcopy(booked_before)

            added_before = temporal_row(booked_before, problem, added_identity)
            added_after = temporal_row(booked_after, problem, added_identity)
            consequence = compare_team_schedule_consequence(
                booked_before, booked_after, added_identity, problem=problem
            )
            opponent, travel = opponent_travel(consequence)
            exact_spacing_delta = {
                key: metric_delta(added_before, added_after, key)
                for key in (
                    "back_to_back_weekends",
                    "gaps_under_7_days",
                    "gaps_under_14_days",
                )
            }
            spacing_delta, pool_consequence = effective_spacing_delta(
                consequence, exact_spacing_delta
            )
            details = preview.get("replacement") or {}
            planned_dates = team_dates(plan, added_identity)
            try:
                target_date = date.fromisoformat(str(tournament.get("date") or ""))
            except ValueError:
                target_date = None
            result = {
                "club": added_identity[0],
                "label": added_identity[1],
                "age_group": age_group,
                "season_participations": len(planned_dates),
                "plays_on_tournament_date": target_date in planned_dates,
                "same_date_tournament_ids": sorted(
                    str(item.get("id") or "")
                    for item in plan.get("tournaments", []) or []
                    if str(item.get("date") or "") == str(tournament.get("date") or "")
                    and any(
                        team_identity(team, str(item.get("age_group") or ""))
                        == added_identity
                        for team in item.get("teams", []) or []
                    )
                ),
                "remove_team": dict(zip(("club", "label", "age_group"), removed_identity)),
                "add_team": dict(zip(("club", "label", "age_group"), added_identity)),
                "verdict": preview["verdict"],
                "safe_to_apply": bool(preview["verdict"].get("applicable")),
                "blockers": list(preview["verdict"].get("blockers") or []),
                "refusals": [dict(item) for item in details.get("refusals") or []],
                "booking_evidence": dict(booking_evidence),
                "club_age_booked_participation": {
                    "removed_club": {
                        "before": pool_row(booked_before, problem, removed_identity[0], age_group),
                        "after": pool_row(booked_after, problem, removed_identity[0], age_group),
                    },
                    "added_club": {
                        "before": pool_row(booked_before, problem, added_identity[0], age_group),
                        "after": pool_row(booked_after, problem, added_identity[0], age_group),
                    },
                },
                "affected_team_booked_coverage": {
                    "removed_team": {
                        "before": temporal_row(booked_before, problem, removed_identity),
                        "after": temporal_row(booked_after, problem, removed_identity),
                    },
                    "added_team": {"before": added_before, "after": added_after},
                },
                "spacing_clustering_delta": spacing_delta,
                "spacing_clustering_scope": (
                    "rotatable_participant_pool" if pool_consequence else "exact_team"
                ),
                "exact_team_spacing_clustering_delta": exact_spacing_delta,
                "participant_pool_consequence": pool_consequence,
                "opponent_exposure_delta": opponent,
                "travel_delta": travel,
                "active_protection_request_effects": {
                    "change_protection_acceptable": details.get("change_protection_acceptable"),
                    "change_protection_violations": details.get("existing_change_protection_violations") or [],
                    "request_constraints_acceptable": details.get("request_constraint_acceptable"),
                    "request_constraint_regressions": details.get("request_constraint_regressions") or [],
                },
                "soft_preference_effects": {
                    "host_sibling_violations_before": preferences_before,
                    "host_sibling_violations_after": count_host_sibling_preference_violations(candidate_plan, decisions),
                },
            }
            soft = result["soft_preference_effects"]
            soft["host_sibling_violations_delta"] = (
                soft["host_sibling_violations_after"] - preferences_before
            )
            result["ranking_reason"] = ranking_reason(result)
            result["_rank_key"] = rank_key(result)
            candidates.append(result)

    candidates.sort(key=lambda item: item["_rank_key"])
    if legal_only:
        candidates = [item for item in candidates if item["safe_to_apply"]]
    if limit is not None and limit >= 0:
        candidates = candidates[:limit]
    for rank, candidate in enumerate(candidates, 1):
        candidate.pop("_rank_key", None)
        candidate["rank"] = rank
    best = next((item for item in candidates if item["safe_to_apply"]), None)
    return {
        "season": season,
        "revision": schedule.get("revision"),
        "canonical_state_revision": canonical_state_revision(schedule, decisions),
        "tournament_id": tournament_id,
        "age_group": age_group,
        "date": tournament.get("date"),
        "replace_team": replace_team_label,
        "validated": True,
        "filters": {"eligible_clubs": sorted(clubs), "eligible_team_labels": sorted(labels)},
        "booking_evidence": booking_evidence,
        "ranking_policy": "lexicographic: legality, donor half-season losses, booked club-age fairness, recipient half-season coverage, team continuity, sibling distribution, cadence/clustering, opposing-club exposure and travel",
        "candidate_count": len(candidates),
        "best_candidate": (
            {
                "rank": best["rank"],
                "remove_team": best["remove_team"],
                "add_team": best["add_team"],
                "ranking_reason": best["ranking_reason"],
            }
            if best
            else None
        ),
        "candidates": candidates,
        "apply_command": "season replace-participant requires an explicit request id and is never invoked by this search",
    }


__all__ = ["search_participant_replacements"]
