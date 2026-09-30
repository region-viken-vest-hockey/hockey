"""Per-team consequence analysis for targeted canonical roster changes.

Season-wide quality metrics are useful for plan selection, but a one-for-one
roster swap can improve the aggregate while making the displaced team's own
schedule materially worse. This module therefore compares the exact two team
identities affected by a targeted swap.

The thresholds here are policy-level definitions of "material", deliberately
kept deterministic and conservative:
- creating an additional gap under 7 or 14 days is material;
- worsening temporal coverage into the normal >60-day offender range is material;
- materially concentrating a squad's opponents on one opposing club is material;
- increasing estimated season travel by at least 50 km and 25% is material.

The subject is the individual squad, but the primary opponent identity is the
*club within an age group* (see
:mod:`tournament_scheduler.opponent_diversity`). Squad labels such as
"Frisk Asker 1"/"Frisk Asker 2" are administrative: a swap that changes which
sibling squad an opponent meets must not be treated as adding a new opponent.
Exact squad-pair repetition is still reported for diagnostics, but a rise in
that metric alone is never a material regression blocker.

Small neutral trade-offs remain visible in the before/after profiles but do not
block a swap.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date
from typing import Any, Mapping, Sequence

from tournament_scheduler import planning_half
from tournament_scheduler.club_distances import arena_to_club, distance
from tournament_scheduler.opponent_diversity import (
    MEASURE_CO_ATTENDANCE,
    compute_opponent_diversity,
)
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

# A squad whose observed share of encounters with one opposing club is this
# many times the share its squad supply predicts has a genuinely concentrated
# opponent. The expected-share denominator means a club that supplies many
# squads is not penalised merely for being available. A change is material
# only when the index both rises materially and ends up concentrated.
MATERIAL_CLUB_EXPOSURE_INDEX = 2.0
MATERIAL_CLUB_EXPOSURE_INDEX_DELTA = 0.5
# A single encounter with a new club is not concentration; require a real
# repeated relationship before the normalized index can block anything.
MATERIAL_CLUB_EXPOSURE_MIN_ENCOUNTERS = 3


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

    # Club-level primary opponent identity (see opponent_diversity). The exact
    # squad counts above are retained under `opponents` for diagnostics and
    # serialized-report compatibility; diversity/exposure decisions read the
    # club view below.
    diversity = compute_opponent_diversity(plan, identity)
    club_opponents = diversity.to_dict()
    # Always keep the co-attendance proxy as well: when a before/after pair has
    # different game coverage, the comparison uses this consistent proxy for
    # both sides instead of silently skipping the concentration check.
    co_attendance = compute_opponent_diversity(
        plan, identity, measure=MEASURE_CO_ATTENDANCE
    )
    club_opponents["co_attendance"] = {
        "measure": co_attendance.measure,
        "distinct_clubs": co_attendance.distinct_clubs,
        "club_counts": [record.to_dict() for record in co_attendance.clubs],
    }

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
        "club_opponents": club_opponents,
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
        # Exact squad-pair repetition is diagnostic only. Squad labels are
        # administrative, so a rise here must never block a repair on its own;
        # the club-level exposure check below is the material protection.
        warnings.append(
            {
                "code": "more_repeated_opponent_excess",
                "before": old_repeat_excess,
                "after": new_repeat_excess,
                "delta": new_repeat_excess - old_repeat_excess,
                "measure": "exact_squad",
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

    # Club-level primary opponent identity: opportunity-aware exposure. A club
    # supplying many squads is expected to be met more often, so only a rise in
    # the squad-supply-normalized exposure index that ends up concentrated is a
    # material regression. `exposure_index` is absent in profiles produced
    # before this contract existed, in which case the check is skipped.
    before_section = before.get("club_opponents") or {}
    after_section = after.get("club_opponents") or {}
    before_measure = before_section.get("measure")
    after_measure = after_section.get("measure")
    use_co_attendance = bool(
        before_measure and after_measure and before_measure != after_measure
    )
    if use_co_attendance:
        warnings.append(
            {
                "code": "opponent_measure_changed",
                "before": before_measure,
                "after": after_measure,
                "compared_using": MEASURE_CO_ATTENDANCE,
            }
        )
    before_view = (
        before_section.get("co_attendance") or {}
        if use_co_attendance
        else before_section
    )
    after_view = (
        after_section.get("co_attendance") or {}
        if use_co_attendance
        else after_section
    )
    before_clubs = {
        str(record.get("club")): record
        for record in before_view.get("club_counts", []) or []
    }
    after_clubs = {
        str(record.get("club")): record
        for record in after_view.get("club_counts", []) or []
    }
    for club_name, after_record in after_clubs.items():
        before_record = before_clubs.get(club_name)
        if before_record is None:
            old_index = 0.0
            old_share = 0.0
            old_encounters = 0
        else:
            old_index = float(before_record.get("exposure_index", 0.0) or 0.0)
            old_share = float(before_record.get("exposure_share", 0.0) or 0.0)
            old_encounters = int(before_record.get("encounters", 0) or 0)
        new_index = float(after_record.get("exposure_index", 0.0) or 0.0)
        new_share = float(after_record.get("exposure_share", 0.0) or 0.0)
        new_encounters = int(after_record.get("encounters", 0) or 0)
        if (
            new_encounters >= MATERIAL_CLUB_EXPOSURE_MIN_ENCOUNTERS
            and new_encounters > old_encounters
            and new_share > old_share
            and new_index - old_index >= MATERIAL_CLUB_EXPOSURE_INDEX_DELTA
            and new_index >= MATERIAL_CLUB_EXPOSURE_INDEX
        ):
            material.append(
                {
                    "code": "more_concentrated_club_exposure",
                    "club": club_name,
                    "before_index": old_index,
                    "after_index": new_index,
                    "before_share": old_share,
                    "after_share": new_share,
                    "before_encounters": old_encounters,
                    "after_encounters": new_encounters,
                    "threshold_index": MATERIAL_CLUB_EXPOSURE_INDEX,
                }
            )
    old_club_diversity = int(before_view.get("distinct_clubs", 0) or 0)
    new_club_diversity = int(after_view.get("distinct_clubs", 0) or 0)
    if new_club_diversity < old_club_diversity:
        warnings.append(
            {
                "code": "fewer_distinct_opponent_clubs",
                "before": old_club_diversity,
                "after": new_club_diversity,
                "delta": new_club_diversity - old_club_diversity,
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


# Schedule-quality regressions an operator may explicitly accept for one named
# team. Participation-count/hard-maximum regressions are deliberately absent
# here: they are structural or hard-rule concerns (waivers), not quality
# trade-offs. The one deliberate exception is a batch that explicitly cancels a
# tournament: the remaining participants lose exactly that one appearance, so
# CANCEL_ACCEPTABLE_REGRESSION_CODES additionally permits accepting that
# operator-directed shortfall -- but only for teams that actually played in a
# cancelled tournament in the same batch (see the batch consequence boundary).
ACCEPTABLE_REGRESSION_CODES = frozenset(
    {
        "more_gaps_under_7_days",
        "more_gaps_under_14_days",
        "temporal_coverage_materially_worse",
        "more_concentrated_club_exposure",
        "travel_materially_worse",
    }
)

PARTICIPATION_COUNT_CHANGED = "participation_count_changed"

CANCEL_ACCEPTABLE_REGRESSION_CODES = ACCEPTABLE_REGRESSION_CODES | frozenset(
    {PARTICIPATION_COUNT_CHANGED}
)


class RegressionAcceptanceError(ValueError):
    """An operator regression acceptance is malformed or incomplete."""


def parse_regression_acceptances(
    raw: Any,
    reason: str | None,
    *,
    allowed_codes: frozenset[str] = ACCEPTABLE_REGRESSION_CODES,
) -> list[dict[str, str]]:
    """Normalize explicit operator acceptances of named team regressions.

    Each item is ``"<team label>=<code>"``, the fully qualified
    ``"<club>|<team label>|<age group>=<code>"``, or a mapping with ``team``
    (label), ``code`` and optional ``club``/``age_group``. Acceptances are
    never implied: a non-empty reason is mandatory, and only codes in
    ``allowed_codes`` may be accepted (schedule-quality codes by default; a
    cancellation batch widens this to include the one-time participation
    shortfall it deliberately creates).
    """

    items = list(raw or [])
    if not items:
        return []
    resolved_reason = str(reason or "").strip()
    if not resolved_reason:
        raise RegressionAcceptanceError(
            "Accepting a team-schedule regression requires an explicit operator reason"
        )
    acceptances: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in items:
        club = age_group = ""
        if isinstance(item, Mapping):
            team = str(item.get("team") or "").strip()
            code = str(item.get("code") or "").strip()
            club = str(item.get("club") or "").strip()
            age_group = str(item.get("age_group") or "").strip()
        else:
            team, separator, code = str(item).rpartition("=")
            team, code = team.strip(), code.strip()
            if not separator:
                team, code = "", ""
            if "|" in team:
                parts = [part.strip() for part in team.split("|")]
                if len(parts) != 3:
                    team = ""
                else:
                    club, team, age_group = parts
        if not team or not code:
            raise RegressionAcceptanceError(
                "Regression acceptance must be '<team label>=<regression code>' or "
                f"'<club>|<team label>|<age group>=<regression code>': {item!r}"
            )
        if code not in allowed_codes:
            raise RegressionAcceptanceError(
                f"Regression code {code!r} cannot be accepted; acceptable codes: "
                + ", ".join(sorted(allowed_codes))
            )
        key = (club, team, age_group, code)
        if key in seen:
            continue
        seen.add(key)
        acceptance = {"team": team, "code": code, "reason": resolved_reason}
        if club:
            acceptance["club"] = club
        if age_group:
            acceptance["age_group"] = age_group
        acceptances.append(acceptance)
    return acceptances


def consequence_team_identity(analysis: Mapping[str, Any]) -> TeamIdentity:
    for side in ("after", "before"):
        team = (analysis.get(side) or {}).get("team") or {}
        if team.get("label"):
            return _identity(team)
    return ("", "", "")


def _acceptance_label(acceptance: Mapping[str, Any]) -> str:
    parts = [
        str(acceptance.get(key) or "")
        for key in ("club", "team", "age_group")
    ]
    label = "|".join(parts) if parts[0] or parts[2] else parts[1]
    return f"{label}={acceptance.get('code')}"


def evaluate_regression_acceptances(
    team_consequences: Mapping[str, Mapping[str, Any]],
    acceptances: list[Mapping[str, str]] | None,
    *,
    code_scope: Mapping[str, set[TeamIdentity]] | None = None,
) -> dict[str, Any]:
    """Apply explicit operator acceptances to per-team consequence analyses.

    Each acceptance must resolve to exactly one affected team identity
    (club, label, age group); a label shared by several affected teams is
    refused as ambiguous until it is qualified. A material regression is
    waived only for that identity and that exact code. Every other material
    regression still refuses, and an acceptance matching no material
    regression in the candidate is itself a refusal, so acceptances cannot be
    supplied pre-emptively or as a blanket override.

    ``code_scope`` optionally narrows a code to the identities it may waive
    (for example ``participation_count_changed`` is only acceptable for teams
    that played in a tournament the same batch cancelled). An acceptance that
    resolves to an identity outside its code's scope is reported separately
    and refuses, rather than silently waiving an unrelated shortfall.
    """

    affected = {
        key: consequence_team_identity(analysis)
        for key, analysis in team_consequences.items()
    }
    identities = set(affected.values())
    resolved: dict[tuple[TeamIdentity, str], Mapping[str, Any]] = {}
    ambiguous: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    ineligible: list[dict[str, Any]] = []
    for acceptance in acceptances or []:
        candidates = sorted(
            identity
            for identity in identities
            if identity[1] == str(acceptance.get("team") or "")
            and (not acceptance.get("club") or identity[0] == acceptance.get("club"))
            and (not acceptance.get("age_group") or identity[2] == acceptance.get("age_group"))
        )
        entry = {"acceptance": _acceptance_label(acceptance), "code": str(acceptance.get("code") or "")}
        if len(candidates) > 1:
            ambiguous.append(
                {**entry, "candidates": ["|".join(identity) for identity in candidates]}
            )
        elif candidates:
            identity = candidates[0]
            allowed = None if code_scope is None else code_scope.get(entry["code"])
            if allowed is not None and identity not in allowed:
                ineligible.append(
                    {
                        **entry,
                        "team": identity[1],
                        "club": identity[0],
                        "age_group": identity[2],
                    }
                )
                continue
            resolved[(identity, entry["code"])] = acceptance
        else:
            unmatched.append(entry)

    accepted: list[dict[str, Any]] = []
    unaccepted: list[dict[str, Any]] = []
    matched: set[tuple[TeamIdentity, str]] = set()
    for key, analysis in team_consequences.items():
        identity = affected[key]
        for regression in analysis.get("material_regressions") or []:
            code = str(regression.get("code") or "")
            entry = {
                "consequence": key,
                "team": identity[1],
                "club": identity[0],
                "age_group": identity[2],
                "code": code,
                "regression": dict(regression),
            }
            acceptance = resolved.get((identity, code))
            if acceptance is not None:
                matched.add((identity, code))
                accepted.append({**entry, "reason": str(acceptance.get("reason") or "")})
            else:
                unaccepted.append(entry)
    unmatched.extend(
        {"acceptance": _acceptance_label(acceptance), "code": code}
        for (identity, code), acceptance in resolved.items()
        if (identity, code) not in matched
    )
    return {
        "acceptable": not unaccepted and not unmatched and not ambiguous and not ineligible,
        "accepted_regressions": accepted,
        "unaccepted_regressions": unaccepted,
        "unmatched_acceptances": unmatched,
        "ambiguous_acceptances": ambiguous,
        "ineligible_acceptances": ineligible,
    }


class ReviewedConsequenceError(ValueError):
    """A reviewed-consequence token is missing, stale, mismatched or malformed."""


def reviewed_consequence_token(
    *,
    plan_fingerprint: str,
    baseline_revision: str,
    unaccepted_regressions: Sequence[Mapping[str, Any]],
) -> str:
    """Return a stable token identifying one reviewed candidate and its consequences.

    The token binds the exact reviewed baseline revision, the resulting
    candidate plan fingerprint and the precise set of material regressions
    the operator reviewed in a dry-run. Any change to the plan, the underlying
    revision or the consequence set yields a different token, so a reviewed
    acceptance cannot be replayed against a stale or different candidate.
    """

    payload = {
        "plan_fingerprint": str(plan_fingerprint or ""),
        "baseline_revision": str(baseline_revision or ""),
        "consequences": sorted(
            "|".join(
                (
                    str(item.get("club") or ""),
                    str(item.get("team") or ""),
                    str(item.get("age_group") or ""),
                    str(item.get("code") or ""),
                )
            )
            for item in unaccepted_regressions
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "rc-" + hashlib.sha256(encoded).hexdigest()


def apply_reviewed_consequence_acceptance(
    team_consequences: Mapping[str, Mapping[str, Any]],
    acceptances: list[Mapping[str, Any]] | None,
    *,
    code_scope: Mapping[str, set[TeamIdentity]] | None = None,
    reviewed_token: str | None = None,
    reviewed_reason: str | None = None,
    baseline_revision: str = "",
    plan_fingerprint: str = "",
) -> dict[str, Any]:
    """Resolve explicit and reviewed-consequence acceptances into one evaluation.

    Returns a dict with the final ``evaluation`` (accepted or refused), the
    reviewed ``review_token`` for the current candidate, whether a reviewed
    acceptance is ``review_required``, and the domain-level
    ``review_consequences`` that a dry-run would ask the operator to accept.

    When ``reviewed_token`` is supplied it must match the token recomputed from
    the *current* baseline revision, candidate fingerprint and complete material
    consequence set, and it accepts exactly those reviewed consequences without
    reconstructing one ``--accept-team-regression`` argument per team/code. The
    token is bound to the complete set (not just the consequences left after any
    explicit acceptances) so the same reviewed plan can be applied with or
    without the explicit flags. A mismatched, stale or unnecessary token
    refuses; there is no blanket force path.
    """

    preview = evaluate_regression_acceptances(
        team_consequences, acceptances, code_scope=code_scope
    )
    unaccepted = list(preview.get("unaccepted_regressions") or [])
    # The reviewed token and its domain-level consequence list cover the
    # complete material consequence set, independent of explicit acceptances
    # already supplied. Binding the token to only the *remaining* consequences
    # would make the advertised apply flow impossible whenever a dry-run mixed
    # explicit acceptances with a reviewed token: dropping the explicit flags
    # changes the unaccepted set, so the recomputed token could never match.
    complete = evaluate_regression_acceptances(
        team_consequences, [], code_scope=code_scope
    )
    complete_material = list(complete.get("unaccepted_regressions") or [])
    review_consequences = [
        {
            "consequence": item.get("consequence"),
            "club": item.get("club"),
            "team": item.get("team"),
            "age_group": item.get("age_group"),
            "code": item.get("code"),
        }
        for item in complete_material
    ]
    review_token = (
        reviewed_consequence_token(
            plan_fingerprint=plan_fingerprint,
            baseline_revision=baseline_revision,
            unaccepted_regressions=complete_material,
        )
        if complete_material
        else None
    )
    if not reviewed_token:
        return {
            "evaluation": preview,
            "review_required": bool(unaccepted),
            "review_token": review_token,
            "review_consequences": review_consequences,
        }
    if acceptances:
        raise ReviewedConsequenceError(
            "Use --accept-team-regression or --accept-reviewed-consequences, not both"
        )
    if not unaccepted:
        raise ReviewedConsequenceError(
            "No material consequences remain to accept; the reviewed token is unnecessary"
        )
    if str(reviewed_token).strip() != review_token:
        raise ReviewedConsequenceError(
            "Reviewed-consequence token does not match the current candidate plan/state; "
            "re-run the dry-run and review the current consequences"
        )
    reason = str(reviewed_reason or "").strip()
    if not reason:
        raise ReviewedConsequenceError(
            "Accepting reviewed consequences requires an explicit --accept-regression-reason"
        )
    synthesized = [
        {
            "club": item.get("club") or "",
            "team": item.get("team") or "",
            "age_group": item.get("age_group") or "",
            "code": item.get("code") or "",
            "reason": reason,
        }
        for item in unaccepted
    ]
    accepted_evaluation = evaluate_regression_acceptances(
        team_consequences, synthesized, code_scope=code_scope
    )
    return {
        "evaluation": accepted_evaluation,
        "review_required": False,
        "review_token": review_token,
        "review_consequences": review_consequences,
    }


def regression_acceptance_refusals(evaluation: Mapping[str, Any]) -> list[str]:
    """Operator-facing refusal reasons for an acceptance evaluation."""

    reasons: list[str] = []
    if evaluation.get("unaccepted_regressions"):
        reasons.append(
            "materially worsens an affected team's schedule: "
            + ", ".join(
                f"{item['consequence']}:{item['code']}"
                for item in evaluation["unaccepted_regressions"]
            )
        )
    if evaluation.get("ambiguous_acceptances"):
        reasons.append(
            "regression acceptance(s) match several affected teams; qualify as "
            "'<club>|<team label>|<age group>=<code>': "
            + ", ".join(
                f"{item['acceptance']} ({' / '.join(item['candidates'])})"
                for item in evaluation["ambiguous_acceptances"]
            )
        )
    if evaluation.get("unmatched_acceptances"):
        reasons.append(
            "regression acceptance(s) match no material regression: "
            + ", ".join(item["acceptance"] for item in evaluation["unmatched_acceptances"])
        )
    if evaluation.get("ineligible_acceptances"):
        reasons.append(
            "regression acceptance(s) name a team the accepted code does not cover: "
            + ", ".join(
                f"{item['acceptance']} ({item.get('club', '')}|{item.get('team', '')}|"
                f"{item.get('age_group', '')})"
                for item in evaluation["ineligible_acceptances"]
            )
        )
    return reasons


__all__ = [
    "ACCEPTABLE_REGRESSION_CODES",
    "CANCEL_ACCEPTABLE_REGRESSION_CODES",
    "PARTICIPATION_COUNT_CHANGED",
    "RegressionAcceptanceError",
    "ReviewedConsequenceError",
    "TeamIdentity",
    "apply_reviewed_consequence_acceptance",
    "compare_changed_team_schedule_consequence",
    "compare_team_schedule_consequence",
    "compare_team_schedule_profiles",
    "consequence_team_identity",
    "evaluate_regression_acceptances",
    "parse_regression_acceptances",
    "regression_acceptance_refusals",
    "reviewed_consequence_token",
    "team_participation_effect",
    "team_schedule_profile",
]
