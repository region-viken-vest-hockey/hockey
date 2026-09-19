"""Harness-neutral Stage 3 planning contracts (issue #257).

Defines two stable, machine-readable JSON shapes so multiple planners
(the existing :class:`~tournament_scheduler.season_planner.SeasonPlanner`,
a future generic optimizer, or an LLM/harness-driven planner) can be
verified and scored the same way, independent of any single planner's
internals:

``planning_problem`` (see :func:`build_planning_problem`)
    A normalized snapshot of everything Stage 3 needs as input: roster,
    club/arena mapping, tournament sizing config, manual operator
    adjustments, date preferences, and per-club calendar availability.
    Produced from the same Stage 1 config + Stage 2 checkpoint that
    :mod:`tournament_scheduler.pipeline.stage3_planning` already reads.

``candidate`` (see :func:`candidate_from_plan_dict` / :func:`extract_candidate`)
    A proposed season plan: a list of tournaments with their teams and
    games. This is the same shape Stage 3 already writes to the pipeline
    checkpoint under the ``"plan"`` key (see ``SeasonPlanCodec.to_dict`` in
    ``serialization/season_plan.py``) plus a small schema/source envelope,
    so existing checkpoints can be verified/scored without conversion.

``verify_candidate`` and ``score_candidate`` are pure functions over these
two dicts: no LLM calls, no dependency on ``SeasonPlanner`` internals, so
they can judge candidates produced by any planner implementation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from itertools import combinations
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from tournament_scheduler import planning_half
from tournament_scheduler.calendar_availability import (
    CLASSIFICATION_SOURCE_INFERRED,
    CalendarAvailability,
    interval_availability,
    unclassified_intervals,
)
from tournament_scheduler.canonical_baseline import (
    locked_dates as _canonical_locked_dates,
    pinned_tournament_ids as _canonical_pinned_tournament_ids,
    verify_canonical_locks as _verify_canonical_locks,
)
from tournament_scheduler.date_policy import (
    HOLIDAY_POLICY_SOURCE,
    holiday_exclusions as _holiday_exclusions,
    problem_date_exclusions as _problem_date_exclusions,
)
from tournament_scheduler.host_representation import host_eligible_teams as _host_eligible_teams, host_represented_in as _host_represented_in
from tournament_scheduler.effective_tournament_shape import (
    NO_BYE_EXACT_TEAM_COUNT_BY_AGE_GROUP,
    compute_effective_tournament_shape,
    shape_violation,
)
from tournament_scheduler.operator_waivers import find_participation_waiver
from tournament_scheduler.participation_targets import SEASON_SCOPE, evaluate_participation
from tournament_scheduler.tournament_identity import validate_tournament_identity
from tournament_scheduler.planning_contract_distribution import (
    hosting_fairness as _hosting_fairness,
    month_and_half_distribution as _month_and_half_distribution,
)

PLANNING_PROBLEM_SCHEMA_VERSION = 1
CANDIDATE_SCHEMA_VERSION = 1

# issue #326: the hard ceiling on teams from one club in one tournament.
# Distinct from the separate, lower `max_club_teams_per_tournament`/
# `same_club_excess_over_2` *preference* (issue #324, normally 2) that every
# planning engine still strongly prefers when feasible -- this is the
# absolute maximum no planning engine, verifier or publication gate may ever
# cross, regardless of fairness/host/objective trade-offs.
HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT = 3

# ---------------------------------------------------------------------------
# planning_problem.json
# ---------------------------------------------------------------------------


def build_planning_problem(
    config: Dict[str, Any],
    scraping_result: Optional[Dict[str, Any]],
    start_date: date,
    end_date: date,
    *,
    waivers: Optional[Iterable[Dict[str, Any]]] = None,
    canonical_baseline: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a normalized ``planning_problem`` dict from Stage 1/2 outputs.

    Reuses the same builder helpers Stage 3 already relies on
    (``pipeline/stage3_helpers.py``) so the contract always matches what
    ``SeasonPlanner`` is actually given, rather than drifting out of sync
    with a hand-maintained parallel representation.
    """
    from tournament_scheduler.pipeline.stage3_helpers import (
        _build_club_arenas,
        _build_club_busy_intervals,
        _build_club_calendar_status,
        _build_events_by_club,
        _build_ice_time,
        _build_parallel_games,
        _build_round_length,
        _build_roster,
    )

    roster = _build_roster(config)
    club_arenas = _build_club_arenas(config)
    events_by_club = _build_events_by_club(scraping_result)
    club_calendar_status = _build_club_calendar_status(scraping_result)

    # `participation_targets_by_age_group` (below) is the single authoritative
    # participation-target model for the canonical workbook -- Stage 1
    # rejects a `Lag.target_tournament_count` override before it ever reaches
    # here. `target_tournament_count` per team only appears at all when a
    # caller has set it explicitly (the lower-level/exceptional-override API,
    # never populated from canonical `input.xlsx`), so it is omitted here
    # rather than emitted as a `null` competing field on every team.
    teams = [
        {
            "club": team.club,
            "label": team.label,
            "age_group": team.age_group,
            **(
                {"target_tournament_count": team.target_tournament_count}
                if team.target_tournament_count is not None
                else {}
            ),
        }
        for team in roster.teams
    ]

    manual_adjustments_raw = config.get("manual_adjustments", {}) or {}
    baseline = canonical_baseline if canonical_baseline is not None else config.get("canonical_baseline")
    if baseline is not None and not isinstance(baseline, dict):
        baseline = None
    pinned_tournament_ids = {str(v) for v in (manual_adjustments_raw.get("pinned_tournament_ids", []) or [])}
    locked_dates = {str(d) for d in (manual_adjustments_raw.get("locked_dates", []) or [])}
    if baseline:
        # A canonical baseline's approved/placement-locked tournaments are
        # hard-preserve constraints: pin them so a candidate can never drop
        # them, and mark their dates locked so the date schedule keeps them.
        pinned_tournament_ids.update(_canonical_pinned_tournament_ids(baseline))
        locked_dates.update(_canonical_locked_dates(baseline))
    manual_adjustments = {
        "locked_dates": sorted(locked_dates),
        "banned_dates": sorted(str(d) for d in manual_adjustments_raw.get("banned_dates", []) or []),
        "forced_host_clubs": list(manual_adjustments_raw.get("forced_host_clubs", []) or []),
        "excluded_host_clubs": sorted(manual_adjustments_raw.get("excluded_host_clubs", []) or []),
        "pinned_tournament_ids": sorted(pinned_tournament_ids),
    }

    date_preferences = [
        {"fra": str(p.get("fra")), "til": str(p.get("til")), "vekt": float(p.get("vekt", 0.0))}
        for p in config.get("date_preferences", []) or []
        if isinstance(p, dict)
    ]

    # Canonical holiday/date-admissibility policy: the dates the initial
    # scheduler would never choose are carried explicitly in the normalized
    # problem so every repair/optimizer/verifier path reads the same facts
    # instead of re-deriving (or forgetting) the holiday rule. These are kept
    # separate from operator ``manual_adjustments.banned_dates`` because they
    # are a fixed domain policy, not an operator preference.
    holiday_exclusions = _holiday_exclusions(start_date, end_date)
    date_exclusions = [
        {
            "date": excluded.isoformat(),
            "reason": reason,
            "source": HOLIDAY_POLICY_SOURCE,
        }
        for excluded, reason in sorted(holiday_exclusions.items())
    ]

    club_busy_dates: Dict[str, List[str]] = {}
    for club, events in events_by_club.items():
        dates = sorted({event.datetime.date().isoformat() for event in events})
        if dates:
            club_busy_dates[club] = dates

    split_date = planning_half.christmas_split_date(start_date, end_date)

    club_busy_intervals = _build_club_busy_intervals(scraping_result)

    return {
        "schema_version": PLANNING_PROBLEM_SCHEMA_VERSION,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "christmas_split_date": split_date.isoformat() if split_date else None,
        # issue #293: cross-half date movement is an explicit operator/policy
        # override, never an optimizer side effect -- config-driven so an
        # operator can actually turn it on, since nothing else in the
        # pipeline sets this flag.
        "allow_cross_half_moves": bool(config.get("allow_cross_half_moves", False)),
        "teams": teams,
        "age_groups": roster.age_groups(),
        "clubs": dict(club_arenas),
        "parallel_games": _build_parallel_games(config),
        "rounds_per_tournament": config.get("rounds_per_tournament") or {},
        "round_length_minutes": _build_round_length(config),
        "ice_time_minutes": _build_ice_time(config),
        "max_hosting_deviation": config.get("maxHostingDeviation", 1),
        # Explicit season-wide override only; never populated by the
        # canonical workbook (see the `teams` comment above). Omitted when
        # unset instead of emitted as `null` alongside the authoritative
        # per-age-group field below.
        **(
            {"target_tournament_count": config["target_tournament_count"]}
            if config.get("target_tournament_count") is not None
            else {}
        ),
        "participation_targets_by_age_group": config.get("participation_targets_by_age_group") or {},
        # Optional explicit participation hard maximum -- a genuinely hard rule,
        # never inferred from the target (see participation_targets.py). Omitted
        # unless a caller/config explicitly configures one, so the canonical
        # workbook keeps target-only semantics by default.
        **(
            {"participation_hard_max": config["participation_hard_max"]}
            if isinstance(config.get("participation_hard_max"), int)
            else {}
        ),
        **(
            {"participation_hard_max_by_age_group": config["participation_hard_max_by_age_group"]}
            if isinstance(config.get("participation_hard_max_by_age_group"), dict)
            else {}
        ),
        "manual_adjustments": manual_adjustments,
        # Canonical date-admissibility policy facts (holiday weeks + the
        # weekend before a holiday). Exposed separately from
        # ``manual_adjustments`` so the verifier and every automatic date
        # search share one rule and the audit can surface the active policy.
        "date_exclusions": date_exclusions,
        "date_preferences": date_preferences,
        "club_busy_dates": club_busy_dates,
        "club_calendar_status": club_calendar_status,
        "club_busy_intervals": club_busy_intervals,
        # Read-only exposure of scraped events nothing configured has
        # classified yet: an ambiguous calendar fact the controller may
        # investigate, never an automatic movable/free verdict.
        "unclassified_calendar_events": unclassified_intervals(club_busy_intervals),
        # Canonical Git-backed baseline snapshot (see
        # canonical_baseline.py), when this plan is a baseline-aware replan
        # of a promoted season. Carries the approved/placement-locked
        # snapshot so verify_candidate can hard-reject a candidate that moves
        # or drops a booked tournament. ``None`` for a from-scratch plan.
        "canonical_baseline": baseline,
        # Soft, configurable change-cost policy (issue #355) used when the
        # planner/search folds baseline change cost into its objective. Kept
        # separate from the hard approval locks above: these only influence
        # preference between hard-valid candidates, never correctness.
        "canonical_change_weights": (
            dict(config.get("canonical_change_weights"))
            if isinstance(config.get("canonical_change_weights"), dict)
            else None
        ),
        "canonical_change_cost_scale": config.get("canonical_change_cost_scale"),
        # Explicit operator waivers are carried as part of the frozen problem
        # snapshot so verification stays a pure function over this contract.
        # Never populated by the planner/optimizer/agent path -- only an
        # explicit operator CLI action writes the store this reads from.
        "operator_waivers": [dict(waiver) for waiver in (waivers or [])],
    }


# ---------------------------------------------------------------------------
# candidate.json
# ---------------------------------------------------------------------------


def candidate_from_plan_dict(
    plan_dict: Dict[str, Any],
    *,
    source: str = "SeasonPlanner",
    planner_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Wrap a canonical ``SeasonPlanCodec.to_dict`` payload in the stable candidate envelope."""
    candidate = dict(plan_dict)
    candidate["schema_version"] = CANDIDATE_SCHEMA_VERSION
    candidate["source"] = {"planner": source, "version": planner_version}
    return candidate


def extract_candidate(data: Dict[str, Any]) -> Dict[str, Any]:
    """Return the candidate payload from *data*, whatever shape it was loaded in.

    Accepts a raw candidate dict (top-level ``"tournaments"`` key), a Stage 3
    pipeline checkpoint payload (``{"plan": {...}, "rules_report": ...}``),
    or a full checkpoint file envelope (``{"stage": ..., "data": {"plan":
    ...}}`` as written by ``PipelineState.write_stage``). Always returns a
    dict with a ``"schema_version"`` set so downstream code doesn't need to
    special-case older, unversioned checkpoints.
    """
    if "tournaments" not in data and isinstance(data.get("data"), dict):
        data = data["data"]
    if "tournaments" in data:
        candidate = dict(data)
    elif isinstance(data.get("plan"), dict):
        candidate = dict(data["plan"])
    else:
        raise ValueError("Could not find a candidate plan (expected a 'tournaments' or 'plan' key)")
    candidate.setdefault("schema_version", CANDIDATE_SCHEMA_VERSION)
    return candidate


def candidate_content_fingerprint(candidate: Mapping[str, Any]) -> str:
    """Canonical content fingerprint for one candidate envelope.

    Candidate identity is a lifecycle contract shared by the Stage 3 session
    and every repair/arena provider, so it has exactly one implementation
    here. The schema default is applied before hashing so an unversioned
    legacy checkpoint and the same candidate after ``extract_candidate``
    hashes to one identity rather than two. Callers must hash the same
    candidate body shape (the payload ``extract_candidate`` returns), never a
    wrapper and a body interchangeably.
    """
    normalized = dict(candidate)
    normalized.setdefault("schema_version", CANDIDATE_SCHEMA_VERSION)
    payload = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------


def _parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


TeamIdentity = Tuple[str, str, str]


def _time_to_minutes(value: str) -> int:
    """Parse an ``HH:MM`` clock string into minutes since midnight.

    Accepts ``"24:00"`` (and other hour values >= 24) as the end-of-day
    boundary `pipeline.stage3_helpers._build_club_busy_intervals` emits for
    events running to/through midnight -- ``datetime.time`` itself rejects
    an hour of 24, so this can't just reuse `utils.slot_finder.parse_time`.
    """
    hour, minute = (int(part) for part in value.split(":", 1))
    return hour * 60 + minute


def _busy_windows_for_availability(
    club_busy_intervals: Optional[Dict[str, List[Dict[str, str]]]],
    club: Optional[str],
    on_date: date,
    *,
    availability: Optional[CalendarAvailability] = None,
) -> List[Tuple[int, int]]:
    """Return ``(start_minutes, end_minutes)`` windows of one availability class.

    The canonical, planner-neutral reader over the ``club_busy_intervals``
    evidence a ``planning_problem`` carries. ``availability=None`` returns
    every interval regardless of class. An empty result does NOT mean "free
    all day": callers must additionally confirm the club's
    ``club_calendar_status`` is ``"known"`` before treating the absence of
    entries as evidence of availability.
    """
    if not club_busy_intervals or not club:
        return []
    date_str = on_date.isoformat()
    windows: List[Tuple[int, int]] = []
    for entry in club_busy_intervals.get(club, []):
        if entry.get("date") != date_str:
            continue
        if availability is not None and interval_availability(entry) != availability:
            continue
        try:
            windows.append((_time_to_minutes(entry["start"]), _time_to_minutes(entry["end"])))
        except (KeyError, ValueError):
            continue
    return windows


def fixed_busy_windows(
    club_busy_intervals: Optional[Dict[str, List[Dict[str, str]]]],
    club: Optional[str],
    on_date: date,
) -> List[Tuple[int, int]]:
    """Return *club*'s ``fixed_busy`` (cannot displace) windows on *on_date*."""
    return _busy_windows_for_availability(
        club_busy_intervals, club, on_date, availability=CalendarAvailability.FIXED_BUSY
    )


def movable_busy_windows(
    club_busy_intervals: Optional[Dict[str, List[Dict[str, str]]]],
    club: Optional[str],
    on_date: date,
) -> List[Tuple[int, int]]:
    """Return *club*'s ``movable_busy`` (host-controlled) windows on *on_date*."""
    return _busy_windows_for_availability(
        club_busy_intervals, club, on_date, availability=CalendarAvailability.MOVABLE_BUSY
    )


def _overlaps_any(windows: List[Tuple[int, int]], start_time: Optional[str], duration_minutes: int) -> bool:
    if not start_time or duration_minutes <= 0:
        return False
    try:
        new_start = _time_to_minutes(start_time)
    except ValueError:
        return False
    new_end = new_start + duration_minutes
    for busy_start, busy_end in windows:
        if new_start < busy_end and busy_start < new_end:
            return True
    return False


def external_busy_windows(
    club_busy_intervals: Optional[Dict[str, List[Dict[str, str]]]],
    club: Optional[str],
    on_date: date,
    *,
    kind: Optional[str] = "external",
) -> List[Tuple[int, int]]:
    """Backward-compatible busy-window reader keyed on the legacy ``kind`` tag.

    Prefer :func:`fixed_busy_windows`/:func:`movable_busy_windows`, which read
    the authoritative ``availability`` field directly. ``kind="external"``
    maps to ``fixed_busy``, ``kind="club_controlled"`` to ``movable_busy`` and
    ``kind=None`` to every interval -- the previous behavior, preserved for
    callers that predate the explicit availability model.
    """
    availability: Optional[CalendarAvailability]
    if kind == "external":
        availability = CalendarAvailability.FIXED_BUSY
    elif kind == "club_controlled":
        availability = CalendarAvailability.MOVABLE_BUSY
    elif kind is None:
        availability = None
    else:
        # An unknown legacy kind: fall back to a raw kind filter so a caller
        # passing something else still gets exactly the old semantics.
        if not club_busy_intervals or not club:
            return []
        date_str = on_date.isoformat()
        windows: List[Tuple[int, int]] = []
        for entry in club_busy_intervals.get(club, []):
            if entry.get("date") != date_str or entry.get("kind", "external") != kind:
                continue
            try:
                windows.append((_time_to_minutes(entry["start"]), _time_to_minutes(entry["end"])))
            except (KeyError, ValueError):
                continue
        return windows
    return _busy_windows_for_availability(club_busy_intervals, club, on_date, availability=availability)


def external_calendar_conflict(
    club_busy_intervals: Optional[Dict[str, List[Dict[str, str]]]],
    club: Optional[str],
    on_date: date,
    start_time: Optional[str],
    duration_minutes: int,
) -> bool:
    """True if ``[start_time, start_time + duration_minutes)`` on *on_date*
    overlaps any of *club*'s ``fixed_busy`` windows.

    Only genuine external commitments count. A ``movable_busy`` interval
    (host-controlled ice the club itself may displace) is never a
    hard conflict -- see :func:`movable_calendar_opportunity` for the
    non-blocking counterpart.
    """
    if not club:
        return False
    return _overlaps_any(fixed_busy_windows(club_busy_intervals, club, on_date), start_time, duration_minutes)


def movable_calendar_opportunity(
    club_busy_intervals: Optional[Dict[str, List[Dict[str, str]]]],
    club: Optional[str],
    on_date: date,
    start_time: Optional[str],
    duration_minutes: int,
) -> Optional[Dict[str, str]]:
    """Return the ``movable_busy`` interval the tournament interval overlaps.

    Non-blocking counterpart to :func:`external_calendar_conflict`. A club
    placed inside its own host-controlled interval is feasible, but the
    placement displaces an existing (movable) event and therefore carries an
    explicit host-confirmation requirement. Returning the raw interval lets
    the caller/evidence explain *which* event must be moved instead of only
    reporting an opaque flag.
    """
    if not club or not start_time or duration_minutes <= 0:
        return None
    try:
        new_start = _time_to_minutes(start_time)
    except ValueError:
        return None
    new_end = new_start + duration_minutes
    for entry in club_busy_intervals.get(club, []) if club_busy_intervals else []:
        if entry.get("date") != on_date.isoformat():
            continue
        if interval_availability(entry) != CalendarAvailability.MOVABLE_BUSY:
            continue
        try:
            busy_start = _time_to_minutes(entry["start"])
            busy_end = _time_to_minutes(entry["end"])
        except (KeyError, ValueError):
            continue
        if new_start < busy_end and busy_start < new_end:
            return dict(entry)
    return None


def club_controlled_allocation_conflict(
    club_busy_intervals: Optional[Dict[str, List[Dict[str, str]]]],
    club: Optional[str],
    on_date: date,
    start_time: Optional[str],
    duration_minutes: int,
) -> bool:
    """Backward-compatible boolean form of :func:`movable_calendar_opportunity`."""
    return (
        movable_calendar_opportunity(
            club_busy_intervals, club, on_date, start_time, duration_minutes
        )
        is not None
    )


def apply_calendar_interpretations(
    club_busy_intervals: Optional[Dict[str, List[Dict[str, str]]]],
    interpretations: Optional[Iterable[Mapping[str, Any]]],
) -> Dict[str, List[Dict[str, str]]]:
    """Overlay controller-requested *inferred* availability interpretations.

    A candidate may carry ``calendar_interpretations`` recording that a
    specific *unclassified* scraped event is, in the controller's judgment,
    host-controlled movable capacity. This does **not** mutate the Stage 2
    source calendar: it re-applies the interpretation here, for verification
    only, so the raw scraped fact stays whatever the source said. The
    resulting interval becomes ``movable_busy`` with
    ``classification_source: "inferred"`` and an explicit host-confirmation
    requirement, which is exactly how the verifier already treats a
    configured movable interval -- an inferred fact can never look like
    confirmed free ice.

    Only an interval whose current class is still ambiguous can be
    reinterpreted: eligibility is re-derived from the event title through the
    same per-club rules used during normalization, so a configured
    ``fixed_busy`` or ``movable_busy`` interval is never overwritten and an
    imported booking cannot be turned into movable capacity by candidate data
    alone. A ``fixed_busy`` interval with no configured rule stays
    ``fixed_busy`` (never assumed free) but is eligible for investigation.
    """
    entries = interpretations or ()
    if not entries or not club_busy_intervals:
        return club_busy_intervals or {}
    from .calendar_availability import is_unclassified_event

    merged: Dict[str, List[Dict[str, str]]] = {
        club: [dict(entry) for entry in club_entries]
        for club, club_entries in club_busy_intervals.items()
    }
    for interpretation in entries:
        if not isinstance(interpretation, Mapping):
            continue
        club = str(interpretation.get("club") or "")
        if not club or club not in merged:
            continue
        for entry in merged[club]:
            title = str(entry.get("calendar_event") or "")
            if not title or not is_unclassified_event(club, title):
                continue
            if interpretation.get("date") and entry.get("date") != interpretation.get("date"):
                continue
            if interpretation.get("calendar_event") and entry.get("calendar_event") != interpretation.get("calendar_event"):
                continue
            if interpretation.get("start") and entry.get("start") != interpretation.get("start"):
                continue
            entry["availability"] = CalendarAvailability.MOVABLE_BUSY.value
            entry["kind"] = "club_controlled"
            entry["classification_source"] = CLASSIFICATION_SOURCE_INFERRED
            entry["requires_host_confirmation"] = "true"
            reason = str(interpretation.get("reason") or "").strip()
            if reason:
                entry["reason"] = reason
            elif not entry.get("reason"):
                entry["reason"] = (
                    "controller-inferred host-controlled interval; host confirmation required"
                )
    return merged


def _team_identity(team: Dict[str, Any]) -> TeamIdentity:
    """Return a (club, label, age_group) identity for *team*.

    Team labels (e.g. "Ringerike 1") are only unique *within* an age group's
    roster — the same label is routinely reused across age groups (and
    occasionally across clubs), so any cross-tournament bookkeeping (season
    participation counts, per-team dates, opponent-pair history) must key on
    the full triple, not the label alone, or unrelated teams that happen to
    share a label get merged together. See `team_key()` in `models.py` for
    the same disambiguation applied to display strings.
    """
    return (team.get("club", ""), team.get("label", ""), team.get("age_group", ""))


def _display_label(identity: TeamIdentity, duplicate_labels: "set[str]") -> str:
    club, label, age_group = identity
    if label in duplicate_labels:
        return f"{label} ({club}, {age_group})"
    return label


def _duplicate_labels(tournaments: Iterable[Dict[str, Any]]) -> "set[str]":
    seen: Dict[str, set[TeamIdentity]] = {}
    for t in tournaments:
        for team in t.get("teams", []):
            identity = _team_identity(team)
            seen.setdefault(identity[1], set()).add(identity)
    return {label for label, identities in seen.items() if len(identities) > 1}


def _participation_search_evidence(
    problem: Optional[Mapping[str, Any]],
) -> Optional[Mapping[TeamIdentity, Any]]:
    """Return the per-team search/acceptance evidence carried by *problem*.

    Canonical season maintenance injects persisted operator acceptances here so
    the same independent verifier that classifies every other deviation also
    honours an explicit ``operator_accepted`` decision. The field is optional:
    an ordinary planner problem carries no acceptances and keeps the exact
    previous classification behaviour.
    """
    if not isinstance(problem, Mapping):
        return None
    evidence = problem.get("participation_search_evidence")
    return evidence if isinstance(evidence, Mapping) else None


def verify_candidate(
    candidate: Dict[str, Any],
    problem: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Deterministically check *candidate* against hard planning requirements.

    Does not depend on an LLM or on ``SeasonPlanner`` internals — only on
    the stable candidate/problem contracts. When *problem* is omitted,
    only self-consistency checks are run (duplicate participation, arena
    double-booking, tournament roster sanity); checks that need the
    original planning inputs (registered teams, calendar validity,
    participation targets, manual restrictions) are skipped and reported
    as such.
    """
    violations: List[Dict[str, Any]] = []
    # Hard violations a matching, active operator waiver explicitly authorized.
    # Kept separate from `violations` so they never block export, but never
    # silently disappear either -- they are surfaced in the audit/evidence.
    waived_violations: List[Dict[str, Any]] = []
    skipped: List[str] = []
    # issue #264: non-blocking record of tournaments placed inside a
    # host-controlled ``movable_busy`` interval rather than an
    # unconditionally free one -- feasible, but it displaces an existing
    # event and therefore carries an explicit host-confirmation requirement
    # the evidence bundle/audit must show.
    movable_allocations_used: List[Dict[str, Any]] = []
    # Non-blocking record of tournaments hosted by a club with no
    # trustworthy calendar evidence this run -- surfaced for manual
    # placement (see hosting_coverage.py's unresolved_hosting_obligations
    # for the equivalent pattern) rather than hard-blocking the candidate,
    # since scheduler.py/host_assignment.py already let such a club host
    # its fair share with a provisional slot.
    manual_calendar_placements: List[Dict[str, str]] = []
    # Non-blocking record of tournaments whose host has a real external
    # calendar conflict the optimizer couldn't route around -- surfaced for
    # manual placement rather than hard-blocking the candidate.
    manual_external_conflict_placements: List[Dict[str, str]] = []
    # Non-blocking record of teams short of (or over) their target
    # tournament count -- surfaced for manual placement (e.g. an operator
    # manually arranging an extra game) rather than hard-blocking.
    manual_participation_placements: List[Dict[str, str]] = []

    tournaments = [t for t in candidate.get("tournaments", []) if not t.get("cancelled")]
    violations.extend(validate_tournament_identity(candidate))

    # Deterministic approval diagnostics: an approval whose
    # protected-fields fingerprint no longer matches the tournament is a
    # stale approval -- non-blocking for correctness (the plan can still be
    # hard-valid) but surfaced so it can never masquerade as an approved
    # placement, and publication readiness reports re-review.
    _canonical_baseline = (problem or {}).get("canonical_baseline") or {}
    stale_approvals: List[Dict[str, Any]] = [
        dict(entry) for entry in (_canonical_baseline.get("stale_approvals") or [])
    ]
    orphaned_approvals: List[Dict[str, Any]] = [
        dict(entry) for entry in (_canonical_baseline.get("orphaned_approvals") or [])
    ]

    def _violate(
        code: str,
        message: str,
        tournament_id: Optional[str] = None,
        **extra: Any,
    ) -> None:
        entry: Dict[str, Any] = {"code": code, "message": message}
        if tournament_id is not None:
            entry["tournament_id"] = tournament_id
        entry.update(extra)
        violations.append(entry)

    # --- self-consistent checks (no problem required) ----------------------

    duplicate_labels = _duplicate_labels(tournaments)
    team_dates: Dict[TeamIdentity, List[Tuple[date, str]]] = {}
    participations: Dict[TeamIdentity, int] = {}
    # Effective-shape rule: per-tournament-id count of rounds with a bye/rest, deferred
    # here and consumed by the problem-dependent shape check below (which
    # needs the full registered pool to judge avoidable vs input-constrained).
    bye_round_shapes: Dict[str, int] = {}

    for t in tournaments:
        t_id = t.get("id", "?")
        t_date = _parse_date(t.get("date"))
        if t_date is None:
            _violate("invalid_date", f"Tournament {t_id} has a missing/invalid date", t_id)
            continue

        seen_identities: set[TeamIdentity] = set()
        club_counts_this_tournament: Dict[str, int] = {}
        for team in t.get("teams", []):
            if not team.get("label"):
                continue
            identity = _team_identity(team)
            display = _display_label(identity, duplicate_labels)
            if identity in seen_identities:
                _violate(
                    "duplicate_team_in_tournament",
                    f"Team {display!r} appears twice in tournament {t_id}",
                    t_id,
                )
            seen_identities.add(identity)
            if team.get("age_group") and team.get("age_group") != t.get("age_group"):
                _violate(
                    "age_group_mismatch",
                    f"Team {display!r} ({team.get('age_group')}) does not match tournament "
                    f"{t_id} age group {t.get('age_group')}",
                    t_id,
                )
            participations[identity] = participations.get(identity, 0) + 1
            team_dates.setdefault(identity, []).append((t_date, t_id))
            club = identity[0]
            if club:
                club_counts_this_tournament[club] = club_counts_this_tournament.get(club, 0) + 1

        team_count = len(t.get("teams", []))
        team_labels = [
            str(team.get("label"))
            for team in t.get("teams", [])
            if isinstance(team, dict) and team.get("label")
        ]
        played_by_round: Dict[int, set[str]] = {}
        for game in t.get("games", []) or []:
            try:
                round_number = int(game.get("round_number") or 0)
            except (AttributeError, TypeError, ValueError):
                continue
            if round_number <= 0:
                continue
            for side in ("home", "away"):
                label = game.get(side)
                if label:
                    played_by_round.setdefault(round_number, set()).add(str(label))
        bye_rounds = {
            round_number: [label for label in team_labels if label not in playing]
            for round_number, playing in played_by_round.items()
            if any(label not in playing for label in team_labels)
        }
        # Effective-shape rule: without a `problem`, the complete registered team pool
        # for the age group is unknowable, so avoidable vs input-constrained
        # scarcity can't be distinguished -- fall back to the unconditional
        # "even, no bye, exact U12/JU12" rule. When a `problem` is supplied,
        # the shape-aware check below (using the real registered pool)
        # replaces this instead.
        if problem is None:
            required_team_count = NO_BYE_EXACT_TEAM_COUNT_BY_AGE_GROUP.get(str(t.get("age_group") or ""))
            invalid_exact_count = required_team_count is not None and team_count != required_team_count
            if team_count % 2 == 1 or bye_rounds or invalid_exact_count:
                requirement = (
                    f"exactly {required_team_count} teams"
                    if required_team_count is not None
                    else "an even number of teams"
                )
                _violate(
                    "bye_team_not_allowed",
                    f"Tournament {t_id} ({t.get('age_group')}) has {team_count} teams; "
                    f"tournaments must have {requirement} and no pause/bye rounds",
                    t_id,
                )
        else:
            bye_round_shapes.setdefault(t_id, len(bye_rounds))

        # issue #326: a hard ceiling, independent of any fairness/host/
        # objective trade-off -- 4+ teams from one club in one tournament is
        # never valid, unlike the separate 2-team soft preference.
        for club, count in club_counts_this_tournament.items():
            if count > HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT:
                _violate(
                    "club_hard_max_exceeded",
                    f"Tournament {t_id} has {count} teams from club {club!r}, "
                    f"exceeding the hard maximum of {HARD_MAX_CLUB_TEAMS_PER_TOURNAMENT}",
                    t_id,
                )

    for identity, entries in team_dates.items():
        display = _display_label(identity, duplicate_labels)
        by_date: Dict[date, List[str]] = {}
        for t_date, t_id in entries:
            by_date.setdefault(t_date, []).append(t_id)
        for t_date, ids in by_date.items():
            distinct_ids = sorted(set(ids))
            # A team appearing twice in *one* tournament is already reported as
            # ``duplicate_team_in_tournament``; only two or more distinct
            # tournaments on the same date are a genuine double-booking. The
            # structured team/date/tournament evidence lets a repair provider
            # route a stable finding without parsing the message.
            if len(distinct_ids) > 1:
                _violate(
                    "duplicate_participation_same_date",
                    f"Team {display!r} is scheduled in {len(distinct_ids)} tournaments on {t_date.isoformat()}: {distinct_ids}",
                    team=list(identity),
                    date=t_date.isoformat(),
                    tournament_ids=distinct_ids,
                )

    if problem is None:
        skipped.extend(
            [
                "registered_teams",
                "valid_slot_window",
                "tournament_capacity",
                "participation_targets",
                "manual_restrictions",
                "calendar_validity",
                "arena_interval_conflicts",
                "external_calendar_conflicts",
            ]
        )
        return {
            "ok": not violations,
            "violations": violations,
            "skipped": skipped,
            "club_controlled_allocations_used": movable_allocations_used,
            "movable_allocations_used": movable_allocations_used,
            "calendar_interpretations_used": [],
            "unresolved_hosting_obligations": [],
            "manual_calendar_placements": [],
            "manual_external_conflict_placements": [],
            "manual_participation_placements": [],
            "participation_deviations": [],
            "participation_metrics": {},
            "participation_club_pools": [],
            "participation_club_pool_shortfalls": [],
            "input_constrained_shapes": [],
            "stale_approvals": stale_approvals,
            "orphaned_approvals": orphaned_approvals,
        }

    # --- problem-dependent checks -------------------------------------------

    valid_teams = {
        (t["club"], t["label"], t["age_group"]) for t in problem.get("teams", [])
    }

    # Effective-shape rule: the complete canonical registered pool per age group --
    # never derived from the candidate's own tournaments, which would let a
    # planner manufacture scarcity by selecting too few of the available
    # registered teams.
    registered_count_by_age_group: Dict[str, int] = {}
    for team in problem.get("teams", []):
        ag = team.get("age_group")
        if ag:
            registered_count_by_age_group[ag] = registered_count_by_age_group.get(ag, 0) + 1
    rounds_per_tournament = problem.get("rounds_per_tournament") or {}
    parallel_games_capacity = problem.get("parallel_games") or {}
    input_constrained_shapes: List[Dict[str, Any]] = []

    window_start = _parse_date(problem.get("start_date"))
    window_end = _parse_date(problem.get("end_date"))

    # Per-half participation needs the same before/after-Christmas
    # boundary `score_candidate` uses below, so a shortfall/overage can be
    # attributed to the half it actually happened in.
    split_date: Optional[date] = None
    if problem.get("christmas_split_date"):
        split_date = _parse_date(problem["christmas_split_date"])
    elif window_start and window_end:
        split_date = planning_half.christmas_split_date(window_start, window_end)
    elif tournaments:
        dated = [d for d in (_parse_date(t.get("date")) for t in tournaments) if d is not None]
        if dated:
            split_date = planning_half.christmas_split_date(min(dated), max(dated))
    participation_ids_by_half: Dict[str, Dict[TeamIdentity, List[str]]] = {
        "before_christmas": {},
        "after_christmas": {},
    }
    for t in tournaments:
        t_date = _parse_date(t.get("date"))
        if t_date is None:
            continue
        half = planning_half.tournament_half(t_date, split_date)
        if half not in participation_ids_by_half:
            continue
        t_id = str(t.get("id", "?"))
        for team in t.get("teams", []):
            identity = _team_identity(team)
            participation_ids_by_half[half].setdefault(identity, []).append(t_id)

    # Arena occupancy is a full datetime interval (start_time + computed
    # duration), not just an arena/date pair — arenas routinely host more
    # than one age group's tournament in a day back-to-back. Reuse the same
    # planner-independent interval-collision logic Stage 3/4 already share
    # (`arena_conflicts.py`) instead of a naive same-arena/same-date check,
    # which would flag normal same-day scheduling as a false positive.
    from tournament_scheduler.arena_conflicts import find_arena_interval_collisions, tournament_interval
    from tournament_scheduler.serialization.season_plan import tournament_from_dict

    ice_time_minutes = problem.get("ice_time_minutes") or problem.get("round_length_minutes") or {}
    club_calendar_status_for_conflicts = problem.get("club_calendar_status") or {}
    club_busy_intervals = apply_calendar_interpretations(
        problem.get("club_busy_intervals") or {},
        candidate.get("calendar_interpretations"),
    )
    try:
        tournament_objs = [tournament_from_dict(t) for t in candidate.get("tournaments", []) if not t.get("cancelled")]
        for collision in find_arena_interval_collisions(tournament_objs, ice_time_minutes):
            _violate(
                "arena_interval_conflict",
                collision["message"],
                collision.get("tournament_id"),
            )
        # issue #264 P0: a "known" calendar status only proves the host club
        # was scraped this run -- it does NOT prove every candidate start
        # time on a given date is free. Independently check each
        # tournament's actual hall interval against the host's real busy
        # windows, so a host/date/time combination the candidate's own
        # planner never validated against real evidence (e.g. a v2 optimizer
        # move) is still caught here rather than only by re-running the
        # planner that produced it.
        for tournament_obj in tournament_objs:
            interval = tournament_interval(tournament_obj, ice_time_minutes)
            if interval is None or not interval.host_club:
                continue
            if (
                club_calendar_status_for_conflicts
                and club_calendar_status_for_conflicts.get(interval.host_club, "unknown") != "known"
            ):
                continue  # already surfaced in manual_calendar_placements below
            duration_minutes = int((interval.end - interval.start).total_seconds() // 60)
            if external_calendar_conflict(
                club_busy_intervals,
                interval.host_club,
                interval.start.date(),
                interval.start.strftime("%H:%M"),
                duration_minutes,
            ):
                # Non-blocking: a genuine external double-booking is real,
                # but the optimizer can't always route around it within its
                # search budget. Surfaced for manual placement instead of
                # hard-blocking the whole candidate (mirrors
                # manual_calendar_placements/unresolved_hosting_obligations).
                manual_external_conflict_placements.append(
                    {
                        "tournament_id": interval.tournament_id,
                        "host_club": interval.host_club,
                        "age_group": interval.age_group,
                        "date": interval.date,
                    }
                )
            elif (opportunity := movable_calendar_opportunity(
                club_busy_intervals,
                interval.host_club,
                interval.start.date(),
                interval.start.strftime("%H:%M"),
                duration_minutes,
            )) is not None:
                # A movable interval is a candidate placement for
                # the responsible host, never a fixed external conflict. It
                # is feasible but displaces an existing host-controlled event
                # (e.g. open ice), so it carries an explicit
                # host-confirmation requirement instead of silently becoming
                # "unconditionally free".
                movable_allocations_used.append(
                    {
                        "tournament_id": interval.tournament_id,
                        "host_club": interval.host_club,
                        "age_group": interval.age_group,
                        "date": interval.date,
                        "interval": interval.interval_label,
                        "availability": CalendarAvailability.MOVABLE_BUSY.value,
                        "classification_source": opportunity.get("classification_source")
                        or "configured",
                        "calendar_event": opportunity.get("calendar_event", ""),
                        "reason": opportunity.get("reason")
                        or "host-controlled interval may be moved or replaced for an RVV tournament",
                        "requires_host_confirmation": True,
                    }
                )
    except (KeyError, ValueError) as exc:
        _violate("arena_interval_check_failed", f"Could not evaluate arena interval conflicts: {exc}")

    parallel_games = problem.get("parallel_games") or {}
    # Note: `club_busy_dates` (in the problem contract) is intentionally not
    # used for a "host already busy" hard check here — a club's own scraped
    # calendar almost always shows the very tournament it is hosting, so a
    # naive date-membership check is a near-universal false positive. Real
    # host/calendar conflict detection needs event-level matching (is the
    # busy event actually a *different* commitment?), which the current
    # per-club date-only export can't distinguish. Left as future work.

    manual = problem.get("manual_adjustments") or {}
    banned_dates = {_parse_date(d) for d in manual.get("banned_dates", [])}
    locked_dates = {_parse_date(d) for d in manual.get("locked_dates", [])}
    excluded_host_clubs = set(manual.get("excluded_host_clubs", []))
    pinned_ids = set(manual.get("pinned_tournament_ids", []))
    # Canonical date-admissibility policy: derived independently from the
    # planning window (and any explicit policy exclusions the problem
    # carries), not trusted from the planner that produced the candidate. A
    # candidate that a later optimizer/repair moved onto an excluded holiday
    # date therefore fails final verification regardless of which path
    # created it.
    policy_excluded_dates = _problem_date_exclusions(problem)

    scheduled_dates: set[date] = set()
    for t in tournaments:
        t_id = t.get("id", "?")
        t_date = _parse_date(t.get("date"))
        if t_date is None:
            continue
        scheduled_dates.add(t_date)

        if window_start and window_end and not (window_start <= t_date <= window_end):
            _violate(
                "date_outside_window",
                f"Tournament {t_id} on {t_date.isoformat()} is outside the planning "
                f"window {window_start.isoformat()}..{window_end.isoformat()}",
                t_id,
            )

        if t_date in policy_excluded_dates:
            _violate(
                "holiday_date_used",
                f"Tournament {t_id} is scheduled on excluded holiday date "
                f"{t_date.isoformat()} ({policy_excluded_dates[t_date]})",
                t_id,
                date=t_date.isoformat(),
                reason=policy_excluded_dates[t_date],
            )

        if t_date in banned_dates:
            _violate("banned_date_used", f"Tournament {t_id} is scheduled on banned date {t_date.isoformat()}", t_id)

        host_club = t.get("host_club")
        if host_club and host_club in excluded_host_clubs:
            _violate(
                "excluded_host_club_used",
                f"Tournament {t_id} is hosted by excluded club {host_club!r}",
                t_id,
            )

        # Effective-shape rule: replaces the unconditional "even, no bye, exact
        # U12/JU12" rule above (problem-less branch only) with a check
        # against the shape the complete registered pool actually supports
        # -- an avoidable bye/underscheduling (the pool could support a
        # bigger no-bye shape) is still a hard violation, but a genuine
        # input-constrained adaptation (the whole pool is too small) is
        # non-blocking evidence instead.
        shape_age_group = str(t.get("age_group") or "")
        shape = compute_effective_tournament_shape(
            shape_age_group,
            registered_count_by_age_group.get(shape_age_group, 0),
            configured_rounds=rounds_per_tournament.get(shape_age_group),
            parallel_game_capacity=parallel_games_capacity.get(shape_age_group),
        )
        actual_team_count = len(t.get("teams", []))
        actual_bye_round_count = bye_round_shapes.get(t_id, 0)
        if shape_violation(shape, actual_team_count, actual_bye_round_count):
            requirement = (
                f"{shape.effective_team_count} teams"
                if not shape.input_constrained
                else f"{shape.effective_team_count} teams (input-constrained, {shape.registered_team_count} registered)"
            )
            _violate(
                "bye_team_not_allowed",
                f"Tournament {t_id} ({shape_age_group}) has {actual_team_count} teams; "
                f"the registered pool supports {requirement} with at most "
                f"{shape.unavoidable_bye_count} unavoidable rest round(s)",
                t_id,
            )
        elif shape.input_constrained and actual_team_count == shape.effective_team_count:
            input_constrained_shapes.append({"tournament_id": t_id, **shape.as_dict()})

        # issue #323: this check is already scoped to the tournament's OWN
        # participant list (`t.get("teams", [])`), not the roster-wide set
        # -- `_host_eligible_teams` only asks whether the invariant applies
        # at all (does the host club have *some* eligible team registered
        # for this age group anywhere), while `_host_represented_in` checks
        # this specific tournament's actual participants. It therefore
        # already independently catches any regression in host/participant
        # derivation upstream (baseline planner, Stage 3 optimizer, CP-SAT)
        # without needing a second, separate check for the same invariant.
        if host_club and _host_eligible_teams(problem.get("teams", []), host_club, (age_group := t.get("age_group"))) and not _host_represented_in(t.get("teams", []), host_club):
            _violate("host_team_missing", f"Tournament {t_id} host {host_club!r} has an eligible team in {age_group} but none participates", t_id)

        # A host club with no trustworthy calendar evidence this run
        # (blocked/skipped/missing scrape) is never silently treated as
        # verified-free, but it is also not hard-blocked -- it hosts its
        # fair share with a provisional slot (scheduler.py/
        # host_assignment.py) and is surfaced here for manual placement
        # instead, mirroring unresolved_hosting_obligations below. Only
        # enforced when the problem actually carries a (non-empty)
        # calendar-status map -- older/hand-built problem dicts without it
        # skip this check rather than falsely flagging every host as
        # unknown.
        club_calendar_status = problem.get("club_calendar_status") or {}
        if host_club and club_calendar_status and club_calendar_status.get(host_club, "unknown") != "known":
            manual_calendar_placements.append(
                {
                    "tournament_id": t_id,
                    "host_club": host_club,
                    "age_group": t.get("age_group", ""),
                    "date": t_date.isoformat() if t_date else "",
                }
            )

        max_teams = parallel_games.get(t.get("age_group"))
        team_count = len(t.get("teams", []))
        if isinstance(max_teams, int) and max_teams > 0 and team_count > max_teams * 2:
            _violate(
                "tournament_over_capacity",
                f"Tournament {t_id} has {team_count} teams, exceeding the configured "
                f"capacity for {t.get('age_group')} ({max_teams * 2} for {max_teams} parallel games)",
                t_id,
            )

        for team in t.get("teams", []):
            identity = _team_identity(team)
            if valid_teams and identity not in valid_teams:
                _violate(
                    "unregistered_team",
                    f"Team {_display_label(identity, duplicate_labels)!r} in tournament {t_id} is not in "
                    f"the registered roster ({identity[0]}/{identity[2]})",
                    t_id,
                )

    for locked in locked_dates:
        if locked and locked not in scheduled_dates:
            _violate("locked_date_missing", f"Locked date {locked.isoformat()} has no scheduled tournament")

    candidate_ids = {t.get("id") for t in candidate.get("tournaments", [])}
    for pinned in pinned_ids:
        if pinned not in candidate_ids:
            _violate("pinned_tournament_missing", f"Pinned tournament {pinned!r} is missing from the candidate")

    # Canonical baseline locks (see canonical_baseline.py): a tournament an
    # operator approved/booked on a promoted season is a hard-preserve
    # constraint, not a soft preference. This is a no-op for a from-scratch
    # plan whose problem carries no canonical_baseline section.
    for lock_violation in _verify_canonical_locks(problem.get("canonical_baseline"), candidate):
        _violate(
            lock_violation["code"],
            lock_violation["message"],
            lock_violation.get("tournament_id"),
        )

    # Participation targets are *strong operational goals*, not hard legality
    # boundaries, and an explicit ``participation_hard_max`` is a separate,
    # genuinely hard (and operator-waivable) rule. Ordinary over/under target
    # deviation is reported as bounded, evidenced quality evidence instead of
    # blocking verification; see ``participation_targets.py`` for the canonical
    # target/hard-max precedence and avoidability classification.
    participation_evaluation = evaluate_participation(
        candidate, problem, search_evidence=_participation_search_evidence(problem)
    )
    participation_deviations = participation_evaluation.deviations
    participation_metrics = participation_evaluation.metrics
    # A team with configured before/after age-group half targets reports its
    # shortfall per half (more precise); only teams without half targets fall
    # back to a season-scope manual placement, so the same miss is never listed
    # twice. An explicit season-wide override is NOT a half target even though
    # its deterministic split is reported on the team entry.
    _age_group_targets = problem.get("participation_targets_by_age_group") or {}
    _explicit_overrides = {
        (str(t.get("club") or ""), str(t.get("label") or ""), str(t.get("age_group") or ""))
        for t in (problem.get("teams") or [])
        if isinstance(t, dict) and isinstance(t.get("target_tournament_count"), int)
    }
    _has_global_default = isinstance(problem.get("target_tournament_count"), int)
    teams_with_half_targets = set()
    if not _has_global_default:
        for entry in participation_evaluation.teams:
            identity = (
                str(entry.get("club") or ""),
                str(entry.get("label") or ""),
                str(entry.get("age_group") or ""),
            )
            if identity in _explicit_overrides:
                continue
            targets = _age_group_targets.get(identity[2]) or {}
            if isinstance(targets.get("before_christmas"), int) and isinstance(
                targets.get("after_christmas"), int
            ):
                teams_with_half_targets.add(identity)

    for hard_max_violation in participation_evaluation.hard_max_violations:
        identity = (
            str(hard_max_violation.get("club") or ""),
            str(hard_max_violation.get("label") or ""),
            str(hard_max_violation.get("age_group") or ""),
        )
        message = str(hard_max_violation.get("message") or "participation hard maximum exceeded")
        waiver = find_participation_waiver(
            problem,
            identity=identity,
            half=None,
            actual=int(hard_max_violation.get("actual") or 0),
            configured=int(hard_max_violation.get("participation_hard_max") or 0),
            tournament_ids=list(participation_ids_by_half.get("before_christmas", {}).get(identity, []))
            + list(participation_ids_by_half.get("after_christmas", {}).get(identity, [])),
        )
        if waiver is not None:
            waived_violations.append(
                {
                    "code": "participation_hard_max_exceeded",
                    "message": message,
                    "waived_by_operator": True,
                    "waiver_id": waiver.get("id"),
                    "waiver": {
                        "team": identity,
                        "configured_value": hard_max_violation.get("participation_hard_max"),
                        "allowed_value": hard_max_violation.get("actual"),
                        "tournament_id": (waiver.get("scope") or {}).get("tournament_id"),
                        "reason": waiver.get("reason"),
                        "created_at": waiver.get("created_at"),
                        "created_by": waiver.get("created_by"),
                    },
                }
            )
        else:
            _violate("participation_hard_max_exceeded", message)

    for deviation in participation_deviations:
        if deviation.get("direction") != "under_target":
            # Over-target deviation is still surfaced (in
            # ``participation_deviations``/metrics), but it is not a
            # slot-scarcity shortfall an operator patches by arranging an
            # extra game.
            continue
        identity = (
            str(deviation.get("club") or ""),
            str(deviation.get("team") or ""),
            str(deviation.get("age_group") or ""),
        )
        if deviation.get("scope") == SEASON_SCOPE and identity in teams_with_half_targets:
            continue
        display = _display_label(identity, duplicate_labels)
        entry: Dict[str, Any] = {
            "club": deviation.get("club"),
            "label": display,
            "age_group": deviation.get("age_group"),
            "actual": str(deviation.get("actual")),
            "target": str(deviation.get("target")),
            "avoidability": deviation.get("avoidability"),
        }
        # Carry the deterministic club x age-group player-pool classification so
        # the operator/audit surface can tell a genuine club-pool shortage apart
        # from an intra-club label imbalance, and publication readiness can stop
        # counting pure redistribution as an unresolved participation deficit.
        pool = deviation.get("club_pool")
        if isinstance(pool, Mapping):
            entry["club_pool_classification"] = deviation.get("club_pool_classification")
            entry["club_pool_significance"] = deviation.get("club_pool_significance")
            entry["counts_as_unresolved_shortfall"] = bool(
                deviation.get("counts_as_unresolved_shortfall", True)
            )
            entry["club_pool"] = dict(pool)
        if deviation.get("scope") in ("before_christmas", "after_christmas"):
            entry["half"] = deviation.get("scope")
        manual_participation_placements.append(entry)

    # issue #266 P0: club x age-group hosting coverage is a required
    # planning obligation, not a hard `violation` -- a candidate with an
    # unresolved obligation is still `ok` (it must not silently give the
    # hosting burden to another club and be rejected outright), but the
    # obligation must be visible so it can be surfaced as a manual-placement
    # item rather than swallowed.
    from tournament_scheduler.hosting_coverage import (
        hosting_balance_matrix,
        hosting_coverage_matrix,
        material_hosting_balance_imbalances,
    )
    from tournament_scheduler.hosting_cross_age_repair import club_hosting_evidence, unresolved_with_evidence
    from tournament_scheduler.hosting_same_age_repair import same_age_reallocation_candidates

    coverage_rows = hosting_coverage_matrix(problem.get("teams", []), tournaments)
    hosting_balance_rows = hosting_balance_matrix(problem.get("teams", []), tournaments)
    hosting_balance_imbalances = material_hosting_balance_imbalances(hosting_balance_rows)
    # issue #328: also expose, per unresolved row, whichever of this club's
    # own surplus/duplicate hosting assignments in a *different* age group
    # could plausibly be repurposed -- evidence only, never auto-applied
    # here (this is a pure verifier with no live planner to rebuild a
    # tournament through; see `hosting_cross_age_repair_apply.py` for where
    # SeasonPlanner actually attempts the repair).
    coverage_evidence = club_hosting_evidence(problem.get("teams", []), tournaments)
    unresolved_hosting_obligations = unresolved_with_evidence(coverage_rows, coverage_evidence, tournaments)
    # issue #329: also expose, per unresolved row, tournaments in that same
    # age group the club already participates in but does not host -- a
    # cheaper/lower-risk repair than a cross-age reallocation since it needs
    # no participant swap; see `hosting_same_age_repair_apply.py` for where
    # SeasonPlanner actually attempts it.
    for row in unresolved_hosting_obligations:
        row["same_age_reallocation_candidates"] = same_age_reallocation_candidates(
            row["club"], row["age_group"], tournaments
        )

    return {
        "ok": not violations,
        "violations": violations,
        "waived_violations": waived_violations,
        "skipped": skipped,
        "club_controlled_allocations_used": movable_allocations_used,
        "movable_allocations_used": movable_allocations_used,
        # A candidate-borne ``calendar_interpretations`` overlay is reported
        # separately so the audit can tell a controller-inferred movable
        # interval apart from a configured or confirmed-free fact. The full
        # ambiguous-event list is deliberately NOT rebuilt here: this runs on
        # every candidate verification, and normalization already exposes it
        # once via ``planning_problem.unclassified_calendar_events``.
        "calendar_interpretations_used": [
            dict(entry)
            for entry in (candidate.get("calendar_interpretations") or [])
            if isinstance(entry, Mapping)
        ],
        "unresolved_hosting_obligations": unresolved_hosting_obligations,
        "hosting_balance": hosting_balance_rows,
        "hosting_balance_imbalances": hosting_balance_imbalances,
        "manual_calendar_placements": manual_calendar_placements,
        "manual_external_conflict_placements": manual_external_conflict_placements,
        "manual_participation_placements": manual_participation_placements,
        "participation_deviations": participation_deviations,
        "participation_metrics": participation_metrics,
        # Club x age-group x scope player-pool aggregation (classification +
        # exact team distribution) and the unresolved subset, exposed as
        # first-class deterministic facts for the audit/objective layers.
        "participation_club_pools": participation_evaluation.club_pools,
        "participation_club_pool_shortfalls": participation_evaluation.club_pool_shortfalls,
        "input_constrained_shapes": input_constrained_shapes,
        "stale_approvals": stale_approvals,
        "orphaned_approvals": orphaned_approvals,
    }


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------


def score_candidate(
    candidate: Dict[str, Any],
    gap_thresholds: Iterable[int] = (7, 14),
    *,
    problem: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute deterministic quality metrics for *candidate*.

    Self-contained: needs no ``planning_problem`` and no LLM. Metrics are
    grouped to mirror issue #257's scope: participation, opponent
    diversity (inter-club vs. same-club kept separate), turnaround
    spacing, and hosting fairness.

    *problem* is optional (issue #266) -- when given, ``hosting`` also
    reports a club x age-group coverage matrix (every registered club x
    age-group pair, hosted count, and whether it is an unresolved
    obligation). Without it, only the pre-existing club-level
    ``counts_by_host``/``spread`` are returned, since the full registered
    roster (including clubs with zero hosted tournaments) isn't derivable
    from the candidate's tournaments alone.
    """
    tournaments = sorted(
        (t for t in candidate.get("tournaments", []) if not t.get("cancelled")),
        key=lambda t: _parse_date(t.get("date")) or date.min,
    )
    duplicate_labels = _duplicate_labels(tournaments)

    # --- participation -------------------------------------------------
    participations: Dict[TeamIdentity, int] = {}
    for t in tournaments:
        for team in t.get("teams", []):
            if team.get("label"):
                identity = _team_identity(team)
                participations[identity] = participations.get(identity, 0) + 1
    counts = list(participations.values())
    participation_spread = (max(counts) - min(counts)) if counts else 0
    # When the planning problem carries configured targets, also expose the
    # canonical bounded-relaxation evidence (season/half deviation, avoidability)
    # so participation is a first-class comparison dimension rather than a
    # single spread scalar a lower-priority objective can outvote.
    participation_metrics: Dict[str, Any] = {"spread": participation_spread}
    if problem is not None:
        participation_metrics.update(
            evaluate_participation(
                candidate, problem, search_evidence=_participation_search_evidence(problem)
            ).metrics
        )

    # --- opponent diversity ---------------------------------------------
    pair_counts: Dict[Tuple[TeamIdentity, TeamIdentity], int] = {}
    same_club_pairs: set[Tuple[TeamIdentity, TeamIdentity]] = set()
    inter_club_pairs: set[Tuple[TeamIdentity, TeamIdentity]] = set()
    novel_games = 0
    total_games = 0
    # The universe of *possible* inter-club opponents is every cross-club
    # pair drawn from all teams that appear anywhere in the season for an
    # age group — not just pairs that happened to share a tournament, which
    # would make `inter_club_diversity` trivially ~100% (round-robin means
    # co-attendance already implies they played).
    teams_by_age_group: Dict[Optional[str], set[TeamIdentity]] = {}

    for t in tournaments:
        teams = t.get("teams", [])
        age_group = t.get("age_group")
        teams_by_age_group.setdefault(age_group, set()).update(_team_identity(tm) for tm in teams)

        identity_by_label = {team.get("label"): _team_identity(team) for team in teams}
        for game in t.get("games", []):
            home_label, away_label = game.get("home"), game.get("away")
            home_identity = identity_by_label.get(home_label)
            away_identity = identity_by_label.get(away_label)
            if home_identity is None or away_identity is None:
                continue
            pair = tuple(sorted((home_identity, away_identity)))
            total_games += 1
            if pair not in pair_counts:
                novel_games += 1
            pair_counts[pair] = pair_counts.get(pair, 0) + 1

            if home_identity[0] == away_identity[0]:
                same_club_pairs.add(pair)
            else:
                inter_club_pairs.add(pair)

    unique_pairs = len(pair_counts)
    pairwise_novelty = (novel_games / total_games) if total_games else 0.0
    repeat_distribution: Dict[int, int] = {}
    for count in pair_counts.values():
        repeat_distribution[count] = repeat_distribution.get(count, 0) + 1
    pairs_meeting_3_plus = sum(v for k, v in repeat_distribution.items() if k >= 3)
    max_pair_repeat = max(pair_counts.values()) if pair_counts else 0

    inter_club_universe: set[Tuple[TeamIdentity, TeamIdentity]] = set()
    for identities in teams_by_age_group.values():
        for a, b in combinations(sorted(identities), 2):
            if a[0] != b[0]:
                inter_club_universe.add((a, b))
    inter_club_diversity = (len(inter_club_pairs) / len(inter_club_universe)) if inter_club_universe else 0.0

    # --- same-club clustering -------------------------------------------
    # issue #324: `max_same_club_per_tournament` alone can't distinguish a
    # single unavoidable 3-team tournament from a candidate that clusters
    # 3+ teams from one club repeatedly, so baseline/local-search/CP-SAT
    # candidates are also compared on these two explicit metrics.
    max_same_club_per_tournament = 0
    club_count_excess_over_2 = 0
    tournaments_with_3plus_same_club = 0
    for t in tournaments:
        club_counts: Dict[str, int] = {}
        for team in t.get("teams", []):
            club = team.get("club")
            if club:
                club_counts[club] = club_counts.get(club, 0) + 1
        if club_counts:
            max_same_club_per_tournament = max(max_same_club_per_tournament, max(club_counts.values()))
            club_count_excess_over_2 += sum(max(0, count - 2) for count in club_counts.values())
            if max(club_counts.values()) >= 3:
                tournaments_with_3plus_same_club += 1

    # --- turnaround spacing ----------------------------------------------
    gap_thresholds = list(gap_thresholds)
    gaps_under: Dict[int, int] = {threshold: 0 for threshold in gap_thresholds}
    min_turnaround: Optional[int] = None
    dates_by_identity: Dict[TeamIdentity, List[date]] = {}
    for identity in participations:
        dates = sorted(
            _parse_date(t.get("date"))
            for t in tournaments
            if any(_team_identity(team) == identity for team in t.get("teams", []))
        )
        dates = [d for d in dates if d is not None]
        dates_by_identity[identity] = dates
        for prev, nxt in zip(dates, dates[1:]):
            gap = (nxt - prev).days
            if min_turnaround is None or gap < min_turnaround:
                min_turnaround = gap
            for threshold in gap_thresholds:
                if gap < threshold:
                    gaps_under[threshold] += 1

    # --- temporal coverage (season_start -> ... -> season_end) -----------
    # Consolidates the old finish-only / intra-season-only gap checks: a
    # team whose tournaments cluster into a short early window shows up here
    # via a large finish gap even when every existing tournament is well
    # spaced from its neighbours. Only computed when *problem* carries real
    # season boundaries (like hosting_coverage above) -- without them, the
    # only boundary available would be the candidate's own min/max date,
    # which is circular (every candidate trivially has zero gap to its own
    # first/last tournament) and would make this metric noise rather than
    # signal for problem-less scoring/comparison callers.
    from tournament_scheduler.temporal_coverage import (
        DEFAULT_TEMPORAL_COVERAGE_THRESHOLD_DAYS,
        season_temporal_coverage,
        temporal_offenders,
    )

    season_start = _parse_date(problem.get("start_date")) if problem else None
    season_end = _parse_date(problem.get("end_date")) if problem else None

    # `problem["christmas_split_date"]` is the shared boundary every engine
    # (Stage 3 local search, CP-SAT, this scorer) reads from, set once in
    # `build_planning_problem`. Falls back to deriving it from the
    # candidate's own tournament dates when no problem is supplied, so this
    # metric still degrades gracefully for the problem-less verification
    # path instead of silently omitting half reporting (issue #293). Also
    # feeds the temporal-coverage active-window calculation below (#319).
    split_date: Optional[date] = None
    if problem is not None and problem.get("christmas_split_date"):
        split_date = _parse_date(problem["christmas_split_date"])
    elif tournaments:
        dated = [d for d in (_parse_date(t.get("date")) for t in tournaments) if d is not None]
        if dated:
            split_date = planning_half.christmas_split_date(min(dated), max(dated))

    temporal_offenders_list: List[Dict[str, Any]] = []
    temporal_max_gap_days = 0
    if season_start is not None and season_end is not None:
        team_meta = {identity: (identity[0], identity[2]) for identity in dates_by_identity}
        coverages = season_temporal_coverage(
            season_start,
            season_end,
            dates_by_identity,
            team_meta,
            participation_targets_by_age_group=(problem or {}).get("participation_targets_by_age_group"),
            split_date=split_date,
        )
        temporal_max_gap_days = max((c.max_gap_days for c in coverages), default=0)
        temporal_offenders_list = [
            {
                "team": _display_label(coverage.team_key, duplicate_labels),
                "age_group": coverage.age_group,
                "lead_gap_days": coverage.lead_gap_days,
                "finish_gap_days": coverage.finish_gap_days,
                "max_intra_gap_days": coverage.max_intra_gap_days,
                "max_gap_days": coverage.max_gap_days,
            }
            for coverage in temporal_offenders(coverages, DEFAULT_TEMPORAL_COVERAGE_THRESHOLD_DAYS)
        ]

    # --- hosting fairness --------------------------------------------------
    host_counts, hosting_spread, hosting_coverage = _hosting_fairness(tournaments, problem)

    # --- month distribution --------------------------------------------------
    month_counts, half_counts, half_deviation_pct = _month_and_half_distribution(
        tournaments, split_date, _parse_date
    )

    return {
        "participation": {
            "counts_by_team": {
                _display_label(identity, duplicate_labels): count
                for identity, count in participations.items()
            },
            **participation_metrics,
        },
        "opponent_diversity": {
            "unique_pairs": unique_pairs,
            "pairwise_novelty": pairwise_novelty,
            "pair_repeat_distribution": repeat_distribution,
            "pairs_meeting_3_plus": pairs_meeting_3_plus,
            "max_pair_repeat": max_pair_repeat,
            "inter_club_diversity": inter_club_diversity,
            "same_club_pairing_count": len(same_club_pairs),
            "max_same_club_teams_per_tournament": max_same_club_per_tournament,
            "club_count_excess_over_2": club_count_excess_over_2,
            "tournaments_with_3plus_same_club": tournaments_with_3plus_same_club,
        },
        "temporal": {
            "max_gap_days": temporal_max_gap_days,
            "offenders_count": len(temporal_offenders_list),
            "offenders": temporal_offenders_list,
        },
        "turnaround": {
            "min_turnaround_days": min_turnaround,
            "gaps_under_days": gaps_under,
        },
        "hosting": {
            "counts_by_host": host_counts,
            "spread": hosting_spread,
            **hosting_coverage,
        },
        "month_distribution": month_counts,
        "half_distribution": half_counts,
        "half_deviation_pct": half_deviation_pct,
    }
