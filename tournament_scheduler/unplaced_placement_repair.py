"""Materialize a genuine unplaced placement obligation into a verified tournament.

A tournament the deterministic slot search could not place exists only as
structured planning work in ``SeasonPlan.unresolved_tournament_placements``
(see ``placement_findings``/``placement_normalization``): it has a responsible
host, roster, source date and required shape, but no ``Tournament``. Earlier
repair providers assumed the thing being repaired was already a scheduled
tournament, so an unresolved obligation could carry candidate-weekend evidence
without any action that turns it into hockey.

This provider owns the missing *materialization* step. For one unresolved
obligation it progressively tries the repository-owned repair ladder:

1. ``same_date_start_time`` -- another verified start time on the source date;
2. ``same_host_date`` -- another verified season date for the same responsible host;
3. ``participant_reselection`` -- a bounded, host-representing replacement
   roster for a date whose current roster already plays elsewhere;
4. ``capacity_release`` -- move a scheduled tournament that consumes the
   responsible host's arena/time and then materialize into the freed capacity;
5. ``coupled_cross_age_exchange`` -- the same release when the blocking
   tournament is another age group sharing the arena; the constrained resource
   is arena/time, not age group;
6. ``bounded_neighborhood`` -- the full bounded date/start/roster scan.

Every legal option is a real :class:`~tournament_scheduler.models.Tournament`
payload (verified date, the responsible host's arena, a start time, the final
roster and freshly generated games), removes the obligation, preserves the
responsible host and never transfers hosting to another club. The independent
verifier decides legality; the provider only proposes.

The provider also reports, per obligation, which supported dimensions were
attempted and which remain untried, so a bounded search that found nothing is
reported as ``search_incomplete`` rather than being mistaken for proof that the
obligation is infeasible. ``proven_infeasible`` is never returned: no
deterministic exhaustive capability exists here.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from . import planning_half
from .candidate_weekends import season_weekend_dates
from .host_representation import clubs_represent_same_club, constituent_clubs
from .host_team_missing_repair import (
    GENERATED_START_TIMES,
    RepairOption,
    _candidate_start_times,
    _duration_minutes,
    _end_time,
    _regenerate_games,
    _slug,
    bounded_start_times,
    candidate_fingerprint,
)
from .models import Team  # noqa: F401  (re-exported for callers/tests)
from .planning_contract import (
    HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT,
    _parse_date,
    _team_identity,
    apply_calendar_interpretations,
    external_calendar_conflict,
    movable_calendar_opportunity,
    verify_candidate,
)
from .search_capability import (
    COVERAGE_BOUNDED_EXHAUSTED,
    SearchCapability,
    capability_is_stale,
    mark_coverage_stale,
)

# Ladder dimensions, in escalating-cost order. The first two are always tried
# by a cheap options pass; the rest require an explicit bounded search.
DIMENSION_SAME_DATE_START_TIME = "same_date_start_time"
DIMENSION_SAME_HOST_DATE = "same_host_date"
DIMENSION_PARTICIPANT_RESELECTION = "participant_reselection"
DIMENSION_CAPACITY_RELEASE = "capacity_release"
DIMENSION_COUPLED_CROSS_AGE = "coupled_cross_age_exchange"
DIMENSION_BOUNDED_NEIGHBORHOOD = "bounded_neighborhood"

CHEAP_DIMENSIONS: Tuple[str, ...] = (
    DIMENSION_SAME_DATE_START_TIME,
    DIMENSION_SAME_HOST_DATE,
)
SEARCH_DIMENSIONS: Tuple[str, ...] = (
    DIMENSION_PARTICIPANT_RESELECTION,
    DIMENSION_CAPACITY_RELEASE,
    DIMENSION_COUPLED_CROSS_AGE,
    DIMENSION_BOUNDED_NEIGHBORHOOD,
)
SUPPORTED_DIMENSIONS: Tuple[str, ...] = CHEAP_DIMENSIONS + SEARCH_DIMENSIONS

# How a bounded/complete search ended. ``proven_infeasible`` is deliberately
# never produced by this provider (no exhaustive proof capability exists).
SEARCH_OPTION_AVAILABLE = "option_available"
SEARCH_INCOMPLETE = "search_incomplete"
SEARCH_BOUNDED_EXHAUSTED = "bounded_search_exhausted"
SEARCH_PROVEN_INFEASIBLE = "proven_infeasible"

# Bounded neighborhoods: enough alternatives for a controller to choose from
# without turning one obligation into a season-wide scan. issue: a cap tight
# enough to sit inside one same-half season (nearest 8 weekend dates, 2 start
# times) silently never reached a real host-controlled opening -- e.g. a
# movable_busy interval (Kongsberg's "Åpen ishall") clearing an evening slot
# on a date more than 8 weekends out was never even attempted, and reported
# as bounded_search_exhausted/search_incomplete rather than "not yet looked
# at that date". Widened to cover a realistic same-half span while staying a
# bounded (not season-wide-across-both-halves) scan.
_MAX_OPTIONS_PER_OBLIGATION = 8
_MAX_DATE_CANDIDATES = 20
_MAX_RELEASE_DATES = 3
_MAX_BLOCKERS = 2
_MAX_BLOCKER_DATES = 3
_MAX_START_TIMES_PER_DATE = 6

# One canonical identity for this provider's bounded search. It includes the
# supported ladder, every configured cap and the generated start-time policy,
# so widening any of them changes the fingerprint and makes prior
# ``bounded_search_exhausted`` evidence stale/retryable (see
# ``search_capability``). Bump ``UNPLACED_PLACEMENT_SEARCH_VERSION`` when the
# *semantics* change without a parameter change.
UNPLACED_PLACEMENT_SEARCH_VERSION = "3"
UNPLACED_PLACEMENT_CAPABILITY = SearchCapability(
    family="unplaced_placement",
    version=UNPLACED_PLACEMENT_SEARCH_VERSION,
    parameters={
        "dimensions": list(SUPPORTED_DIMENSIONS),
        "max_options_per_obligation": _MAX_OPTIONS_PER_OBLIGATION,
        "max_date_candidates": _MAX_DATE_CANDIDATES,
        "max_release_dates": _MAX_RELEASE_DATES,
        "max_blockers": _MAX_BLOCKERS,
        "max_blocker_dates": _MAX_BLOCKER_DATES,
        "max_start_times_per_date": _MAX_START_TIMES_PER_DATE,
        "generated_start_times": list(GENERATED_START_TIMES),
        "start_time_sampling": "representative_day_coverage",
    },
)


def unplaced_placement_search_capability() -> SearchCapability:
    """Current bounded-search capability for the unplaced-placement ladder."""
    return UNPLACED_PLACEMENT_CAPABILITY


def enumerate_unplaced_placement_repairs(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    finding_ids: Optional[Iterable[str]] = None,
    allow_search: bool = True,
) -> Dict[str, Any]:
    """Return materialization options and coverage for unresolved obligations.

    ``finding_ids`` narrows the enumeration to selected obligations; the full
    bounded search family is only attempted when ``allow_search`` is true, so
    a cheap ``repair-options`` pass reports the untried dimensions explicitly
    instead of silently pretending nothing was left to try.
    """
    fingerprint = candidate_fingerprint(plan)
    selected = _select_obligations(plan, finding_ids)
    options: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    coverage: Dict[str, Any] = {}
    for obligation in selected:
        result = _enumerate_for_obligation(
            plan, problem, obligation, fingerprint, allow_search=allow_search
        )
        options.extend(result["options"])
        rejected.extend(result["rejected_candidates"])
        coverage[str(obligation.get("id") or "")] = result["coverage"]
    return {
        "candidate_fingerprint": fingerprint,
        "options": options,
        "rejected_candidates": rejected,
        "coverage": coverage,
        "dimensions": list(SUPPORTED_DIMENSIONS),
    }


def obligation_search_coverage(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    obligation: Mapping[str, Any],
    *,
    allow_search: bool,
) -> Dict[str, Any]:
    """Cheap coverage view for one obligation (no full materialization run).

    ``list_findings`` must stay cheap, so it reports what the planner already
    recorded plus the dimensions this provider supports. The authoritative
    attempted/untried set for a selected finding comes from
    :func:`enumerate_unplaced_placement_repairs`.

    A planner marker (``bounded_repair_exhausted``) written by an older search
    capability is *stale*, not current evidence: the coverage is rewritten as
    retryable ``search_incomplete`` so canonical maintenance can re-open the
    obligation against the widened search instead of inheriting the old result.
    """
    attempted = _recorded_dimensions(obligation)
    coverage = _coverage(attempted, found_option=False, allow_search=allow_search)
    return _apply_recorded_capability(coverage, obligation)


def _apply_recorded_capability(
    coverage: Dict[str, Any], obligation: Mapping[str, Any]
) -> Dict[str, Any]:
    """Fold the obligation's persisted capability marker into *coverage*.

    Only a persisted bounded-exhaustion claim can become stale; the current
    provider's own resolved coverage is always produced under the current
    capability and is left untouched.
    """
    if not obligation.get("bounded_repair_exhausted"):
        return coverage
    recorded = obligation.get("search_capability")
    if recorded and not capability_is_stale(recorded, UNPLACED_PLACEMENT_CAPABILITY):
        coverage["capability_stale"] = False
        return coverage
    return mark_coverage_stale(
        {**coverage, "status": COVERAGE_BOUNDED_EXHAUSTED},
        current=UNPLACED_PLACEMENT_CAPABILITY,
        reason="recorded_bounded_repair_exhausted_superseded",
    )


def apply_unplaced_placement_repair_option(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    option_id: str,
    expected_fingerprint: str,
    arguments: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Materialize the selected obligation option and verify the whole season.

    ``arguments`` is the selected option's own self-contained mutation plan
    (obligation id, date/start/roster, optional capacity-release moves). When
    it is omitted -- e.g. a direct CLI apply that only has the option id -- the
    obligation is recovered from the option id and re-enumerated, then the
    matching option is applied.
    """
    before = candidate_fingerprint(plan)
    if before != expected_fingerprint:
        return {
            "ok": False,
            "reason": "stale_candidate_fingerprint",
            "before_fingerprint": before,
            "expected_fingerprint": expected_fingerprint,
        }
    if arguments is None:
        obligation_id = _obligation_id_from_option_id(option_id)
        if not obligation_id:
            return {"ok": False, "reason": "unrecognized_option", "before_fingerprint": before}
        repair_set = enumerate_unplaced_placement_repairs(
            plan, problem, finding_ids=[obligation_id], allow_search=True
        )
        option = next(
            (entry for entry in repair_set["options"] if entry["option_id"] == option_id),
            None,
        )
        if option is None:
            return {"ok": False, "reason": "unknown_or_stale_option", "before_fingerprint": before}
        arguments = option.get("arguments") or {}

    obligation_id = str(arguments.get("obligation_id") or "")
    obligation = _find_obligation(plan, obligation_id)
    if obligation is None:
        return {"ok": False, "reason": "unknown_or_stale_obligation", "before_fingerprint": before}

    trial = _materialize(
        plan,
        problem,
        obligation,
        date_iso=str(arguments.get("date") or ""),
        start_time=str(arguments.get("start_time") or ""),
        roster=list(arguments.get("roster") or obligation.get("participant_teams") or []),
        moves=list(arguments.get("moves") or []),
        requires_host_confirmation=bool(arguments.get("requires_host_confirmation")),
        host_confirmation_reason=str(arguments.get("host_confirmation_reason") or ""),
    )
    verification = verify_candidate(trial["candidate"], dict(problem))
    if not verification.get("ok"):
        return {
            "ok": False,
            "reason": "verification_failed",
            "before_fingerprint": before,
            "verification": verification,
        }
    return {
        "ok": True,
        "candidate": trial["candidate"],
        "tournament_id": trial["tournament"]["id"],
        "option_id": option_id,
        "before_fingerprint": before,
        "after_fingerprint": candidate_fingerprint(trial["candidate"]),
        "verification": verification,
        "effects": dict(arguments.get("effects") or {}),
    }


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------


def _enumerate_for_obligation(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    obligation: Mapping[str, Any],
    fingerprint: str,
    *,
    allow_search: bool,
) -> Dict[str, Any]:
    finding_id = str(obligation.get("id") or "")
    options: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    attempted: Set[str] = set()
    seen: Set[Tuple[Any, ...]] = set()

    host_club = str(obligation.get("responsible_host") or obligation.get("host_club") or "")
    original_date = str(obligation.get("date") or "")
    current_roster = _roster(obligation)
    if not finding_id or not host_club or not original_date or len(current_roster) < 2:
        return {
            "options": [],
            "rejected_candidates": [
                {
                    "finding_id": finding_id,
                    "reason": "obligation_missing_placement_facts",
                }
            ],
            "coverage": _coverage(set(), found_option=False, allow_search=allow_search),
        }

    occupancy = _occupancy(plan)
    blocked_reasons: Dict[str, int] = {}

    def record_option(option: RepairOption, key: Tuple[Any, ...]) -> None:
        if key in seen or len(options) >= _MAX_OPTIONS_PER_OBLIGATION:
            return
        seen.add(key)
        options.append(option.to_dict())

    def attempt(
        *,
        date_iso: str,
        start_time: str,
        dimension: str,
        moves: Optional[List[Dict[str, Any]]] = None,
        reselect: bool = False,
    ) -> None:
        roster = current_roster
        roster_source = "current"
        if reselect:
            roster = _reselect_roster(problem, obligation, date_iso, occupancy)
            roster_source = "alternate"
            if roster is None:
                blocked_reasons["no_host_representing_alternate_roster"] = (
                    blocked_reasons.get("no_host_representing_alternate_roster", 0) + 1
                )
                return
        movable = _movable_facts(problem, host_club, date_iso, start_time, obligation)
        on_date = _parse_date(date_iso)
        duration = _duration_minutes(_obligation_tournament(obligation), problem)
        if on_date is not None and external_calendar_conflict(
            _effective_busy_intervals(problem, plan), host_club, on_date, start_time, duration
        ):
            # The verifier surfaces an external conflict as a non-blocking
            # manual item, but materialization must not present a known
            # double-booking as a verified placement.
            blocked_reasons["external_calendar_conflict"] = (
                blocked_reasons.get("external_calendar_conflict", 0) + 1
            )
            rejected.append(
                {
                    "finding_id": finding_id,
                    "host_club": host_club,
                    "age_group": obligation.get("age_group"),
                    "date": date_iso,
                    "start_time": start_time,
                    "roster_source": roster_source,
                    "dimension": dimension,
                    "reason": "external_calendar_conflict",
                    "moves": [dict(move) for move in (moves or [])],
                }
            )
            return
        trial = _materialize(
            plan,
            problem,
            obligation,
            date_iso=date_iso,
            start_time=start_time,
            roster=roster,
            moves=moves or [],
            requires_host_confirmation=bool(movable.get("requires_host_confirmation")),
            host_confirmation_reason=str(movable.get("host_action_required") or ""),
        )
        verification = verify_candidate(trial["candidate"], dict(problem))
        if not verification.get("ok"):
            reason = _primary_reason(verification)
            blocked_reasons[reason] = blocked_reasons.get(reason, 0) + 1
            rejected.append(
                {
                    "finding_id": finding_id,
                    "host_club": host_club,
                    "age_group": obligation.get("age_group"),
                    "date": date_iso,
                    "start_time": start_time,
                    "roster_source": roster_source,
                    "dimension": dimension,
                    "reason": reason,
                    "violations": _codes(verification),
                    "moves": [dict(move) for move in (moves or [])],
                }
            )
            return
        action = _action_for(dimension, moves or [])
        arguments = {
            "obligation_id": finding_id,
            "action": action,
            "date": date_iso,
            "start_time": start_time,
            "roster": [dict(team) for team in roster],
            "roster_source": roster_source,
            "moves": [dict(move) for move in (moves or [])],
            "requires_host_confirmation": bool(movable.get("requires_host_confirmation")),
            "host_confirmation_reason": str(movable.get("host_action_required") or ""),
            "effects": {
                "obligation_resolved": 1,
                "materialized_tournament_id": trial["tournament"]["id"],
                "roster_source": roster_source,
                "moved_tournament_ids": [move["tournament_id"] for move in (moves or [])],
            },
        }
        option = RepairOption(
            option_id=(
                f"{fingerprint[:12]}:{finding_id}:materialize:{dimension}:"
                f"{_slug((date_iso, start_time, roster_source, tuple(move['tournament_id'] for move in (moves or []))))}"
            ),
            finding_id=finding_id,
            action=action,
            tournament_id="",
            arguments=arguments,
            hard_feasible=True,
            effects=dict(arguments["effects"]),
            evidence={
                "verification_ok": True,
                "responsible_host": host_club,
                "age_group": obligation.get("age_group"),
                "date": date_iso,
                "start_time": start_time,
                "end_time": _end_time(start_time, _duration_minutes(trial["tournament"], problem)),
                "roster_source": roster_source,
                "moves": [dict(move) for move in (moves or [])],
                "source_obligation": finding_id,
                "dimension": dimension,
                **movable,
            },
        )
        record_option(
            option,
            (
                dimension,
                date_iso,
                start_time,
                roster_source,
                tuple(sorted(move["tournament_id"] for move in (moves or []))),
                tuple(sorted((move["tournament_id"], move["date"], move["start_time"]) for move in (moves or []))),
            ),
        )

    # 1. Same source date, alternative verified start time.
    attempted.add(DIMENSION_SAME_DATE_START_TIME)
    for start_time in _start_times_for(obligation):
        attempt(date_iso=original_date, start_time=start_time, dimension=DIMENSION_SAME_DATE_START_TIME)

    # 2. Another verified season date for the same responsible host.
    attempted.add(DIMENSION_SAME_HOST_DATE)
    alternate_dates = _candidate_dates(problem, original_date)
    for date_iso in alternate_dates:
        for start_time in _bounded_start_times_for(obligation):
            attempt(date_iso=date_iso, start_time=start_time, dimension=DIMENSION_SAME_HOST_DATE)

    # 3. Participant reselection only when the current roster collides. Try it
    #    at each cheap date before escalating to a coupled capacity release.
    if not options and _has_roster_conflicts(occupancy, original_date, current_roster):
        attempted.add(DIMENSION_PARTICIPANT_RESELECTION)
        for start_time in _start_times_for(obligation):
            attempt(
                date_iso=original_date,
                start_time=start_time,
                dimension=DIMENSION_PARTICIPANT_RESELECTION,
                reselect=True,
            )
        for date_iso in alternate_dates:
            for start_time in _bounded_start_times_for(obligation):
                attempt(
                    date_iso=date_iso,
                    start_time=start_time,
                    dimension=DIMENSION_PARTICIPANT_RESELECTION,
                    reselect=True,
                )

    # 4/5. Coupled capacity release: move a scheduled tournament that consumes
    #      the responsible host's arena/time, then materialize into the freed
    #      capacity. The blocker may be another age group (arena/time is the
    #      shared resource), but it always keeps its own host/arena.
    if allow_search and not options:
        attempted.add(DIMENSION_CAPACITY_RELEASE)
        attempted.add(DIMENSION_COUPLED_CROSS_AGE)
        attempted.add(DIMENSION_BOUNDED_NEIGHBORHOOD)
        _capacity_release_options(
            plan,
            problem,
            obligation,
            attempt,
            host_club=host_club,
            original_date=original_date,
            option_count=lambda: len(options),
        )

    covered = _coverage(attempted, found_option=bool(options), allow_search=allow_search)
    if not options:
        rejected.append(
            {
                "finding_id": finding_id,
                "host_club": host_club,
                "age_group": obligation.get("age_group"),
                "date": original_date,
                "reason": "no_verified_materialization",
                "blocked_reasons": dict(sorted(blocked_reasons.items())),
                "search_coverage": covered,
            }
        )
    return {"options": options, "rejected_candidates": rejected, "coverage": covered}


def _capacity_release_options(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    obligation: Mapping[str, Any],
    attempt: Any,
    *,
    host_club: str,
    original_date: str,
    option_count: Any,
) -> None:
    """Try move-blocker + materialize as one coupled, fully verified option.

    The previous cheap pass already failed, so this only runs for an explicit
    bounded search. ``attempt`` records any legal result directly into the
    caller's option list; here we only enumerate the blocker moves that could
    unlock a slot. A blocker may belong to another age group, but it always
    keeps its own host/arena -- only the shared arena/time resource moves.
    """
    host_arena = _club_arena(problem, host_club)
    # Include the source date itself: the production case is a blocker that
    # already occupies the responsible host's arena on the obligation's own
    # date, not only on an alternate date.
    candidate_dates = [original_date, *_candidate_dates(problem, original_date)][
        :_MAX_RELEASE_DATES
    ]
    blocker_dates = _candidate_dates(problem, original_date)[:_MAX_BLOCKER_DATES]
    start_times = _bounded_start_times_for(obligation)
    for date_iso in candidate_dates:
        if option_count() >= _MAX_OPTIONS_PER_OBLIGATION:
            return
        blockers = [
            tournament
            for tournament in plan.get("tournaments") or []
            if not tournament.get("cancelled")
            and str(tournament.get("date")) == date_iso
            and str(tournament.get("arena") or "") == host_arena
        ][:_MAX_BLOCKERS]
        for blocker in blockers:
            for start_time in start_times:
                for new_date in blocker_dates:
                    if new_date == str(blocker.get("date")):
                        continue
                    for new_start in _blocker_start_times(blocker):
                        if option_count() >= _MAX_OPTIONS_PER_OBLIGATION:
                            return
                        # The constrained resource is arena/time, not age
                        # group: a blocker from another age group is labelled
                        # as the explicit cross-age exchange dimension.
                        cross_age = str(blocker.get("age_group") or "") != str(
                            obligation.get("age_group") or ""
                        )
                        attempt(
                            date_iso=date_iso,
                            start_time=start_time,
                            dimension=(
                                DIMENSION_COUPLED_CROSS_AGE
                                if cross_age
                                else DIMENSION_CAPACITY_RELEASE
                            ),
                            moves=[
                                {
                                    "tournament_id": str(blocker.get("id")),
                                    "date": new_date,
                                    "start_time": new_start,
                                    "age_group": str(blocker.get("age_group") or ""),
                                    "host_club": str(blocker.get("host_club") or ""),
                                }
                            ],
                        )


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def _materialize(
    plan: Mapping[str, Any],
    problem: Mapping[str, Any],
    obligation: Mapping[str, Any],
    *,
    date_iso: str,
    start_time: str,
    roster: Sequence[Mapping[str, Any]],
    moves: Sequence[Mapping[str, Any]],
    requires_host_confirmation: bool,
    host_confirmation_reason: str,
) -> Dict[str, Any]:
    candidate = copy.deepcopy(plan)
    _apply_moves(candidate, moves)
    tournament = _build_tournament(
        candidate,
        problem,
        obligation,
        date_iso=date_iso,
        start_time=start_time,
        roster=roster,
        requires_host_confirmation=requires_host_confirmation,
        host_confirmation_reason=host_confirmation_reason,
    )
    candidate.setdefault("tournaments", []).append(tournament)
    _remove_obligation(candidate, str(obligation.get("id") or ""))
    return {"candidate": candidate, "tournament": tournament}


def _apply_moves(candidate: Dict[str, Any], moves: Sequence[Mapping[str, Any]]) -> None:
    by_id = {str(t.get("id")): t for t in candidate.get("tournaments") or []}
    for move in moves:
        tournament = by_id.get(str(move.get("tournament_id")))
        if tournament is None:
            continue
        tournament["date"] = str(move["date"])
        tournament["start_time"] = str(move["start_time"])


def _build_tournament(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    obligation: Mapping[str, Any],
    *,
    date_iso: str,
    start_time: str,
    roster: Sequence[Mapping[str, Any]],
    requires_host_confirmation: bool,
    host_confirmation_reason: str,
) -> Dict[str, Any]:
    age_group = str(obligation.get("age_group") or "")
    host_club = str(obligation.get("responsible_host") or obligation.get("host_club") or "")
    tournament: Dict[str, Any] = {
        "id": _materialized_id(candidate, obligation),
        "date": date_iso,
        "arena": _club_arena(problem, host_club),
        "age_group": age_group,
        "host_club": host_club,
        "teams": [
            {"club": str(team.get("club") or ""), "label": str(team.get("label") or ""), "age_group": str(team.get("age_group") or age_group)}
            for team in roster
        ],
        "games": [],
        "start_time": start_time,
    }
    if requires_host_confirmation:
        tournament["requires_host_confirmation"] = True
        tournament["host_confirmation_reason"] = host_confirmation_reason
    _regenerate_games(tournament, problem)
    return tournament


def _materialized_id(candidate: Mapping[str, Any], obligation: Mapping[str, Any]) -> str:
    existing = {str(t.get("id")) for t in candidate.get("tournaments") or []}
    source = str(obligation.get("source_tournament_id") or "").strip()
    if source and source not in existing:
        return source
    base = f"mp_{_slug(obligation.get('id'))}"
    candidate_id = base
    suffix = 1
    while candidate_id in existing:
        suffix += 1
        candidate_id = f"{base}_{suffix}"
    return candidate_id


# ---------------------------------------------------------------------------
# Slot / roster evidence
# ---------------------------------------------------------------------------


def _start_times_for(obligation: Mapping[str, Any]) -> List[str]:
    preferred = str(obligation.get("preferred_start_time") or "10:00")
    return _candidate_start_times({"start_time": preferred})


def _bounded_start_times_for(obligation: Mapping[str, Any]) -> List[str]:
    """Preferred start plus a day-covering sample capped at the configured limit.

    An alternate-date/roster/capacity search must not spend its whole start-time
    budget on the first six morning entries: a legal afternoon slot (<= 16:00)
    has to remain reachable within the bound.
    """
    preferred = str(obligation.get("preferred_start_time") or "10:00")
    return bounded_start_times(preferred, _MAX_START_TIMES_PER_DATE)


def _blocker_start_times(blocker: Mapping[str, Any]) -> List[str]:
    return bounded_start_times(str(blocker.get("start_time") or "10:00"), _MAX_START_TIMES_PER_DATE)


def _candidate_dates(problem: Mapping[str, Any], on_date: str) -> List[str]:
    """Nearest-first season weekend dates for the same planning half."""
    anchor = _parse_date(on_date)
    split = _parse_date(problem.get("christmas_split_date"))
    allow_cross_half = bool(problem.get("allow_cross_half_moves"))
    anchor_half = planning_half.tournament_half(anchor, split) if anchor else None
    dates = [
        candidate
        for candidate in season_weekend_dates(problem)
        if candidate != anchor
        and (
            allow_cross_half
            or anchor_half is None
            or planning_half.tournament_half(candidate, split) == anchor_half
        )
    ]
    dates.sort(key=lambda value: (abs((value - anchor).days) if anchor else 0, value))
    return [value.isoformat() for value in dates[:_MAX_DATE_CANDIDATES]]


def _reselect_roster(
    problem: Mapping[str, Any],
    obligation: Mapping[str, Any],
    date_iso: str,
    occupancy: Mapping[str, Set[Tuple[str, str, str]]],
) -> Optional[List[Dict[str, str]]]:
    """A bounded host-representing replacement roster for one date.

    Keeps as many of the obligation's own teams as possible, then fills up to
    the same roster size from registered same-age-group teams that are not
    already playing that date. Host representation and the hard per-club cap
    are enforced; if either cannot be satisfied the dimension yields nothing
    rather than committing an illegal roster.
    """
    age_group = str(obligation.get("age_group") or "")
    host_club = str(obligation.get("responsible_host") or obligation.get("host_club") or "")
    original = _roster(obligation)
    target_size = len(original)
    if target_size < 2 or not age_group:
        return None
    occupied = occupancy.get(date_iso, set())
    selected: List[Dict[str, str]] = []
    selected_ids: Set[Tuple[str, str, str]] = set()
    club_counts: Dict[str, int] = {}

    def club_ok(club: str) -> bool:
        return club_counts.get(club, 0) < HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT

    for team in original:
        identity = _identity(team)
        club = str(team.get("club") or "")
        if identity in occupied or identity in selected_ids or not club_ok(club):
            continue
        selected.append(dict(team))
        selected_ids.add(identity)
        club_counts[club] = club_counts.get(club, 0) + 1

    remaining = target_size - len(selected)
    if remaining > 0:
        pool = [
            team
            for team in problem.get("teams") or []
            if str(team.get("age_group") or "") == age_group
            and _identity(team) not in occupied
            and _identity(team) not in selected_ids
        ]
        # Prefer the clubs already represented (preserves roster balance),
        # then the responsible host so its obligation stays represented.
        pool.sort(
            key=lambda team: (
                0 if str(team.get("club") or "") in club_counts else 1,
                0 if clubs_represent_same_club(str(team.get("club") or ""), host_club) else 1,
                str(team.get("club") or ""),
                str(team.get("label") or ""),
            )
        )
        for team in pool:
            if remaining <= 0:
                break
            club = str(team.get("club") or "")
            if not club_ok(club):
                continue
            selected.append(
                {
                    "club": club,
                    "label": str(team.get("label") or ""),
                    "age_group": age_group,
                }
            )
            selected_ids.add(_identity(team))
            club_counts[club] = club_counts.get(club, 0) + 1
            remaining -= 1

    if len(selected) < target_size:
        return None
    if not any(
        clubs_represent_same_club(str(team.get("club") or ""), host_club) for team in selected
    ):
        return None
    return selected


def _has_roster_conflicts(
    occupancy: Mapping[str, Set[Tuple[str, str, str]]],
    date_iso: str,
    roster: Sequence[Mapping[str, Any]],
) -> bool:
    occupied = occupancy.get(date_iso, set())
    return any(_identity(team) in occupied for team in roster)


def _effective_busy_intervals(
    problem: Mapping[str, Any], plan: Mapping[str, Any]
) -> Mapping[str, Any]:
    return apply_calendar_interpretations(
        problem.get("club_busy_intervals") or {},
        plan.get("calendar_interpretations"),
    )


def _movable_facts(
    problem: Mapping[str, Any],
    host_club: str,
    date_iso: str,
    start_time: str,
    obligation: Mapping[str, Any],
) -> Dict[str, Any]:
    on_date = _parse_date(date_iso)
    if on_date is None:
        return {"availability": "free", "requires_host_confirmation": False}
    duration = _duration_minutes(_obligation_tournament(obligation), problem)
    opportunity = movable_calendar_opportunity(
        problem.get("club_busy_intervals") or {},
        host_club,
        on_date,
        start_time,
        duration,
    )
    if opportunity is None:
        return {"availability": "free", "requires_host_confirmation": False}
    return {
        "availability": "movable_busy",
        "requires_host_confirmation": True,
        "classification_source": opportunity.get("classification_source") or "configured",
        "calendar_event": opportunity.get("calendar_event", ""),
        "host_action_required": opportunity.get("reason")
        or "host-controlled interval may be moved or replaced for an RVV tournament",
    }


def _obligation_tournament(obligation: Mapping[str, Any]) -> Dict[str, Any]:
    duration = obligation.get("required_duration_minutes")
    tournament: Dict[str, Any] = {
        "age_group": str(obligation.get("age_group") or ""),
        "start_time": str(obligation.get("preferred_start_time") or "10:00"),
        "teams": _roster(obligation),
    }
    if isinstance(duration, int) and duration > 0:
        tournament["duration_minutes"] = duration
    return tournament


def _club_arena(problem: Mapping[str, Any], host_club: str) -> str:
    clubs = problem.get("clubs") or {}
    arena = clubs.get(host_club)
    if arena:
        return str(arena)
    for constituent in constituent_clubs(host_club) or [host_club]:
        if clubs.get(constituent):
            return str(clubs[constituent])
    return host_club


def _roster(obligation: Mapping[str, Any]) -> List[Dict[str, str]]:
    return [
        {
            "club": str(team.get("club") or ""),
            "label": str(team.get("label") or ""),
            "age_group": str(team.get("age_group") or obligation.get("age_group") or ""),
        }
        for team in obligation.get("participant_teams") or []
        if isinstance(team, Mapping)
    ]


def _occupancy(plan: Mapping[str, Any]) -> Dict[str, Set[Tuple[str, str, str]]]:
    occupancy: Dict[str, Set[Tuple[str, str, str]]] = {}
    for tournament in plan.get("tournaments") or []:
        if tournament.get("cancelled"):
            continue
        date_iso = str(tournament.get("date") or "")
        if not date_iso:
            continue
        bucket = occupancy.setdefault(date_iso, set())
        for team in tournament.get("teams") or []:
            bucket.add(_identity(team))
    return occupancy


def _identity(team: Mapping[str, Any]) -> Tuple[str, str, str]:
    return _team_identity(dict(team))


# ---------------------------------------------------------------------------
# Selection / coverage
# ---------------------------------------------------------------------------


def _select_obligations(
    plan: Mapping[str, Any], finding_ids: Optional[Iterable[str]]
) -> List[Dict[str, Any]]:
    wanted = {str(item) for item in (finding_ids or []) if item}
    out: List[Dict[str, Any]] = []
    for entry in plan.get("unresolved_tournament_placements") or []:
        if not isinstance(entry, Mapping):
            continue
        finding_id = str(entry.get("id") or "")
        if wanted and finding_id not in wanted:
            continue
        out.append(dict(entry))
    return out


def _find_obligation(plan: Mapping[str, Any], obligation_id: str) -> Optional[Dict[str, Any]]:
    for entry in plan.get("unresolved_tournament_placements") or []:
        if isinstance(entry, Mapping) and str(entry.get("id") or "") == obligation_id:
            return dict(entry)
    return None


def _remove_obligation(plan: Dict[str, Any], obligation_id: str) -> None:
    plan["unresolved_tournament_placements"] = [
        entry
        for entry in plan.get("unresolved_tournament_placements") or []
        if not (isinstance(entry, Mapping) and str(entry.get("id") or "") == obligation_id)
    ]


def _recorded_dimensions(obligation: Mapping[str, Any]) -> Set[str]:
    attempted: Set[str] = set()
    if obligation.get("search_attempted"):
        attempted.add(DIMENSION_SAME_DATE_START_TIME)
    if obligation.get("same_host_dates_checked"):
        attempted.add(DIMENSION_SAME_HOST_DATE)
    if obligation.get("alternate_roster_attempted"):
        attempted.add(DIMENSION_PARTICIPANT_RESELECTION)
    return attempted


def _coverage(attempted: Set[str], *, found_option: bool, allow_search: bool) -> Dict[str, Any]:
    supported = set(SUPPORTED_DIMENSIONS if allow_search else CHEAP_DIMENSIONS)
    attempted = {dimension for dimension in attempted if dimension in supported}
    untried = [dimension for dimension in SUPPORTED_DIMENSIONS if dimension not in attempted]
    if found_option:
        status = SEARCH_OPTION_AVAILABLE
    elif not allow_search or untried:
        status = SEARCH_INCOMPLETE
    else:
        status = SEARCH_BOUNDED_EXHAUSTED
    return {
        "status": status,
        "attempted": [dimension for dimension in SUPPORTED_DIMENSIONS if dimension in attempted],
        "untried": untried,
        "supported": list(SUPPORTED_DIMENSIONS),
        "search_requested": allow_search,
        "proven_infeasible": False,
        # Coverage is only current for the search that produced it; the
        # controller compares this fingerprint against the current capability.
        "capability": UNPLACED_PLACEMENT_CAPABILITY.to_dict(),
    }


def _action_for(dimension: str, moves: Sequence[Mapping[str, Any]]) -> str:
    if moves:
        return "materialize_after_capacity_release"
    if dimension == DIMENSION_SAME_DATE_START_TIME:
        return "materialize_same_date_start_time"
    if dimension == DIMENSION_PARTICIPANT_RESELECTION:
        return "materialize_with_reselected_roster"
    return "materialize_same_host_date"


def _obligation_id_from_option_id(option_id: str) -> str:
    # ``<fingerprint>:unplaced_placement:<age>:<date>:<n>:materialize:...``
    if ":materialize:" not in option_id:
        return ""
    head = option_id.split(":materialize:", 1)[0]
    parts = head.split(":", 1)
    return parts[1] if len(parts) == 2 else ""


def _primary_reason(verification: Mapping[str, Any]) -> str:
    codes = _codes(verification)
    return codes[0] if codes else "verification_failed"


def _codes(verification: Mapping[str, Any]) -> List[str]:
    return [str(violation.get("code")) for violation in verification.get("violations") or []]


__all__ = [
    "CHEAP_DIMENSIONS",
    "DIMENSION_BOUNDED_NEIGHBORHOOD",
    "DIMENSION_CAPACITY_RELEASE",
    "DIMENSION_COUPLED_CROSS_AGE",
    "DIMENSION_PARTICIPANT_RESELECTION",
    "DIMENSION_SAME_DATE_START_TIME",
    "DIMENSION_SAME_HOST_DATE",
    "SEARCH_BOUNDED_EXHAUSTED",
    "SEARCH_INCOMPLETE",
    "SEARCH_OPTION_AVAILABLE",
    "SEARCH_PROVEN_INFEASIBLE",
    "SEARCH_DIMENSIONS",
    "SUPPORTED_DIMENSIONS",
    "UNPLACED_PLACEMENT_CAPABILITY",
    "UNPLACED_PLACEMENT_SEARCH_VERSION",
    "apply_unplaced_placement_repair_option",
    "enumerate_unplaced_placement_repairs",
    "obligation_search_coverage",
    "unplaced_placement_search_capability",
]
