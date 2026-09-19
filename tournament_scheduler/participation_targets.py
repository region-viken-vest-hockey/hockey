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
# club x age-group x scope player-pool view
# ---------------------------------------------------------------------------
#
# A participation target is configured per registered team, but a club with
# several teams in one age group can operationally redistribute players between
# the nominal team labels.  The *operational* question is therefore whether the
# club's age-group player pool received its intended participation capacity,
# with the exact per-team split as secondary evidence.
#
# This module owns that aggregation deterministically so a harness/objective
# never has to infer "players can simply be shuffled" from team names.
#
# Classifications:
#
# ``complete``
#     Every team is exactly on target.
# ``intra_club_distribution``
#     A multi-team club's pool is at or above its aggregate target, but the
#     team labels are uneven -- pure redistribution, no missing club-pool
#     participation opportunity.
# ``minor_club_pool_shortfall``
#     A multi-team club's pool is below its aggregate target by at most half of
#     one team's nominal target -- a small residual, lower priority than a
#     single-team miss.
# ``material_club_pool_shortfall``
#     A multi-team club's pool is materially below its aggregate target.
# ``single_team_deviation``
#     A single-team club: the per-team deviation *is* the club-pool deviation,
#     so existing per-team semantics apply unchanged.
# ``over_target``
#     A multi-team club's pool exceeds its aggregate target with no label under
#     its own target.
CLUB_POOL_COMPLETE = "complete"
INTRA_CLUB_DISTRIBUTION = "intra_club_distribution"
MINOR_CLUB_POOL_SHORTFALL = "minor_club_pool_shortfall"
MATERIAL_CLUB_POOL_SHORTFALL = "material_club_pool_shortfall"
SINGLE_TEAM_DEVIATION = "single_team_deviation"
CLUB_POOL_OVER_TARGET = "over_target"

CLUB_POOL_CLASSIFICATIONS: Tuple[str, ...] = (
    CLUB_POOL_COMPLETE,
    INTRA_CLUB_DISTRIBUTION,
    MINOR_CLUB_POOL_SHORTFALL,
    MATERIAL_CLUB_POOL_SHORTFALL,
    SINGLE_TEAM_DEVIATION,
    CLUB_POOL_OVER_TARGET,
)

#: Planning significance implied by each classification.  ``resolved`` and
#: ``informational`` are *not* unresolved participation deficits; ``minor`` and
#: ``material`` are.
CLUB_POOL_SIGNIFICANCE: Dict[str, str] = {
    CLUB_POOL_COMPLETE: "resolved",
    INTRA_CLUB_DISTRIBUTION: "informational",
    MINOR_CLUB_POOL_SHORTFALL: "minor",
    MATERIAL_CLUB_POOL_SHORTFALL: "material",
    SINGLE_TEAM_DEVIATION: "material",
    CLUB_POOL_OVER_TARGET: "informational",
}

#: Classifications that represent a genuine unresolved participation deficit.
UNRESOLVED_CLUB_POOL_CLASSIFICATIONS: frozenset[str] = frozenset(
    {
        MINOR_CLUB_POOL_SHORTFALL,
        MATERIAL_CLUB_POOL_SHORTFALL,
        SINGLE_TEAM_DEVIATION,
    }
)


def counts_as_unresolved_shortfall(
    classification: str, *, direction: str = "under_target"
) -> bool:
    """Whether *classification* is a genuine missing participation opportunity.

    Single-team *over*-target and multi-team over-target are not shortages, so
    only the under-target cases of the unresolved families count.  This is the
    one predicate other layers (objective vector, publication readiness,
    export grouping) must call instead of re-deriving the policy.
    """
    if classification == SINGLE_TEAM_DEVIATION:
        return direction == "under_target"
    return classification in UNRESOLVED_CLUB_POOL_CLASSIFICATIONS


def _evidence_entries(raw: Any) -> List[Mapping[str, Any]]:
    """Normalize one identity's search/acceptance evidence into a list.

    A caller may attach a single evidence mapping (the original contract) or a
    sequence of mappings when the same team has a separate acceptance/outcome per
    deviation scope (season, before-christmas, after-christmas).
    """
    if isinstance(raw, Mapping):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [entry for entry in raw if isinstance(entry, Mapping)]
    return []


def evidence_covers_deviation(
    evidence: Mapping[str, Any],
    *,
    scope: str,
    direction: str,
    actual: int,
    target: int,
) -> bool:
    """True when one evidence/acceptance entry still explains this deviation.

    Coverage is deliberately contextual rather than unconditional. An entry may
    narrow itself with ``scope``, ``direction``, ``target`` and
    ``accepted_deviation`` (the signed deviation the operator/search outcome
    applies to). A target change, a direction flip or a deviation worse than the
    accepted bound therefore stops the entry applying, instead of silently
    masking an avoidable regression. Entries that declare none of these fields
    keep the original unconditional behaviour.
    """
    declared_scope = evidence.get("scope")
    if declared_scope is not None and str(declared_scope) != scope:
        return False
    declared_direction = evidence.get("direction")
    if declared_direction is not None and str(declared_direction) != direction:
        return False
    declared_target = evidence.get("target")
    if isinstance(declared_target, int) and not isinstance(declared_target, bool):
        if declared_target != target:
            return False
    bound = evidence.get("accepted_deviation")
    if isinstance(bound, int) and not isinstance(bound, bool):
        if abs(actual - target) > abs(bound):
            return False
    return True


def search_evidence_from_acceptances(
    records: Iterable[Mapping[str, Any]],
) -> Dict[TeamIdentity, List[Dict[str, Any]]]:
    """Group persisted operator acceptances into verifier ``search_evidence``.

    The acceptance record shape is owned by the canonical season-store layer, but
    the mapping into the verifier evidence contract (keyed by team identity,
    each entry a mapping with ``status``/coverage fields) belongs here, next to
    :func:`evidence_covers_deviation`, so every canonical verification path
    builds the exact same evidence.
    """
    evidence: Dict[TeamIdentity, List[Dict[str, Any]]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        identity = (
            str(record.get("club") or ""),
            str(record.get("label") or ""),
            str(record.get("age_group") or ""),
        )
        evidence.setdefault(identity, []).append(dict(record))
    return evidence


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
    #: Club x age-group x scope aggregate player-pool views (multi-team clubs
    #: included) with the bounded classification above.
    club_pools: List[Dict[str, Any]] = field(default_factory=list)
    #: The subset of ``club_pools`` that counts as a genuine unresolved
    #: participation deficit (excludes intra-club distribution/over-target).
    club_pool_shortfalls: List[Dict[str, Any]] = field(default_factory=list)


def _pool_classification(
    *,
    registered_team_count: int,
    pool_target: int,
    pool_actual: int,
    per_team_targets: Sequence[int],
    per_team_actuals: Sequence[int],
) -> Tuple[str, Optional[int]]:
    """Classify one club x age-group x scope pool deterministically.

    Returns ``(classification, minor_shortfall_threshold)``.  The threshold is
    derived from the configured target model (half of the smallest nominal
    per-team target, at least one) rather than a fixed example count, so a
    target of 4 marks a 1-2 miss minor and a 3+ miss material, while a larger
    configured target scales with it.
    """
    deviation = pool_actual - pool_target
    if registered_team_count <= 1:
        if deviation == 0:
            return CLUB_POOL_COMPLETE, None
        return SINGLE_TEAM_DEVIATION, None
    minor_threshold = max(1, (min(per_team_targets) if per_team_targets else 1) // 2)
    any_label_under = any(a < t for a, t in zip(per_team_actuals, per_team_targets))
    if deviation >= 0 and any_label_under:
        # Aggregate capacity is there; only the nominal labels are uneven.
        return INTRA_CLUB_DISTRIBUTION, minor_threshold
    if deviation > 0:
        return CLUB_POOL_OVER_TARGET, minor_threshold
    if deviation == 0:
        return CLUB_POOL_COMPLETE, minor_threshold
    if abs(deviation) <= minor_threshold:
        return MINOR_CLUB_POOL_SHORTFALL, minor_threshold
    return MATERIAL_CLUB_POOL_SHORTFALL, minor_threshold


def _club_pool_views(
    *,
    counts: Mapping[TeamIdentity, Mapping[str, int]],
    problem: Mapping[str, Any],
    team_lookup: Mapping[TeamIdentity, Mapping[str, Any]],
    split_date: Optional[date],
) -> List[Dict[str, Any]]:
    """Build the club x age-group x scope aggregate participation views.

    Uses the same canonical target resolution as the per-team evaluation, so an
    aggregate target is the sum of the club's registered teams' own targets, not
    a re-derived per-team number.  Pools without any configured target are
    omitted (there is nothing to classify against).
    """
    problem = problem or {}
    club_age_teams: Dict[Tuple[str, str], List[TeamIdentity]] = {}
    for team in problem.get("teams", []) or []:
        if not isinstance(team, Mapping):
            continue
        club = str(team.get("club") or "")
        age_group = str(team.get("age_group") or "")
        label = str(team.get("label") or "")
        if not club or not age_group or not label:
            continue
        club_age_teams.setdefault((club, age_group), []).append((club, label, age_group))
    # Include candidate-only teams too, so an unregistered label is still visible
    # in its pool instead of silently disappearing from the aggregate view.
    known_members = {member for members in club_age_teams.values() for member in members}
    for identity in counts:
        if identity not in known_members:
            club_age_teams.setdefault((identity[0], identity[2]), []).append(identity)

    scopes: Tuple[Tuple[str, Optional[str]], ...] = ((SEASON_SCOPE, None),)
    if split_date is not None:
        scopes = scopes + tuple((half, half) for half in HALVES)

    pools: List[Dict[str, Any]] = []
    for (club, age_group), identities in sorted(club_age_teams.items()):
        for scope, half in scopes:
            per_team_targets: List[int] = []
            per_team_actuals: List[int] = []
            distribution: List[Dict[str, Any]] = []
            for identity in sorted(identities):
                registered_team = team_lookup.get(identity)
                if scope == SEASON_SCOPE:
                    target = resolve_season_target(identity, problem, team=registered_team)
                    actual = sum(counts.get(identity, {}).get(h, 0) for h in HALVES)
                else:
                    target = resolve_half_target(identity, problem, scope, team=registered_team)
                    actual = counts.get(identity, {}).get(half, 0)
                if target is None:
                    continue
                per_team_targets.append(int(target))
                per_team_actuals.append(int(actual))
                distribution.append({"team": identity[1], "actual": int(actual), "target": int(target)})
            if not per_team_targets:
                continue
            # Teams whose own target resolves are the ones the aggregate is
            # meaningful for, so a partially-configured club is classified on
            # the teams that actually count rather than a mislabeled partial sum.
            registered_team_count = len(per_team_targets)
            pool_target = sum(per_team_targets)
            pool_actual = sum(per_team_actuals)
            classification, threshold = _pool_classification(
                registered_team_count=registered_team_count,
                pool_target=pool_target,
                pool_actual=pool_actual,
                per_team_targets=per_team_targets,
                per_team_actuals=per_team_actuals,
            )
            uniform_target = (
                per_team_targets[0]
                if all(target == per_team_targets[0] for target in per_team_targets)
                else None
            )
            pools.append(
                {
                    "club": club,
                    "age_group": age_group,
                    "scope": scope,
                    "registered_team_count": registered_team_count,
                    "nominal_target_per_team": uniform_target,
                    "minor_shortfall_threshold": threshold,
                    "club_pool_target": pool_target,
                    "club_pool_actual": pool_actual,
                    "club_pool_deviation": pool_actual - pool_target,
                    "team_distribution": distribution,
                    "classification": classification,
                    "planning_significance": CLUB_POOL_SIGNIFICANCE[classification],
                    "counts_as_unresolved_shortfall": counts_as_unresolved_shortfall(
                        classification,
                        direction="over_target" if pool_actual > pool_target else "under_target",
                    ),
                }
            )
    return pools


def evaluate_participation(
    candidate: Mapping[str, Any],
    problem: Optional[Mapping[str, Any]],
    *,
    search_evidence: Optional[Mapping[TeamIdentity, Any]] = None,
) -> ParticipationEvaluation:
    """Evaluate participation target/hard-max compliance for *candidate*.

    *search_evidence* (optional) lets a planner attach the outcome of an
    explicit bounded search -- or a persisted operator acceptance -- per team
    (``status`` plus optional ``scope``/``direction``/``target``/
    ``accepted_deviation`` coverage).  Without it every unproven deviation is
    classified ``bounded_search_exhausted`` -- a bounded search that ran out of
    budget is never reported as unavoidable.  An ``operator_accepted`` entry is
    honoured only while it still covers the current deviation, so a target
    change or a worse deviation re-surfaces it as a genuine finding.
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
            # A filled guest place is a game participant, not an RVV season
            # participation: it must never enter the counts the fairness and
            # target-deviation evidence is derived from.
            if team.get("guest"):
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

    club_pools = _club_pool_views(
        counts=counts,
        problem=problem,
        team_lookup=team_lookup,
        split_date=split_date,
    )
    club_pool_lookup: Dict[Tuple[str, str, str], Dict[str, Any]] = {
        (str(pool.get("club") or ""), str(pool.get("age_group") or ""), str(pool.get("scope") or "")): pool
        for pool in club_pools
    }

    def _pool_annotation(identity: TeamIdentity, scope: str, direction: str) -> Dict[str, Any]:
        pool = club_pool_lookup.get((identity[0], identity[2], scope))
        if pool is None:
            return {}
        classification = str(pool.get("classification") or "")
        return {
            "club_pool_classification": classification,
            "club_pool_significance": pool.get("planning_significance"),
            "counts_as_unresolved_shortfall": counts_as_unresolved_shortfall(
                classification, direction=direction
            ),
            "club_pool": pool,
        }

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
        actual: int,
    ) -> Tuple[str, Dict[str, Any]]:
        for evidence in _evidence_entries(search_evidence.get(identity)):
            explicit_status = evidence.get("status")
            if explicit_status not in AVOIDABILITY_STATUSES:
                continue
            if evidence_covers_deviation(
                evidence, scope=scope, direction=direction, actual=actual, target=target
            ):
                return str(explicit_status), dict(evidence)
        evidence: Dict[str, Any] = {}
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
                identity,
                SEASON_SCOPE,
                direction,
                available_tournaments=available,
                target=season_target,
                actual=season_actual,
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
                    **_pool_annotation(identity, SEASON_SCOPE, direction),
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
                    identity,
                    half,
                    direction,
                    available_tournaments=available,
                    target=half_target,
                    actual=half_actual,
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
                        **_pool_annotation(identity, half, direction),
                    }
                )

    club_pool_shortfalls = [
        pool for pool in club_pools if pool.get("counts_as_unresolved_shortfall")
    ]
    metrics = _metrics(
        teams,
        deviations,
        registered_by_age,
        registered_by_age_club,
        club_age_half_counts,
        club_age_half_totals,
        split_date,
        club_pools,
    )
    return ParticipationEvaluation(
        teams=teams,
        deviations=deviations,
        hard_max_violations=hard_max_violations,
        metrics=metrics,
        club_pools=club_pools,
        club_pool_shortfalls=club_pool_shortfalls,
    )


def _metrics(
    teams: Sequence[Mapping[str, Any]],
    deviations: Sequence[Mapping[str, Any]],
    registered_by_age: Mapping[str, int],
    registered_by_age_club: Mapping[Tuple[str, str], int],
    club_age_half_counts: Mapping[Tuple[str, str, str], int],
    club_age_half_totals: Mapping[Tuple[str, str], int],
    split_date: Optional[date],
    club_pools: Sequence[Mapping[str, Any]] = (),
) -> Dict[str, Any]:
    targeted = [entry for entry in teams if isinstance(entry.get("season_target"), int)]
    season_deviations = [d for d in deviations if d.get("scope") == SEASON_SCOPE]
    half_deviations = [d for d in deviations if d.get("scope") in HALVES]

    def _is_unresolved(deviation: Mapping[str, Any]) -> bool:
        flag = deviation.get("counts_as_unresolved_shortfall")
        # Older/synthetic deviation fixtures without a club-pool view keep the
        # original per-team semantics (every material deviation counts).
        return True if flag is None else bool(flag)

    unresolved_season_deviations = [d for d in season_deviations if _is_unresolved(d)]
    unresolved_half_deviations = [d for d in half_deviations if _is_unresolved(d)]

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
    unresolved_avoidable = sum(
        1 for d in deviations if d.get("avoidability") == AVOIDABLE and _is_unresolved(d)
    )
    cross_half_opportunities = sum(
        1
        for d in deviations
        if d.get("direction") == "under_target"
        and (d.get("evidence") or {}).get("cross_half_capacity_available")
    )

    pool_classifications = [str(pool.get("classification") or "") for pool in club_pools]
    unresolved_pools = [pool for pool in club_pools if pool.get("counts_as_unresolved_shortfall")]

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
        # Club x age-group x scope player-pool view. The per-team numbers above
        # stay exact and auditable; these are the aggregation the objective
        # vector / publication readiness consume so an intra-club label
        # imbalance is not treated as an equal-weight participation shortfall.
        "club_pool_count": len(club_pools),
        "club_pool_intra_distribution_count": sum(
            1 for name in pool_classifications if name == INTRA_CLUB_DISTRIBUTION
        ),
        "club_pool_minor_shortfall_count": sum(
            1 for name in pool_classifications if name == MINOR_CLUB_POOL_SHORTFALL
        ),
        "club_pool_material_shortfall_count": sum(
            1 for name in pool_classifications if name == MATERIAL_CLUB_POOL_SHORTFALL
        ),
        "club_pool_single_team_deviation_count": sum(
            1 for name in pool_classifications if name == SINGLE_TEAM_DEVIATION
        ),
        "club_pool_over_target_count": sum(
            1 for name in pool_classifications if name == CLUB_POOL_OVER_TARGET
        ),
        "club_pool_unresolved_shortfall_count": len(unresolved_pools),
        "club_pool_unresolved_total_absolute_deviation": sum(
            abs(int(pool.get("club_pool_deviation") or 0)) for pool in unresolved_pools
        ),
        "club_pool_unresolved_season_total_absolute_deviation": sum(
            abs(int(d.get("deviation") or 0)) for d in unresolved_season_deviations
        ),
        "club_pool_unresolved_max_team_season_deviation": max(
            (abs(int(d.get("deviation") or 0)) for d in unresolved_season_deviations), default=0
        ),
        "club_pool_unresolved_half_total_absolute_deviation": sum(
            abs(int(d.get("deviation") or 0)) for d in unresolved_half_deviations
        ),
        "club_pool_unresolved_max_team_half_deviation": max(
            (abs(int(d.get("deviation") or 0)) for d in unresolved_half_deviations), default=0
        ),
        "club_pool_unresolved_avoidable_deviation_count": unresolved_avoidable,
    }


#: Relative planning severity of the club-pool classifications, used only to
#: decide whether one candidate's pool state is materially worse than another.
_CLUB_POOL_SIGNIFICANCE_RANK: Dict[str, int] = {
    "resolved": 0,
    "informational": 0,
    "minor": 1,
    "material": 2,
}


def _club_pool_worse(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    """Whether *after* is a material participation regression of *before*.

    A repair may rotate participation between a club's sibling teams, but it
    may not deepen the club's aggregate player-pool deficit. A pool is worse
    when it becomes an unresolved shortfall, when an existing unresolved
    shortfall grows, or when its planning significance escalates. A larger
    over-target surplus is informational, not a regression.
    """

    before_rank = _CLUB_POOL_SIGNIFICANCE_RANK.get(
        str(before.get("planning_significance") or ""), 0
    )
    after_rank = _CLUB_POOL_SIGNIFICANCE_RANK.get(
        str(after.get("planning_significance") or ""), 0
    )
    if after_rank > before_rank:
        return True
    before_unresolved = bool(before.get("counts_as_unresolved_shortfall"))
    after_unresolved = bool(after.get("counts_as_unresolved_shortfall"))
    if after_unresolved and not before_unresolved:
        return True
    if after_unresolved and before_unresolved:
        before_actual = int(before.get("club_pool_actual") or 0)
        after_actual = int(after.get("club_pool_actual") or 0)
        before_deviation = abs(int(before.get("club_pool_deviation") or 0))
        after_deviation = abs(int(after.get("club_pool_deviation") or 0))
        return after_actual < before_actual or after_deviation > before_deviation
    return False


def club_pool_participation_regressions(
    before: ParticipationEvaluation,
    after: ParticipationEvaluation,
    *,
    club_age_pairs: Optional[Iterable[Tuple[str, str]]] = None,
) -> List[Dict[str, Any]]:
    """Return club-pool participation views that materially worsened.

    Scoped to ``club_age_pairs`` when given (the clubs/age groups a repair
    actually touched). Uses the canonical club-pool classification so a repair
    that merely redistributes participation between sibling teams keeps the
    aggregate pool unchanged, while a repair that deepens or introduces an
    aggregate deficit is reported as a regression.
    """

    scope = {tuple(pair) for pair in (club_age_pairs or [])} or None
    before_index = {
        (pool.get("club"), pool.get("age_group"), pool.get("scope")): pool
        for pool in before.club_pools
    }
    regressions: List[Dict[str, Any]] = []
    for pool in after.club_pools:
        key = (pool.get("club"), pool.get("age_group"), pool.get("scope"))
        if scope is not None and (key[0], key[1]) not in scope:
            continue
        prior = before_index.get(key)
        if prior is None:
            continue
        if not _club_pool_worse(prior, pool):
            continue
        regressions.append(
            {
                "club": pool.get("club"),
                "age_group": pool.get("age_group"),
                "scope": pool.get("scope"),
                "registered_team_count": pool.get("registered_team_count"),
                "club_pool_target": pool.get("club_pool_target"),
                "club_pool_actual_before": prior.get("club_pool_actual"),
                "club_pool_actual_after": pool.get("club_pool_actual"),
                "club_pool_deviation_before": prior.get("club_pool_deviation"),
                "club_pool_deviation_after": pool.get("club_pool_deviation"),
                "classification_before": prior.get("classification"),
                "classification_after": pool.get("classification"),
            }
        )
    return regressions


def club_pool_snapshot(
    evaluation: ParticipationEvaluation,
    *,
    club_age_pairs: Optional[Iterable[Tuple[str, str]]] = None,
) -> List[Dict[str, Any]]:
    """Bounded club-pool evidence for the clubs/age groups a repair touched."""

    scope = {tuple(pair) for pair in (club_age_pairs or [])} or None
    rows: List[Dict[str, Any]] = []
    for pool in evaluation.club_pools:
        if scope is not None and (pool.get("club"), pool.get("age_group")) not in scope:
            continue
        rows.append(
            {
                "club": pool.get("club"),
                "age_group": pool.get("age_group"),
                "scope": pool.get("scope"),
                "registered_team_count": pool.get("registered_team_count"),
                "club_pool_target": pool.get("club_pool_target"),
                "club_pool_actual": pool.get("club_pool_actual"),
                "club_pool_deviation": pool.get("club_pool_deviation"),
                "classification": pool.get("classification"),
                "counts_as_unresolved_shortfall": pool.get("counts_as_unresolved_shortfall"),
            }
        )
    return rows


__all__ = [
    "AVOIDABLE",
    "PROVEN_INFEASIBLE",
    "BOUNDED_SEARCH_EXHAUSTED",
    "OPERATOR_ACCEPTED",
    "AVOIDABILITY_STATUSES",
    "HALVES",
    "SEASON_SCOPE",
    "CLUB_POOL_COMPLETE",
    "INTRA_CLUB_DISTRIBUTION",
    "MINOR_CLUB_POOL_SHORTFALL",
    "MATERIAL_CLUB_POOL_SHORTFALL",
    "SINGLE_TEAM_DEVIATION",
    "CLUB_POOL_OVER_TARGET",
    "CLUB_POOL_CLASSIFICATIONS",
    "CLUB_POOL_SIGNIFICANCE",
    "UNRESOLVED_CLUB_POOL_CLASSIFICATIONS",
    "ParticipationEvaluation",
    "club_pool_participation_regressions",
    "club_pool_snapshot",
    "counts_as_unresolved_shortfall",
    "evaluate_participation",
    "evidence_covers_deviation",
    "search_evidence_from_acceptances",
    "resolve_hard_max",
    "resolve_half_target",
    "resolve_season_target",
    "split_date_for",
]
