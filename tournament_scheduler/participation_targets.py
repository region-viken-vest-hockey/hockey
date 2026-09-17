"""Canonical participation target semantics and bounded-relaxation evidence.

RVV participation targets (``deltakelser_per_lag_før_jul`` /
``deltakelser_per_lag_etter_jul`` in the workbook) are **strong operational
goals**, not hard legality boundaries and not freely-tradeable soft
preferences:

* the planner should meet the configured per-half and season totals whenever
  that is reasonably feasible;
* a genuine deviation is allowed only as a *bounded* relaxation when harder
  feasibility constraints or demonstrably unavailable capacity make the target
  impractical;
* an explicit hard maximum (``participation_hard_max``) is a separate, truly
  hard rule and is never inferred from the target.

This module is the single owner of that distinction.  ``verify_candidate``,
``score_candidate``, the rules report and the Stage 3 CP-SAT participant
resolution all resolve the same target/hard-max values here instead of each
re-deriving their own precedence.

Target deviation is reported, never silently accepted: every deviation carries
an :data:`AVOIDABILITY_STATUSES` classification plus the capacity/search
coverage it was judged against, so a harness/controller can tell an
``avoidable`` regression from ``proven_infeasible`` capacity scarcity and from
a search that merely ran out of budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from tournament_scheduler import planning_half

TeamIdentity = Tuple[str, str, str]

HALVES: Tuple[str, ...] = ("before_christmas", "after_christmas")
SEASON_SCOPE = "season"

# Avoidability classifications for a remaining material participation
# deviation. A caller must never present ``BOUNDED_SEARCH_EXHAUSTED`` as proof
# of unavoidability.
AVOIDABLE = "avoidable"
PROVEN_INFEASIBLE = "proven_infeasible"
BOUNDED_SEARCH_EXHAUSTED = "bounded_search_exhausted"
OPERATOR_ACCEPTED = "operator_accepted"

AVOIDABILITY_STATUSES: Tuple[str, ...] = (
    AVOIDABLE,
    PROVEN_INFEASIBLE,
    BOUNDED_SEARCH_EXHAUSTED,
    OPERATOR_ACCEPTED,
)


# ---------------------------------------------------------------------------
# target / hard-max resolution
# ---------------------------------------------------------------------------


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def split_date_for(
    problem: Optional[Mapping[str, Any]],
    tournaments: Optional[Iterable[Mapping[str, Any]]] = None,
) -> Optional[date]:
    """Return the shared before/after-Christmas boundary for *problem*."""
    problem = problem or {}
    explicit = _parse_date(problem.get("christmas_split_date"))
    if explicit is not None:
        return explicit
    start = _parse_date(problem.get("start_date"))
    end = _parse_date(problem.get("end_date"))
    if start is not None and end is not None:
        return planning_half.christmas_split_date(start, end)
    dated = [
        parsed
        for parsed in (_parse_date(t.get("date")) for t in (tournaments or []))
        if parsed is not None
    ]
    if dated:
        return planning_half.christmas_split_date(min(dated), max(dated))
    return None


def _team_lookup(problem: Optional[Mapping[str, Any]]) -> Dict[TeamIdentity, Mapping[str, Any]]:
    lookup: Dict[TeamIdentity, Mapping[str, Any]] = {}
    for team in (problem or {}).get("teams", []) or []:
        if not isinstance(team, Mapping):
            continue
        identity = (
            str(team.get("club") or ""),
            str(team.get("label") or ""),
            str(team.get("age_group") or ""),
        )
        lookup[identity] = team
    return lookup


def resolve_season_target(
    identity: TeamIdentity,
    problem: Optional[Mapping[str, Any]],
    *,
    team: Optional[Mapping[str, Any]] = None,
) -> Optional[int]:
    """Canonical season-total participation target for *identity*, or ``None``.

    Precedence mirrors ``SeasonPlanner._team_target_tournament_count``: an
    explicit per-team/global override (season-wide by definition), then the
    per-age-group before/after values summed.
    """
    problem = problem or {}
    team = team if team is not None else _team_lookup(problem).get(identity)
    explicit = (team or {}).get("target_tournament_count")
    if isinstance(explicit, int):
        return explicit
    default = problem.get("target_tournament_count")
    if isinstance(default, int):
        return default
    age_group = identity[2]
    targets = (problem.get("participation_targets_by_age_group") or {}).get(age_group) or {}
    before = targets.get("before_christmas")
    after = targets.get("after_christmas")
    if isinstance(before, int) and isinstance(after, int):
        return before + after
    return None


def resolve_half_target(
    identity: TeamIdentity,
    problem: Optional[Mapping[str, Any]],
    half: str,
    *,
    team: Optional[Mapping[str, Any]] = None,
) -> Optional[int]:
    """Canonical per-half participation target for *identity*, or ``None``.

    An explicit season-wide override has no half of its own, so it is split
    deterministically using the age group's before/after values as a ratio
    (falling back to an even split), keeping the halves sum to the season
    target.  Otherwise the age group's authoritative half value is used
    directly.
    """
    problem = problem or {}
    team = team if team is not None else _team_lookup(problem).get(identity)
    explicit = (team or {}).get("target_tournament_count")
    default = problem.get("target_tournament_count")
    has_explicit_season_target = isinstance(explicit, int) or isinstance(default, int)
    age_group = identity[2]
    targets = (problem.get("participation_targets_by_age_group") or {}).get(age_group) or {}
    if has_explicit_season_target:
        season_target = explicit if isinstance(explicit, int) else int(default)  # type: ignore[arg-type]
        if half not in HALVES:
            return season_target
        before_weight = targets.get("before_christmas") or 0
        after_weight = targets.get("after_christmas") or 0
        total_weight = before_weight + after_weight
        if total_weight <= 0:
            before = season_target // 2
        else:
            before = int(round(season_target * before_weight / total_weight))
        before = max(0, min(season_target, before))
        return {"before_christmas": before, "after_christmas": season_target - before}[half]
    half_target = targets.get(half)
    return half_target if isinstance(half_target, int) else None


def resolve_hard_max(
    identity: TeamIdentity,
    problem: Optional[Mapping[str, Any]],
    *,
    team: Optional[Mapping[str, Any]] = None,
) -> Optional[int]:
    """Canonical *optional* explicit participation hard maximum, or ``None``.

    This is a genuinely hard rule and is never inferred from the target.  It is
    separate from :func:`resolve_season_target` by design.
    """
    problem = problem or {}
    team = team if team is not None else _team_lookup(problem).get(identity)
    explicit = (team or {}).get("participation_hard_max")
    if isinstance(explicit, int):
        return explicit
    default = problem.get("participation_hard_max")
    if isinstance(default, int):
        return default
    by_age = (problem.get("participation_hard_max_by_age_group") or {}).get(identity[2])
    if isinstance(by_age, int):
        return by_age
    return None


# ---------------------------------------------------------------------------
# counts and evaluation
# ---------------------------------------------------------------------------


def _tournament_half(tournament_date: Optional[date], split_date: Optional[date]) -> Optional[str]:
    if tournament_date is None:
        return None
    half = planning_half.tournament_half(tournament_date, split_date)
    return half if half in HALVES else "unsplit"


@dataclass
class ParticipationEvaluation:
    """Deterministic participation counts, deviations and bounded evidence."""

    teams: List[Dict[str, Any]] = field(default_factory=list)
    deviations: List[Dict[str, Any]] = field(default_factory=list)
    hard_max_violations: List[Dict[str, Any]] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)


def evaluate_participation(
    candidate: Mapping[str, Any],
    problem: Optional[Mapping[str, Any]],
    *,
    search_evidence: Optional[Mapping[TeamIdentity, Mapping[str, Any]]] = None,
) -> ParticipationEvaluation:
    """Evaluate participation target/hard-max compliance for *candidate*.

    *search_evidence* (optional) lets a planner attach the outcome of an
    explicit bounded search per team (``status`` plus free-form coverage
    notes).  Without it every unproven deviation is classified
    ``bounded_search_exhausted`` -- a bounded search that ran out of budget is
    never reported as unavoidable.
    """
    problem = problem or {}
    tournaments = [t for t in candidate.get("tournaments", []) or [] if not t.get("cancelled")]
    split_date = split_date_for(problem, tournaments)

    counts: Dict[TeamIdentity, Dict[str, int]] = {}
    age_group_half_tournaments: Dict[Tuple[str, str], int] = {}
    age_group_season_tournaments: Dict[str, int] = {}
    club_age_half_counts: Dict[Tuple[str, str, str], int] = {}
    club_age_half_totals: Dict[Tuple[str, str], int] = {}

    for tournament in tournaments:
        t_date = _parse_date(tournament.get("date"))
        half = _tournament_half(t_date, split_date)
        age_group = str(tournament.get("age_group") or "")
        if age_group:
            age_group_season_tournaments[age_group] = age_group_season_tournaments.get(age_group, 0) + 1
            if half in HALVES:
                key = (age_group, half)
                age_group_half_tournaments[key] = age_group_half_tournaments.get(key, 0) + 1
        for team in tournament.get("teams", []) or []:
            if not isinstance(team, Mapping) or not team.get("label"):
                continue
            identity = (
                str(team.get("club") or ""),
                str(team.get("label") or ""),
                str(team.get("age_group") or ""),
            )
            season_counts = counts.setdefault(identity, {half: 0 for half in HALVES})
            if half in HALVES:
                season_counts[half] = season_counts.get(half, 0) + 1
            if age_group:
                club = identity[0]
                if club:
                    scope = (age_group, half if half in HALVES else SEASON_SCOPE)
                    club_age_half_counts[(age_group, scope[1], club)] = (
                        club_age_half_counts.get((age_group, scope[1], club), 0) + 1
                    )
                    club_age_half_totals[(age_group, scope[1])] = (
                        club_age_half_totals.get((age_group, scope[1]), 0) + 1
                    )

    registered_by_age: Dict[str, int] = {}
    registered_by_age_club: Dict[Tuple[str, str], int] = {}
    for team in problem.get("teams", []) or []:
        if not isinstance(team, Mapping):
            continue
        age_group = str(team.get("age_group") or "")
        club = str(team.get("club") or "")
        if age_group:
            registered_by_age[age_group] = registered_by_age.get(age_group, 0) + 1
            registered_by_age_club[(age_group, club)] = registered_by_age_club.get((age_group, club), 0) + 1

    known_identities: set[TeamIdentity] = set(counts)
    team_lookup = _team_lookup(problem)
    known_identities.update(team_lookup)
    search_evidence = search_evidence or {}

    teams: List[Dict[str, Any]] = []
    deviations: List[Dict[str, Any]] = []
    hard_max_violations: List[Dict[str, Any]] = []

    def _classify(
        identity: TeamIdentity,
        scope: str,
        direction: str,
        *,
        available_tournaments: int,
        target: int,
    ) -> Tuple[str, Dict[str, Any]]:
        evidence = dict(search_evidence.get(identity) or {})
        explicit_status = evidence.get("status")
        if explicit_status in AVOIDABILITY_STATUSES:
            return str(explicit_status), evidence
        if direction == "under_target" and available_tournaments <= target:
            # No assignment of participants can give a team more participations
            # than there are distinct tournaments in the scope, so within the
            # current (fixed) tournament skeleton the target is provably
            # unreachable.
            return PROVEN_INFEASIBLE, {
                "reason": "fewer_tournaments_than_target",
                "available_tournaments": available_tournaments,
                "target": target,
                "scope": scope,
                "coverage": "fixed_tournament_skeleton",
            }
        return BOUNDED_SEARCH_EXHAUSTED, {
            "reason": "no_better_candidate_verified_within_search",
            "available_tournaments": available_tournaments,
            "target": target,
            "scope": scope,
            "coverage": str(evidence.get("coverage") or "bounded_search"),
        }

    for identity in sorted(known_identities):
        season_counts = counts.get(identity, {half: 0 for half in HALVES})
        season_actual = sum(season_counts.get(half, 0) for half in HALVES)
        registered_team = team_lookup.get(identity)
        season_target = resolve_season_target(identity, problem, team=registered_team)
        hard_max = resolve_hard_max(identity, problem, team=registered_team)
        before_target = resolve_half_target(identity, problem, "before_christmas", team=registered_team)
        after_target = resolve_half_target(identity, problem, "after_christmas", team=registered_team)

        entry: Dict[str, Any] = {
            "club": identity[0],
            "label": identity[1],
            "age_group": identity[2],
            "season_actual": season_actual,
            "season_target": season_target,
            "participation_hard_max": hard_max,
            "before_christmas_actual": season_counts.get("before_christmas", 0),
            "after_christmas_actual": season_counts.get("after_christmas", 0),
            "before_christmas_target": before_target,
            "after_christmas_target": after_target,
        }
        teams.append(entry)

        if isinstance(hard_max, int) and season_actual > hard_max:
            hard_max_violations.append(
                {
                    "code": "participation_hard_max_exceeded",
                    "club": identity[0],
                    "label": identity[1],
                    "age_group": identity[2],
                    "actual": season_actual,
                    "participation_hard_max": hard_max,
                    "message": (
                        f"Team {identity[1]!r} is scheduled in {season_actual} tournaments, "
                        f"exceeding its explicit participation hard maximum of {hard_max}"
                    ),
                }
            )

        if isinstance(season_target, int) and season_actual != season_target:
            direction = "over_target" if season_actual > season_target else "under_target"
            available = age_group_season_tournaments.get(identity[2], 0)
            status, coverage = _classify(
                identity, SEASON_SCOPE, direction, available_tournaments=available, target=season_target
            )
            deviations.append(
                {
                    "team": identity[1],
                    "club": identity[0],
                    "age_group": identity[2],
                    "scope": SEASON_SCOPE,
                    "direction": direction,
                    "actual": season_actual,
                    "target": season_target,
                    "deviation": season_actual - season_target,
                    "avoidability": status,
                    "evidence": coverage,
                }
            )

        # An explicit per-team/global override is season-wide by definition;
        # its deterministic half split is reported on the team entry but must
        # not double-count as an independent half deviation.
        has_explicit_season_target = isinstance(
            (registered_team or {}).get("target_tournament_count"), int
        ) or isinstance(problem.get("target_tournament_count"), int)
        if split_date is not None and not has_explicit_season_target:
            for half, half_target in (("before_christmas", before_target), ("after_christmas", after_target)):
                if not isinstance(half_target, int):
                    continue
                half_actual = season_counts.get(half, 0)
                if half_actual == half_target:
                    continue
                direction = "over_target" if half_actual > half_target else "under_target"
                available = age_group_half_tournaments.get((identity[2], half), 0)
                status, coverage = _classify(
                    identity, half, direction, available_tournaments=available, target=half_target
                )
                # Cross-half compensation: an under-target half whose sibling
                # half still has tournaments is a bounded relaxation candidate
                # (`2 + 4` preferred to `2 + 3`) -- expose the capacity as
                # evidence rather than silently leaving the team short. This is
                # still only `bounded_search_exhausted`, never proof that a
                # legal compensating assignment exists.
                other_half = "after_christmas" if half == "before_christmas" else "before_christmas"
                cross_half_capacity = age_group_half_tournaments.get((identity[2], other_half), 0)
                if direction == "under_target":
                    coverage["cross_half_capacity_available"] = cross_half_capacity > 0
                    coverage["cross_half_tournament_count"] = cross_half_capacity
                    coverage["cross_half"] = other_half
                deviations.append(
                    {
                        "team": identity[1],
                        "club": identity[0],
                        "age_group": identity[2],
                        "scope": half,
                        "direction": direction,
                        "actual": half_actual,
                        "target": half_target,
                        "deviation": half_actual - half_target,
                        "avoidability": status,
                        "evidence": coverage,
                    }
                )

    metrics = _metrics(
        teams,
        deviations,
        registered_by_age,
        registered_by_age_club,
        club_age_half_counts,
        club_age_half_totals,
        split_date,
    )
    return ParticipationEvaluation(
        teams=teams,
        deviations=deviations,
        hard_max_violations=hard_max_violations,
        metrics=metrics,
    )


def _metrics(
    teams: Sequence[Mapping[str, Any]],
    deviations: Sequence[Mapping[str, Any]],
    registered_by_age: Mapping[str, int],
    registered_by_age_club: Mapping[Tuple[str, str], int],
    club_age_half_counts: Mapping[Tuple[str, str, str], int],
    club_age_half_totals: Mapping[Tuple[str, str], int],
    split_date: Optional[date],
) -> Dict[str, Any]:
    targeted = [entry for entry in teams if isinstance(entry.get("season_target"), int)]
    season_deviations = [d for d in deviations if d.get("scope") == SEASON_SCOPE]
    half_deviations = [d for d in deviations if d.get("scope") in HALVES]

    teams_exact = sum(1 for entry in targeted if entry["season_actual"] == entry["season_target"])
    teams_under = sum(1 for entry in targeted if entry["season_actual"] < entry["season_target"])
    teams_over = sum(1 for entry in targeted if entry["season_actual"] > entry["season_target"])
    season_total_deviation = sum(abs(int(d["deviation"])) for d in season_deviations)
    max_season_deviation = max((abs(int(d["deviation"])) for d in season_deviations), default=0)

    teams_exact_half = 0
    teams_with_half_targets = 0
    for entry in teams:
        before_target = entry.get("before_christmas_target")
        after_target = entry.get("after_christmas_target")
        if not (isinstance(before_target, int) and isinstance(after_target, int)):
            continue
        teams_with_half_targets += 1
        if (
            entry.get("before_christmas_actual") == before_target
            and entry.get("after_christmas_actual") == after_target
        ):
            teams_exact_half += 1
    half_total_deviation = sum(abs(int(d["deviation"])) for d in half_deviations)
    max_half_deviation = max((abs(int(d["deviation"])) for d in half_deviations), default=0)

    club_share_deviations: List[float] = []
    for (age_group, scope), total in club_age_half_totals.items():
        if total <= 0:
            continue
        total_registered = registered_by_age.get(age_group, 0)
        if total_registered <= 0:
            continue
        for (row_age, row_scope, club), actual in club_age_half_counts.items():
            if row_age != age_group or row_scope != scope:
                continue
            registered = registered_by_age_club.get((age_group, club), 0)
            club_share_deviations.append(abs(actual / total - registered / total_registered))

    sibling_spreads: List[int] = []
    for age_group in registered_by_age:
        clubs = {club for (row_age, club) in registered_by_age_club if row_age == age_group}
        for club in clubs:
            sibling_counts = [
                entry["season_actual"]
                for entry in teams
                if entry["age_group"] == age_group and entry["club"] == club
            ]
            if len(sibling_counts) >= 2:
                sibling_spreads.append(max(sibling_counts) - min(sibling_counts))

    avoidable = sum(1 for d in deviations if d.get("avoidability") == AVOIDABLE)
    proven = sum(1 for d in deviations if d.get("avoidability") == PROVEN_INFEASIBLE)
    exhausted = sum(1 for d in deviations if d.get("avoidability") == BOUNDED_SEARCH_EXHAUSTED)
    accepted = sum(1 for d in deviations if d.get("avoidability") == OPERATOR_ACCEPTED)
    cross_half_opportunities = sum(
        1
        for d in deviations
        if d.get("direction") == "under_target"
        and (d.get("evidence") or {}).get("cross_half_capacity_available")
    )

    return {
        "teams_with_season_target": len(targeted),
        "teams_exact_season_target": teams_exact,
        "teams_under_season_target": teams_under,
        "teams_over_season_target": teams_over,
        "season_total_absolute_deviation": season_total_deviation,
        "max_team_season_deviation": max_season_deviation,
        "teams_with_half_targets": teams_with_half_targets,
        "teams_exact_half_targets": teams_exact_half,
        "half_total_absolute_deviation": half_total_deviation,
        "max_team_half_deviation": max_half_deviation,
        "has_half_split": split_date is not None,
        "club_share_deviation": round(max(club_share_deviations, default=0.0), 6),
        "sibling_spread": max(sibling_spreads, default=0),
        "deviation_count": len(deviations),
        "avoidable_deviation_count": avoidable,
        "proven_unavoidable_deviation_count": proven,
        "search_exhausted_deviation_count": exhausted,
        "operator_accepted_deviation_count": accepted,
        "cross_half_compensation_opportunity_count": cross_half_opportunities,
    }


__all__ = [
    "AVOIDABLE",
    "PROVEN_INFEASIBLE",
    "BOUNDED_SEARCH_EXHAUSTED",
    "OPERATOR_ACCEPTED",
    "AVOIDABILITY_STATUSES",
    "HALVES",
    "SEASON_SCOPE",
    "ParticipationEvaluation",
    "evaluate_participation",
    "resolve_hard_max",
    "resolve_half_target",
    "resolve_season_target",
    "split_date_for",
]
