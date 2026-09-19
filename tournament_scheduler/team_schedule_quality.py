"""Per-team consequence analysis for targeted canonical roster changes.

Season-wide quality metrics are useful for plan selection, but a one-for-one
roster swap can improve the aggregate while making the displaced team's own
schedule materially worse. This module therefore compares the exact two team
identities affected by a targeted swap.

The thresholds here are policy-level definitions of "material", deliberately
kept deterministic and conservative:
- creating an additional gap under 7 or 14 days is material;
- worsening temporal coverage into the normal >60-day offender range is material;
- increasing repeated-opponent excess beyond two meetings is material;
- increasing estimated season travel by at least 50 km and 25% is material.

Small neutral trade-offs remain visible in the before/after profiles but do not
block a swap.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any, Mapping

from tournament_scheduler import planning_half
from tournament_scheduler.club_distances import arena_to_club, distance
from tournament_scheduler.participation_targets import (
    HALVES,
    resolve_half_target,
    resolve_hard_max,
    resolve_season_target,
    split_date_for,
)
from tournament_scheduler.temporal_coverage import (
    DEFAULT_TEMPORAL_COVERAGE_THRESHOLD_DAYS,
    team_temporal_coverage,
)

TeamIdentity = tuple[str, str, str]

CLOSE_GAP_DAYS = 7
PREFERRED_GAP_DAYS = 14
MATERIAL_TEMPORAL_GAP_DELTA_DAYS = 14
MATERIAL_TRAVEL_INCREASE_KM = 50
MATERIAL_TRAVEL_INCREASE_RATIO = 0.25


def _identity(team: Mapping[str, Any], fallback_age_group: str = "") -> TeamIdentity:
    return (
        str(team.get("club") or ""),
        str(team.get("label") or ""),
        str(team.get("age_group") or fallback_age_group),
    )


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def team_schedule_profile(
    plan: Mapping[str, Any],
    identity: TeamIdentity,
    *,
    problem: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return deterministic schedule-quality facts for one exact team."""

    club, label, age_group = identity
    dates: list[date] = []
    opponents: Counter[TeamIdentity] = Counter()
    travel_km = 0

    for tournament in plan.get("tournaments", []) or []:
        if tournament.get("cancelled"):
            continue
        tournament_age_group = str(tournament.get("age_group") or "")
        participant_identities = [
            _identity(team, tournament_age_group)
            for team in tournament.get("teams", []) or []
            if not bool(team.get("guest", False))
        ]
        if identity not in participant_identities:
            continue

        tournament_date = _parse_date(tournament.get("date"))
        if tournament_date is not None:
            dates.append(tournament_date)

        for opponent in participant_identities:
            if opponent != identity:
                opponents[opponent] += 1

        host_club = arena_to_club(str(tournament.get("arena") or ""))
        if host_club is None:
            host_club = str(tournament.get("host_club") or "") or None
        if host_club and host_club != club:
            travel_km += distance(club, host_club)

    dates = sorted(dates)
    gaps = [(nxt - prev).days for prev, nxt in zip(dates, dates[1:])]
    min_gap = min(gaps) if gaps else None
    gaps_under_7 = sum(1 for gap in gaps if gap < CLOSE_GAP_DAYS)
    gaps_under_14 = sum(1 for gap in gaps if gap < PREFERRED_GAP_DAYS)

    temporal = {
        "lead_gap_days": None,
        "finish_gap_days": None,
        "max_intra_gap_days": max(gaps) if gaps else 0,
        "max_gap_days": max(gaps) if gaps else 0,
    }
    if problem is not None:
        season_start = _parse_date(problem.get("start_date"))
        season_end = _parse_date(problem.get("end_date"))
        if season_start is not None and season_end is not None:
            split = _parse_date(problem.get("christmas_split_date"))
            active_start, active_end = planning_half.active_window_for_age_group(
                age_group,
                season_start,
                season_end,
                split,
                problem.get("participation_targets_by_age_group"),
            )
            coverage = team_temporal_coverage(
                identity,
                club,
                age_group,
                active_start,
                active_end,
                dates,
            )
            temporal = {
                "lead_gap_days": coverage.lead_gap_days,
                "finish_gap_days": coverage.finish_gap_days,
                "max_intra_gap_days": coverage.max_intra_gap_days,
                "max_gap_days": coverage.max_gap_days,
            }

    opponent_counts = sorted(
        (
            {
                "club": opponent[0],
                "label": opponent[1],
                "age_group": opponent[2],
                "meetings": count,
            }
            for opponent, count in opponents.items()
        ),
        key=lambda entry: (-entry["meetings"], entry["club"], entry["label"]),
    )
    max_repeat = max(opponents.values(), default=0)
    repeat_excess_over_2 = sum(max(0, count - 2) for count in opponents.values())

    return {
        "team": {"club": club, "label": label, "age_group": age_group},
        "tournament_count": len(dates),
        "tournament_dates": [value.isoformat() for value in dates],
        "spacing": {
            "min_gap_days": min_gap,
            "gaps_under_7": gaps_under_7,
            "gaps_under_14": gaps_under_14,
        },
        "temporal": temporal,
        "opponents": {
            "unique_opponents": len(opponents),
            "max_repeat": max_repeat,
            "repeat_excess_over_2": repeat_excess_over_2,
            "counts": opponent_counts,
        },
        "travel_km": travel_km,
    }


def compare_team_schedule_profiles(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> dict[str, Any]:
    """Classify material regressions between two profiles for the same team."""

    material: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if int(after.get("tournament_count", 0)) != int(before.get("tournament_count", 0)):
        material.append(
            {
                "code": "participation_count_changed",
                "before": before.get("tournament_count"),
                "after": after.get("tournament_count"),
            }
        )

    before_spacing = before.get("spacing") or {}
    after_spacing = after.get("spacing") or {}
    for key, code in (
        ("gaps_under_7", "more_gaps_under_7_days"),
        ("gaps_under_14", "more_gaps_under_14_days"),
    ):
        old = int(before_spacing.get(key, 0) or 0)
        new = int(after_spacing.get(key, 0) or 0)
        if new > old:
            material.append({"code": code, "before": old, "after": new, "delta": new - old})

    before_temporal = before.get("temporal") or {}
    after_temporal = after.get("temporal") or {}
    old_max_gap = int(before_temporal.get("max_gap_days", 0) or 0)
    new_max_gap = int(after_temporal.get("max_gap_days", 0) or 0)
    temporal_delta = new_max_gap - old_max_gap
    if (
        new_max_gap > DEFAULT_TEMPORAL_COVERAGE_THRESHOLD_DAYS
        and temporal_delta >= MATERIAL_TEMPORAL_GAP_DELTA_DAYS
    ):
        material.append(
            {
                "code": "temporal_coverage_materially_worse",
                "before": old_max_gap,
                "after": new_max_gap,
                "delta": temporal_delta,
                "threshold_days": DEFAULT_TEMPORAL_COVERAGE_THRESHOLD_DAYS,
            }
        )
    elif temporal_delta > 0:
        warnings.append(
            {
                "code": "temporal_coverage_worse",
                "before": old_max_gap,
                "after": new_max_gap,
                "delta": temporal_delta,
            }
        )

    before_opponents = before.get("opponents") or {}
    after_opponents = after.get("opponents") or {}
    old_repeat_excess = int(before_opponents.get("repeat_excess_over_2", 0) or 0)
    new_repeat_excess = int(after_opponents.get("repeat_excess_over_2", 0) or 0)
    if new_repeat_excess > old_repeat_excess:
        material.append(
            {
                "code": "more_repeated_opponent_excess",
                "before": old_repeat_excess,
                "after": new_repeat_excess,
                "delta": new_repeat_excess - old_repeat_excess,
            }
        )
    old_unique = int(before_opponents.get("unique_opponents", 0) or 0)
    new_unique = int(after_opponents.get("unique_opponents", 0) or 0)
    if new_unique < old_unique:
        warnings.append(
            {
                "code": "fewer_unique_opponents",
                "before": old_unique,
                "after": new_unique,
                "delta": new_unique - old_unique,
            }
        )

    old_travel = int(before.get("travel_km", 0) or 0)
    new_travel = int(after.get("travel_km", 0) or 0)
    travel_delta = new_travel - old_travel
    ratio = (travel_delta / old_travel) if old_travel > 0 else None
    if travel_delta >= MATERIAL_TRAVEL_INCREASE_KM and (
        old_travel == 0 or ratio is not None and ratio >= MATERIAL_TRAVEL_INCREASE_RATIO
    ):
        material.append(
            {
                "code": "travel_materially_worse",
                "before_km": old_travel,
                "after_km": new_travel,
                "delta_km": travel_delta,
                "relative_increase": ratio,
                "absolute_threshold_km": MATERIAL_TRAVEL_INCREASE_KM,
                "relative_threshold": MATERIAL_TRAVEL_INCREASE_RATIO,
            }
        )
    elif travel_delta > 0:
        warnings.append(
            {
                "code": "travel_worse",
                "before_km": old_travel,
                "after_km": new_travel,
                "delta_km": travel_delta,
                "relative_increase": ratio,
            }
        )

    return {
        "acceptable": not material,
        "material_regressions": material,
        "warnings": warnings,
        "before": dict(before),
        "after": dict(after),
    }


def compare_team_schedule_consequence(
    before_plan: Mapping[str, Any],
    after_plan: Mapping[str, Any],
    identity: TeamIdentity,
    *,
    problem: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and compare one team's before/after targeted-change profiles."""

    before = team_schedule_profile(before_plan, identity, problem=problem)
    after = team_schedule_profile(after_plan, identity, problem=problem)
    return compare_team_schedule_profiles(before, after)


def _half_counts(dates: list[str], split: date | None) -> dict[str, int]:
    counts = {half: 0 for half in HALVES}
    for value in dates:
        parsed = _parse_date(value)
        if parsed is None:
            continue
        half = planning_half.tournament_half(parsed, split)
        if half in HALVES:
            counts[half] += 1
    return counts


def team_participation_effect(
    identity: TeamIdentity,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    problem: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Explicit per-half participation effect of a roster-membership change.

    A roster repair deliberately adds or removes a team, so a raw
    ``tournament_count`` change is not by itself a material regression. This
    resolves the canonical targets/hard maximum and reports the shortfall the
    membership change causes (or fails to cause), so the participation effect
    is evaluated the same way the planner's targets are.
    """

    split = split_date_for(problem)
    before_dates = [str(item) for item in (before.get("tournament_dates") or [])]
    after_dates = [str(item) for item in (after.get("tournament_dates") or [])]
    before_half = _half_counts(before_dates, split)
    after_half = _half_counts(after_dates, split)
    half_targets = {half: resolve_half_target(identity, problem, half) for half in HALVES}

    def shortfall(counts: Mapping[str, int]) -> int:
        total = 0
        for half in HALVES:
            target = half_targets.get(half)
            if isinstance(target, int):
                total += max(0, target - int(counts.get(half, 0) or 0))
        return total

    return {
        "before_count": len(before_dates),
        "after_count": len(after_dates),
        "before_half_counts": before_half,
        "after_half_counts": after_half,
        "half_targets": half_targets,
        "season_target": resolve_season_target(identity, problem),
        "hard_max": resolve_hard_max(identity, problem),
        "before_shortfall": shortfall(before_half),
        "after_shortfall": shortfall(after_half),
    }


def compare_changed_team_schedule_consequence(
    before_plan: Mapping[str, Any],
    after_plan: Mapping[str, Any],
    identity: TeamIdentity,
    *,
    problem: Mapping[str, Any] | None = None,
    membership_role: str = "retained",
) -> dict[str, Any]:
    """Compare one team's before/after schedule for a *membership-aware* change.

    ``membership_role`` is ``"retained"`` (the team is a participant of a moved
    tournament in both plans), ``"removed"`` (a roster repair dropped it) or
    ``"added"`` (a roster repair introduced it). Retained teams keep the full
    material-regression policy. A deliberate membership change is evaluated
    explicitly against the canonical participation targets/hard maximum instead
    of being condemned by a generic ``tournament_count`` change:

    * an added team gains participation, which is not a regression of its own
      schedule; only a new hard-maximum violation is material, and the extra
      travel to the gained tournament is the cost of that participation rather
      than a regression;
    * a removed team is material only when it creates or widens a participation
      shortfall (or crosses the hard maximum).

    Spacing, temporal coverage and opponent-repetition regressions stay
    material for every role, because a new too-close pair or a new long gap is
    a genuine schedule regression regardless of how the team entered/left the
    tournament.
    """

    analysis = compare_team_schedule_consequence(
        before_plan, after_plan, identity, problem=problem
    )
    role = str(membership_role or "retained")
    if role not in {"retained", "added", "removed"}:
        raise ValueError(f"Unknown membership role: {membership_role!r}")
    if role == "retained":
        result = dict(analysis)
        result["membership_role"] = role
        return result

    material = [
        dict(regression)
        for regression in analysis.get("material_regressions") or []
        if regression.get("code") != "participation_count_changed"
    ]
    effect = team_participation_effect(
        identity, analysis.get("before") or {}, analysis.get("after") or {}, problem=problem
    )
    hard_max = effect.get("hard_max")
    before_count = int(effect.get("before_count") or 0)
    after_count = int(effect.get("after_count") or 0)

    if role == "removed":
        if int(effect["after_shortfall"]) > int(effect["before_shortfall"]):
            material.append(
                {
                    "code": "participation_shortfall_worsened",
                    "before_shortfall": effect["before_shortfall"],
                    "after_shortfall": effect["after_shortfall"],
                    "before_half_counts": effect["before_half_counts"],
                    "after_half_counts": effect["after_half_counts"],
                    "half_targets": effect["half_targets"],
                }
            )
    else:  # added
        # Gaining a tournament is not a regression of the team's own schedule,
        # but it must still not cross a genuinely hard maximum, and the travel
        # it adds is the cost of the gained participation rather than a
        # regression relative to a pre-existing schedule.
        material = [regression for regression in material if regression.get("code") != "travel_materially_worse"]

    if isinstance(hard_max, int) and after_count > hard_max and before_count <= hard_max:
        material.append(
            {
                "code": "participation_hard_max_exceeded",
                "hard_max": hard_max,
                "before_count": before_count,
                "after_count": after_count,
            }
        )

    result = dict(analysis)
    result["membership_role"] = role
    result["material_regressions"] = material
    result["acceptable"] = not material
    result["participation_effect"] = effect
    return result


__all__ = [
    "TeamIdentity",
    "compare_changed_team_schedule_consequence",
    "compare_team_schedule_consequence",
    "compare_team_schedule_profiles",
    "team_participation_effect",
    "team_schedule_profile",
]
