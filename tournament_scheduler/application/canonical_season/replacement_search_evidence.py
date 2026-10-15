"""Booked-reality evidence and ranking helpers for replacement search."""

from __future__ import annotations

import copy
from datetime import date
from typing import Any, Iterable, Mapping

from tournament_scheduler.booking.calendar_bookings import OPERATIONAL_BOOKED
from tournament_scheduler.participation.targets import (
    club_pool_snapshot,
    evaluate_participation,
    split_date_for,
)
from tournament_scheduler.season.request_constraints import team_identity


def eligible_incoming(
    problem: Mapping[str, Any],
    existing: set[tuple[str, str, str]],
    age_group: str,
    clubs: set[str],
    labels: set[str],
) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for team in problem.get("teams") or []:
        if (
            not isinstance(team, Mapping)
            or str(team.get("age_group") or "") != age_group
            or team.get("guest")
        ):
            continue
        identity = team_identity(team, age_group)
        if identity in existing or identity in seen:
            continue
        if (clubs and identity[0] not in clubs) or (labels and identity[1] not in labels):
            continue
        seen.add(identity)
        result.append(team)
    return result


def booked_projection(
    plan: Mapping[str, Any], booking_rows: Iterable[Mapping[str, Any]], age_group: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    states = {
        str(row.get("tournament_id") or ""): str(row.get("operational_state") or "")
        for row in booking_rows
    }
    relevant = [
        item
        for item in plan.get("tournaments", []) or []
        if not item.get("cancelled") and str(item.get("age_group") or "") == age_group
    ]
    booked_ids = {
        str(item.get("id") or "")
        for item in relevant
        if states.get(str(item.get("id") or "")) == OPERATIONAL_BOOKED
    }
    projected = copy.deepcopy(dict(plan))
    projected["tournaments"] = [
        copy.deepcopy(item)
        for item in relevant
        if str(item.get("id") or "") in booked_ids
    ]
    unresolved = sorted(
        str(item.get("id") or "")
        for item in relevant
        if states.get(str(item.get("id") or "")) not in {OPERATIONAL_BOOKED, "not_booked"}
    )
    return projected, {
        "semantics": "confirmed_booked_only",
        "booked_tournament_count": len(booked_ids),
        "potential_unconfirmed_tournament_count": len(relevant) - len(booked_ids),
        "provisional": bool(unresolved),
        "unresolved_tournament_ids": unresolved,
    }


def pool_row(
    plan: Mapping[str, Any], problem: Mapping[str, Any], club: str, age_group: str
) -> dict[str, Any]:
    rows = club_pool_snapshot(
        evaluate_participation(plan, problem), club_age_pairs={(club, age_group)}
    )
    season = next(
        (row for row in rows if row.get("scope") == "season"),
        rows[0] if rows else None,
    )
    if season is None:
        return {
            "club": club,
            "age_group": age_group,
            "registered_team_count": 0,
            "booked_participations": 0,
            "target_participations": 0,
            "normalized_shortfall": 0.0,
            "classification": "not_registered",
        }
    registered = int(season.get("registered_team_count") or 0)
    actual = int(season.get("club_pool_actual") or 0)
    target = int(season.get("club_pool_target") or 0)
    return {
        "club": club,
        "age_group": age_group,
        "registered_team_count": registered,
        "booked_participations": actual,
        "target_participations": target,
        "normalized_shortfall": round(max(0, target - actual) / max(1, registered), 6),
        "classification": season.get("classification"),
    }


def team_dates(plan: Mapping[str, Any], identity: tuple[str, str, str]) -> list[date]:
    result: list[date] = []
    for tournament in plan.get("tournaments", []) or []:
        if tournament.get("cancelled"):
            continue
        if not any(
            team_identity(team, str(tournament.get("age_group") or "")) == identity
            for team in tournament.get("teams", []) or []
        ):
            continue
        try:
            result.append(date.fromisoformat(str(tournament.get("date") or "")))
        except ValueError:
            continue
    return sorted(set(result))


def temporal_row(
    plan: Mapping[str, Any], problem: Mapping[str, Any], identity: tuple[str, str, str]
) -> dict[str, Any]:
    dates = team_dates(plan, identity)
    split = split_date_for(problem, plan.get("tournaments", []) or [])
    gaps = [(later - earlier).days for earlier, later in zip(dates, dates[1:])]
    try:
        season_start = date.fromisoformat(
            str(plan.get("start_date") or problem.get("start_date") or "")
        )
        season_end = date.fromisoformat(
            str(plan.get("end_date") or problem.get("end_date") or "")
        )
    except ValueError:
        season_start = season_end = None
    inactive_gaps = list(gaps)
    if dates and season_start is not None and season_end is not None:
        inactive_gaps.extend(
            [(dates[0] - season_start).days, (season_end - dates[-1]).days]
        )
    elif not dates and season_start is not None and season_end is not None:
        inactive_gaps.append((season_end - season_start).days)
    return {
        "booked_participations": len(dates),
        "before_christmas": sum(
            1 for item in dates if split is not None and item < split
        ),
        "after_christmas": sum(
            1 for item in dates if split is not None and item >= split
        ),
        "max_inactive_gap_days": max(inactive_gaps, default=None),
        "back_to_back_weekends": sum(1 for gap in gaps if gap == 7),
        "gaps_under_7_days": sum(1 for gap in gaps if gap < 7),
        "gaps_under_14_days": sum(1 for gap in gaps if gap < 14),
    }


def metric_delta(before: Mapping[str, Any], after: Mapping[str, Any], key: str) -> int:
    return int(after.get(key) or 0) - int(before.get(key) or 0)


def opponent_travel(
    consequence: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    before = consequence.get("before") or {}
    after = consequence.get("after") or {}
    before_distinct = int((before.get("club_opponents") or {}).get("distinct_clubs") or 0)
    after_distinct = int((after.get("club_opponents") or {}).get("distinct_clubs") or 0)
    before_travel = float(before.get("travel_km") or 0)
    after_travel = float(after.get("travel_km") or 0)
    regressions = consequence.get("material_regressions") or []
    return (
        {
            "distinct_clubs_before": before_distinct,
            "distinct_clubs_after": after_distinct,
            "distinct_clubs_delta": after_distinct - before_distinct,
            "material_regressions": [
                item
                for item in regressions
                if "opponent" in str(item.get("code") or "")
            ],
        },
        {
            "km_before": before_travel,
            "km_after": after_travel,
            "delta_km": after_travel - before_travel,
            "warnings": [
                item
                for item in (consequence.get("warnings") or []) + regressions
                if "travel" in str(item.get("code") or "")
            ],
        },
    )


def ranking_reason(candidate: Mapping[str, Any]) -> str:
    verdict = candidate["verdict"]
    if not verdict.get("applicable"):
        blockers = verdict.get("blockers") or ["canonical replacement gate"]
        return "Blocked by " + ", ".join(str(item) for item in blockers)
    fairness = candidate["club_age_booked_participation"]["added_club"]
    coverage = candidate["affected_team_booked_coverage"]
    temporal = coverage["added_team"]["after"]
    removed_before = coverage["removed_team"]["before"]
    removed_after = coverage["removed_team"]["after"]
    donor_half_losses = sum(
        int(removed_before[half] > 0 and removed_after[half] == 0)
        for half in ("before_christmas", "after_christmas")
    )
    reason = (
        f"Safe; added club has {fairness['before']['normalized_shortfall']:.2f} "
        f"booked opportunities per registered team below target"
    )
    if donor_half_losses:
        reason += f"; removal loses donor coverage in {donor_half_losses} season half"
    elif temporal["before_christmas"] and temporal["after_christmas"]:
        reason += "; preserves both season halves"
    if candidate["booking_evidence"]["provisional"]:
        reason += "; provisional because booking evidence is incomplete"
    return reason


def rank_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    fairness = candidate["club_age_booked_participation"]
    added, removed = fairness["added_club"], fairness["removed_club"]
    coverage = candidate["affected_team_booked_coverage"]
    after = coverage["added_team"]["after"]
    removed_before = coverage["removed_team"]["before"]
    removed_after = coverage["removed_team"]["after"]
    removed_half_losses = sum(
        int(removed_before[half] > 0 and removed_after[half] == 0)
        for half in ("before_christmas", "after_christmas")
    )
    removed_gap_worsening = max(
        0,
        int(removed_after.get("max_inactive_gap_days") or 0)
        - int(removed_before.get("max_inactive_gap_days") or 0),
    )
    spacing = candidate["spacing_clustering_delta"]
    opponent = candidate["opponent_exposure_delta"]
    travel = candidate["travel_delta"]
    preference = candidate["soft_preference_effects"]
    net_improvement = (
        float(added["before"]["normalized_shortfall"])
        + float(removed["before"]["normalized_shortfall"])
        - float(added["after"]["normalized_shortfall"])
        - float(removed["after"]["normalized_shortfall"])
    )
    missing_halves = int(after["before_christmas"] == 0) + int(
        after["after_christmas"] == 0
    )
    return (
        0 if candidate["verdict"].get("applicable") else 1,
        -float(added["before"]["normalized_shortfall"]),
        -net_improvement,
        removed_half_losses,
        removed_gap_worsening,
        missing_halves,
        int(after.get("max_inactive_gap_days") or 0),
        max(0, int(spacing["back_to_back_weekends"])),
        max(0, int(spacing["gaps_under_7_days"])),
        max(0, int(spacing["gaps_under_14_days"])),
        len(opponent.get("material_regressions") or []),
        -int(opponent.get("distinct_clubs_delta") or 0),
        len(travel.get("warnings") or []),
        max(0.0, float(travel.get("delta_km") or 0)),
        max(0, int(preference.get("host_sibling_violations_delta") or 0)),
        str(candidate["remove_team"]["club"]),
        str(candidate["remove_team"]["label"]),
        str(candidate["add_team"]["club"]),
        str(candidate["add_team"]["label"]),
    )
