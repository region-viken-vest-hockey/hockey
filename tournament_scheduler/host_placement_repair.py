"""Deterministic responsibility-preserving placement repair options.

When the responsible host is represented and still owes hosting, but the
planner found no verified slot on the selected date, baseline building can
leave the tournament as ``MANUAL PLACEMENT REQUIRED``. Hosting responsibility
and automatic placement are separate concerns (see
``docs/system-architecture.md``): the obligation stays with the intended host,
but before that obligation is handed to the operator this provider enumerates
the bounded, *responsibility-preserving* moves that could still recover an
automatic placement for the same host:

- ``move_same_host_start_time`` -- another legal start time on the same date;
- ``move_same_host_date`` -- another already-scheduled season date (same
  planning half unless cross-half moves are explicitly enabled) with a legal
  slot for the same host;
- ``swap_compatible_tournament_placement`` -- only when neither same-host move
  verifies: trade date/start time with a compatible same-age tournament so
  both responsible hosts get a legal slot, each keeping its own host and
  arena.

It never transfers hosting responsibility to another club, never invents a new
season date, and never weakens a hard rule. Applying a selected option mutates
a copy, regenerates the tournament's placement and reruns the full independent
verifier; the candidate is committed only when verification passes.

Calendar-untrusted hosts are reported with an explicit rejection reason rather
than guessed at: an unknown/untrusted calendar means automatic placement cannot
be proven, so the manual item is the honest outcome.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import planning_half
from .application.decisions import DecisionContext
from .host_representation import constituent_clubs
from .host_team_missing_repair import (
    RepairOption,
    _candidate_start_times,
    _codes,
    _duration_minutes,
    _find_tournament,
    _primary_violation_reason as _primary_reason,
    _slug,
    candidate_fingerprint,
)
from .planning_contract import (
    _parse_date,
    apply_calendar_interpretations,
    external_calendar_conflict,
    verify_candidate,
)

# The canonical marker SeasonPlanner writes when no verified slot was found for
# the responsible host (see season_planner.py and the manual-schedule export).
# It deliberately excludes the calendar-untrusted reason: an unverified
# calendar cannot be repaired by slot search, only booked by hand.
MANUAL_SLOT_FAILURE_MARKER = "må plasseres manuelt"

# Bounded neighborhoods: enough alternatives for the controller to choose from
# without turning one unplaced tournament into a season-wide search.
_MAX_START_TIME_OPTIONS = 6
_MAX_DATE_CANDIDATES = 8
_MAX_SWAP_OPTIONS = 4
_MAX_OPTIONS = 12


@dataclass(frozen=True)
class _Finding:
    finding_id: str
    tournament_id: str
    host_club: str
    age_group: str
    original_date: str


def candidate_has_manual_slot_failure(candidate: Mapping[str, Any]) -> bool:
    """True when any active tournament is marked as an unplaced slot failure."""
    return any(
        not tournament.get("cancelled") and _is_manual_slot_failure(tournament)
        for tournament in candidate.get("tournaments", [])
    )


def enumerate_host_placement_repairs(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    run_id: str = "",
) -> Dict[str, Any]:
    """Return legal same-host placement options and rejection evidence."""
    verification = verify_candidate(dict(candidate), dict(problem))
    fingerprint = candidate_fingerprint(candidate)
    pinned_ids = {
        str(item) for item in (problem.get("manual_adjustments") or {}).get("pinned_tournament_ids", [])
    }
    options: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    for tournament in candidate.get("tournaments", []):
        if tournament.get("cancelled") or not _is_manual_slot_failure(tournament):
            continue
        tournament_id = str(tournament.get("id"))
        host_club = str(tournament.get("host_club") or "")
        finding = _Finding(
            finding_id=f"manual_placement:{tournament_id}",
            tournament_id=tournament_id,
            host_club=host_club,
            age_group=str(tournament.get("age_group") or ""),
            original_date=str(tournament.get("date") or ""),
        )
        if not host_club:
            rejected.append({**_base(finding), "reason": "missing_host_club"})
            continue
        if tournament_id in pinned_ids:
            rejected.append({**_base(finding), "reason": "manual_restriction_forbids_mutation"})
            continue
        if not _calendar_trusted(problem, host_club):
            rejected.append({**_base(finding), "reason": "calendar_evidence_not_trusted"})
            continue
        if len(options) >= _MAX_OPTIONS:
            rejected.append({**_base(finding), "reason": "bounded_search_budget_exhausted"})
            continue
        time_options, time_rejected = _start_time_options(
            candidate, problem, tournament, finding, fingerprint
        )
        interpret_options: List[RepairOption] = []
        interpret_rejected: List[Dict[str, Any]] = []
        if not time_options:
            # No unconditionally free same-date slot. Before moving the
            # tournament to another date, expose an ambiguous scraped event
            # that *could* be host-controlled capacity as an explicit,
            # confirmation-gated interpretation option. This is a fact-based
            # candidate, not a guess: only an event nothing has classified is
            # eligible, and applying it never mutates the source calendar.
            interpret_options, interpret_rejected = _interpretation_options(
                candidate, problem, tournament, finding, fingerprint
            )
        date_options, date_rejected = _date_options(
            candidate, problem, tournament, finding, fingerprint
        )
        options.extend(time_options)
        options.extend(interpret_options)
        options.extend(date_options)
        rejected.extend(time_rejected)
        rejected.extend(interpret_rejected)
        rejected.extend(date_rejected)
        if not time_options and not interpret_options and not date_options:
            # Coupled nearby neighborhood, only after the small same-host
            # neighborhood found nothing: trade placements with a compatible
            # same-age tournament so both responsible hosts get a legal slot.
            swap_options, swap_rejected = _swap_options(
                candidate, problem, tournament, finding, fingerprint, pinned_ids
            )
            options.extend(swap_options)
            rejected.extend(swap_rejected)
        if not any(
            option.finding_id == finding.finding_id for option in options
        ):
            rejected.append(
                {
                    **_base(finding),
                    "reason": "no_same_host_slot_found",
                    "dates_checked": sorted(
                        {entry.get("date") for entry in date_rejected if entry.get("date")}
                    ),
                }
            )
    return {
        "run_id": run_id,
        "candidate_fingerprint": fingerprint,
        "verification": verification,
        "options": [option.to_dict() for option in options],
        "rejected_candidates": rejected,
    }


def build_host_placement_decision_context(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    run_id: str,
    candidate_ref: Optional[str] = None,
) -> DecisionContext:
    repair_set = enumerate_host_placement_repairs(candidate, problem, run_id=run_id)
    legal_ids = [option["option_id"] for option in repair_set["options"]]
    verification = repair_set["verification"]
    return DecisionContext(
        run_id=run_id,
        capability="host_placement_repair",
        stage="stage3",
        objective=(
            "Choose one repository-generated responsibility-preserving placement for a "
            "tournament the planner left manual, or keep/escalate if none is legal."
        ),
        facts={
            "candidate_fingerprint": repair_set["candidate_fingerprint"],
            "repair_options": repair_set["options"],
            "rejected_candidates": repair_set["rejected_candidates"],
            # Read-only ambiguous calendar facts: events nothing has
            # classified yet. They are not free and not movable -- they are
            # candidates the controller may investigate through an
            # ``interpret_calendar_event_as_movable`` repair option.
            "unclassified_calendar_events": _unclassified_calendar_events(problem),
        },
        # Keeping the candidate as-is is legitimate here: manual placement is
        # soft/unresolved evidence, not a hard violation, so a plan the
        # optimizer cannot improve must remain finalizable.
        baseline_hard_violations=tuple(
            f"{violation.get('code')}: {violation.get('message')}"
            for violation in verification.get("violations", [])
        ),
        candidate_ref=candidate_ref,
        available_actions=(
            "apply_repair_option",
            "keep_baseline",
            "optimize_plan",
            "request_operator",
        ),
        action_parameters={
            "apply_repair_option": {
                "option_id": {
                    "type": "string",
                    "enum": legal_ids,
                    "description": "Repository-generated placement option id to apply.",
                },
                "candidate_fingerprint": {
                    "type": "string",
                    "enum": [repair_set["candidate_fingerprint"]],
                },
            }
        },
    )


def apply_host_placement_repair_option(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    option_id: str,
    expected_fingerprint: str,
    run_id: str = "",
) -> Dict[str, Any]:
    """Atomically apply a selected placement option and verify the result."""
    before = candidate_fingerprint(candidate)
    if before != expected_fingerprint:
        return {
            "ok": False,
            "reason": "stale_candidate_fingerprint",
            "before_fingerprint": before,
            "expected_fingerprint": expected_fingerprint,
        }
    repair_set = enumerate_host_placement_repairs(candidate, problem, run_id=run_id)
    option = next(
        (entry for entry in repair_set["options"] if entry["option_id"] == option_id), None
    )
    if option is None:
        return {"ok": False, "reason": "unknown_or_stale_option", "before_fingerprint": before}

    mutated = copy.deepcopy(candidate)
    tournament = _find_tournament(mutated, option["tournament_id"])
    if tournament is None:
        return {"ok": False, "reason": "tournament_not_found", "before_fingerprint": before}
    arguments = option["arguments"]
    original_date = str(arguments["original_date"])
    if option["action"] == "interpret_calendar_event_as_movable":
        # Record the inferred interpretation on the *candidate*. The Stage 2
        # source calendar is untouched; verification re-applies the overlay,
        # so the placement surfaces as movable_busy with an explicit
        # host-confirmation requirement instead of confirmed free ice.
        interpretation = dict(arguments.get("interpretation") or {})
        interpretations = list(mutated.get("calendar_interpretations") or [])
        interpretations.append(interpretation)
        mutated["calendar_interpretations"] = interpretations
    tournament["date"] = str(arguments["date"])
    tournament["start_time"] = str(arguments["start_time"])
    tournament["manual_booking_reason"] = None
    _clear_unresolved_placement(mutated, tournament, original_date)
    if option["action"] == "swap_compatible_tournament_placement":
        donor = _find_tournament(mutated, str(arguments["swap_tournament_id"]))
        if donor is None:
            return {"ok": False, "reason": "swap_tournament_not_found", "before_fingerprint": before}
        donor["date"] = str(arguments["swap_tournament_date"])
        donor["start_time"] = str(arguments["swap_tournament_start_time"])

    verification = verify_candidate(mutated, dict(problem))
    if not verification.get("ok"):
        return {
            "ok": False,
            "reason": "verification_failed",
            "before_fingerprint": before,
            "verification": verification,
        }
    return {
        "ok": True,
        "candidate": mutated,
        "option_id": option_id,
        "before_fingerprint": before,
        "after_fingerprint": candidate_fingerprint(mutated),
        "verification": verification,
        "effects": option.get("effects", {}),
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _base(finding: _Finding) -> Dict[str, Any]:
    return {
        "finding_id": finding.finding_id,
        "tournament_id": finding.tournament_id,
        "host_club": finding.host_club,
        "age_group": finding.age_group,
        "original_date": finding.original_date,
    }


def _is_manual_slot_failure(tournament: Mapping[str, Any]) -> bool:
    return MANUAL_SLOT_FAILURE_MARKER in str(tournament.get("manual_booking_reason") or "")


def _movable_option_facts(
    problem: Mapping[str, Any],
    host_club: str,
    date_str: str,
    start_time: str,
    duration_minutes: int,
    candidate: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Normalized availability facts for one repaired placement.

    A repair offered into a host-controlled ``movable_busy`` interval is a
    valid option, but the controller/audit must see that it displaces an
    existing event and needs host confirmation rather than looking like
    unconditionally free ice. Read directly from the same interval evidence
    the repair enumeration already uses, so it does not depend on the
    verifier's own ice-time config being present.

    When *candidate* carries ``calendar_interpretations``, the same effective
    view the verifier uses is applied, so a controller-inferred movable
    interval is reported with ``classification_source: inferred`` instead of
    masquerading as free ice.
    """
    from tournament_scheduler.planning_contract import movable_calendar_opportunity

    on_date = _parse_date(date_str)
    if on_date is None:
        return {"availability": "free", "requires_host_confirmation": False}
    busy = (
        _effective_busy_intervals(problem, candidate)
        if candidate is not None
        else _busy_intervals(problem)
    )
    opportunity = movable_calendar_opportunity(
        busy,
        host_club,
        on_date,
        start_time,
        duration_minutes,
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


def _calendar_trusted(problem: Mapping[str, Any], host_club: str) -> bool:
    statuses = problem.get("club_calendar_status") or {}
    if not statuses:
        return False
    constituents = constituent_clubs(host_club) or [host_club]
    return all(statuses.get(part) == "known" for part in constituents)


def _busy_intervals(problem: Mapping[str, Any]) -> Mapping[str, Any]:
    return problem.get("club_busy_intervals") or {}


def _effective_busy_intervals(
    problem: Mapping[str, Any], candidate: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Busy intervals with any candidate ``calendar_interpretations`` applied.

    Enumeration must see the same effective availability the verifier will:
    once the controller has recorded an inferred movable interpretation on the
    candidate, a later enumeration pass must not keep rejecting that interval
    as a fixed external conflict.
    """
    return apply_calendar_interpretations(
        problem.get("club_busy_intervals") or {},
        candidate.get("calendar_interpretations"),
    )


def _unclassified_calendar_events(problem: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Ambiguous scraped events for the decision context.

    Prefer the planning problem's explicit list (built during normalization)
    and fall back to deriving it from ``club_busy_intervals`` so a problem
    assembled without that convenience field still exposes the same facts
    rather than silently hiding them.
    """
    explicit = problem.get("unclassified_calendar_events")
    if explicit is not None:
        return [dict(entry) for entry in explicit if isinstance(entry, Mapping)]
    from .calendar_availability import unclassified_intervals

    return unclassified_intervals(problem.get("club_busy_intervals"))


def _start_time_options(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    tournament: Mapping[str, Any],
    finding: _Finding,
    fingerprint: str,
) -> Tuple[List[RepairOption], List[Dict[str, Any]]]:
    out: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    on_date = _parse_date(finding.original_date)
    current_start = tournament.get("start_time")
    duration = _duration_minutes(tournament, problem)
    busy = _effective_busy_intervals(problem, candidate)
    checked: List[str] = []
    for start in _candidate_start_times(tournament):
        if start == current_start:
            continue
        base = {**_base(finding), "start_time": start}
        if external_calendar_conflict(busy, finding.host_club, on_date, start, duration):
            rejected.append({**base, "reason": "external_calendar_conflict"})
            continue
        checked.append(start)
        trial = copy.deepcopy(candidate)
        trial_tournament = _find_tournament(trial, finding.tournament_id)
        trial_tournament["start_time"] = start
        trial_tournament["manual_booking_reason"] = None
        _clear_unresolved_placement(trial, trial_tournament, finding.original_date)
        result = verify_candidate(trial, dict(problem))
        if not result.get("ok"):
            rejected.append({**base, "reason": _primary_reason(result), "violations": _codes(result)})
            continue
        out.append(
            RepairOption(
                option_id=(
                    f"{fingerprint[:12]}:{finding.finding_id}:move_same_host_start_time:"
                    f"{_slug((finding.original_date, start))}"
                ),
                finding_id=finding.finding_id,
                action="move_same_host_start_time",
                tournament_id=finding.tournament_id,
                arguments={
                    "original_date": finding.original_date,
                    "date": finding.original_date,
                    "start_time": start,
                },
                hard_feasible=True,
                effects={"manual_placement_required": -1, "date_changed": 0},
                evidence={
                    "verification_ok": True,
                    "responsible_host": finding.host_club,
                    "date": finding.original_date,
                    "start_time": start,
                    "end_time": _end_time(start, duration),
                    "start_times_checked": list(checked),
                    **_movable_option_facts(
                        problem,
                        finding.host_club,
                        finding.original_date,
                        start,
                        duration,
                        candidate,
                    ),
                },
            )
        )
        if len(out) >= _MAX_START_TIME_OPTIONS:
            break
    return out, rejected


def _interpretation_options(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    tournament: Mapping[str, Any],
    finding: _Finding,
    fingerprint: str,
) -> Tuple[List[RepairOption], List[Dict[str, Any]]]:
    """Options that treat an *ambiguous* scraped event as host-controlled.

    Only an interval whose title nothing configured has classified is
    eligible (re-derived through the same per-club rules the normalizer
    uses). The offered option records a controller-requested,
    host-confirmation-gated interpretation on the *candidate* -- it never
    edits the Stage 2 source calendar and never turns a configured fixed
    booking into capacity. If no interpretation is needed (a free same-date
    slot exists) the caller does not offer these at all.
    """
    out: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    if not finding.host_club:
        return out, rejected
    effective = _effective_busy_intervals(problem, candidate)
    from .calendar_availability import is_unclassified_event

    entries = [
        entry
        for entry in (effective.get(finding.host_club) or [])
        if is_unclassified_event(
            finding.host_club, str(entry.get("calendar_event") or "")
        )
        and entry.get("calendar_event")
        and entry.get("date") == finding.original_date
    ]
    if not entries:
        return out, rejected
    duration = _duration_minutes(tournament, problem)
    current_start = tournament.get("start_time")
    for entry in entries:
        interpretation = {
            "club": finding.host_club,
            "date": finding.original_date,
            "start": entry.get("start", ""),
            "end": entry.get("end", ""),
            "calendar_event": entry.get("calendar_event", ""),
            "reason": (
                "controller-inferred host-controlled interval; "
                "host confirmation required before the placement is booked"
            ),
        }
        interpreted_busy = apply_calendar_interpretations(effective, [interpretation])
        for start in _candidate_start_times(tournament):
            if start == current_start:
                continue
            base = {**_base(finding), "start_time": start}
            if external_calendar_conflict(
                interpreted_busy, finding.host_club, _parse_date(finding.original_date), start, duration
            ):
                # Another genuine fixed booking still blocks this start -- an
                # inferred movable interpretation must not paper over it.
                rejected.append({**base, "reason": "external_calendar_conflict"})
                continue
            trial = copy.deepcopy(candidate)
            trial["calendar_interpretations"] = [
                *(trial.get("calendar_interpretations") or []),
                interpretation,
            ]
            trial_tournament = _find_tournament(trial, finding.tournament_id)
            trial_tournament["start_time"] = start
            trial_tournament["manual_booking_reason"] = None
            _clear_unresolved_placement(trial, trial_tournament, finding.original_date)
            result = verify_candidate(trial, dict(problem))
            if not result.get("ok"):
                rejected.append(
                    {**base, "reason": _primary_reason(result), "violations": _codes(result)}
                )
                continue
            out.append(
                RepairOption(
                    option_id=(
                        f"{fingerprint[:12]}:{finding.finding_id}:"
                        f"interpret_calendar_event_as_movable:"
                        f"{_slug((finding.original_date, start, entry.get('calendar_event', '')))}"
                    ),
                    finding_id=finding.finding_id,
                    action="interpret_calendar_event_as_movable",
                    tournament_id=finding.tournament_id,
                    arguments={
                        "original_date": finding.original_date,
                        "date": finding.original_date,
                        "start_time": start,
                        "interpretation": interpretation,
                    },
                    hard_feasible=True,
                    effects={
                        "manual_placement_required": -1,
                        "date_changed": 0,
                        "calendar_interpretations_added": 1,
                    },
                    evidence={
                        "verification_ok": True,
                        "responsible_host": finding.host_club,
                        "date": finding.original_date,
                        "start_time": start,
                        "end_time": _end_time(start, duration),
                        "availability": "movable_busy",
                        "requires_host_confirmation": True,
                        "classification_source": "inferred",
                        "calendar_event": entry.get("calendar_event", ""),
                        "host_action_required": interpretation["reason"],
                    },
                )
            )
        if len(out) >= _MAX_START_TIME_OPTIONS:
            break
    return out, rejected


def _date_options(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    tournament: Mapping[str, Any],
    finding: _Finding,
    fingerprint: str,
) -> Tuple[List[RepairOption], List[Dict[str, Any]]]:
    out: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    current_date = _parse_date(finding.original_date)
    duration = _duration_minutes(tournament, problem)
    busy = _effective_busy_intervals(problem, candidate)
    current_start = str(tournament.get("start_time") or "11:00")
    candidate_dates = _candidate_dates(candidate, problem, current_date)
    for candidate_date in candidate_dates:
        base = {**_base(finding), "candidate_date": candidate_date.isoformat()}
        start = current_start
        if external_calendar_conflict(busy, finding.host_club, candidate_date, start, duration):
            rejected.append({**base, "start_time": start, "reason": "external_calendar_conflict"})
            continue
        trial = copy.deepcopy(candidate)
        trial_tournament = _find_tournament(trial, finding.tournament_id)
        trial_tournament["date"] = candidate_date.isoformat()
        trial_tournament["start_time"] = start
        trial_tournament["manual_booking_reason"] = None
        _clear_unresolved_placement(trial, trial_tournament, finding.original_date)
        result = verify_candidate(trial, dict(problem))
        if not result.get("ok"):
            rejected.append(
                {**base, "start_time": start, "reason": _primary_reason(result), "violations": _codes(result)}
            )
            continue
        out.append(
            RepairOption(
                option_id=(
                    f"{fingerprint[:12]}:{finding.finding_id}:move_same_host_date:"
                    f"{_slug((candidate_date.isoformat(), start))}"
                ),
                finding_id=finding.finding_id,
                action="move_same_host_date",
                tournament_id=finding.tournament_id,
                arguments={
                    "original_date": finding.original_date,
                    "date": candidate_date.isoformat(),
                    "start_time": start,
                },
                hard_feasible=True,
                effects={"manual_placement_required": -1, "date_changed": 1},
                evidence={
                    "verification_ok": True,
                    "responsible_host": finding.host_club,
                    "date": candidate_date.isoformat(),
                    "start_time": start,
                    "end_time": _end_time(start, duration),
                    **_movable_option_facts(
                        problem,
                        finding.host_club,
                        candidate_date.isoformat(),
                        start,
                        duration,
                        candidate,
                    ),
                },
            )
        )
        if len(out) >= _MAX_OPTIONS:
            break
    return out, rejected


def _swap_options(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    tournament: Mapping[str, Any],
    finding: _Finding,
    fingerprint: str,
    pinned_ids: set,
) -> Tuple[List[RepairOption], List[Dict[str, Any]]]:
    """Compatible same-age placement swaps that keep both hosts responsible.

    The manual tournament and a compatible scheduled same-age tournament
    exchange date/start time, but each keeps its own responsible host and
    arena. The swap is offered only when both hosts get an externally free
    slot on the other's date and the resulting candidate verifies; anything
    else is recorded as explicit rejection evidence.
    """
    out: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    if not finding.age_group:
        return out, rejected
    current_start = str(tournament.get("start_time") or "10:00")
    current_duration = _duration_minutes(tournament, problem)
    busy = _effective_busy_intervals(problem, candidate)
    for donor in candidate.get("tournaments", []):
        donor_id = str(donor.get("id"))
        base = {
            **_base(finding),
            "donor_tournament_id": donor_id,
            "donor_host_club": donor.get("host_club"),
        }
        if donor_id == finding.tournament_id or donor.get("cancelled"):
            continue
        if donor.get("age_group") != finding.age_group:
            continue
        donor_host = str(donor.get("host_club") or "")
        if not donor_host:
            rejected.append({**base, "reason": "donor_missing_host_club"})
            continue
        if donor_id in pinned_ids:
            rejected.append({**base, "reason": "donor_manual_restriction_forbids_mutation"})
            continue
        if _is_manual_slot_failure(donor):
            # Only trade with an already-placed tournament: a swap that moves
            # one manual item onto another manual item has not repaired
            # anything, and would need its own manual-state bookkeeping.
            rejected.append({**base, "reason": "donor_itself_manual"})
            continue
        if not _calendar_trusted(problem, donor_host):
            rejected.append({**base, "reason": "donor_calendar_evidence_not_trusted"})
            continue
        donor_date = str(donor.get("date") or "")
        donor_start = str(donor.get("start_time") or "")
        donor_duration = _duration_minutes(donor, problem)
        if not donor_date or not donor_start:
            # A placement swap trades concrete placements; a donor with no
            # parseable slot cannot give the finding host one.
            rejected.append({**base, "reason": "donor_missing_start_time"})
            continue
        if external_calendar_conflict(
            busy, finding.host_club, _parse_date(donor_date), donor_start, current_duration
        ):
            rejected.append({**base, "reason": "external_calendar_conflict_for_finding_host"})
            continue
        if external_calendar_conflict(
            busy, donor_host, _parse_date(finding.original_date), current_start, donor_duration
        ):
            rejected.append({**base, "reason": "external_calendar_conflict_for_donor_host"})
            continue
        trial = copy.deepcopy(candidate)
        finding_tournament = _find_tournament(trial, finding.tournament_id)
        donor_tournament = _find_tournament(trial, donor_id)
        if finding_tournament is None or donor_tournament is None:
            continue
        finding_tournament["date"] = donor_date
        finding_tournament["start_time"] = donor_start
        finding_tournament["manual_booking_reason"] = None
        donor_tournament["date"] = finding.original_date
        donor_tournament["start_time"] = current_start
        _clear_unresolved_placement(trial, finding_tournament, finding.original_date)
        result = verify_candidate(trial, dict(problem))
        if not result.get("ok"):
            rejected.append({**base, "reason": _primary_reason(result), "violations": _codes(result)})
            continue
        out.append(
            RepairOption(
                option_id=(
                    f"{fingerprint[:12]}:{finding.finding_id}:swap_compatible_tournament_placement:"
                    f"{_slug(donor_id)}"
                ),
                finding_id=finding.finding_id,
                action="swap_compatible_tournament_placement",
                tournament_id=finding.tournament_id,
                arguments={
                    "original_date": finding.original_date,
                    "date": donor_date,
                    "start_time": donor_start,
                    "swap_tournament_id": donor_id,
                    "swap_tournament_date": finding.original_date,
                    "swap_tournament_start_time": current_start,
                },
                hard_feasible=True,
                effects={"manual_placement_required": -1, "placement_swap": 1},
                evidence={
                    "verification_ok": True,
                    "responsible_host": finding.host_club,
                    "swap_host": donor_host,
                    "date": donor_date,
                    "start_time": donor_start,
                    "swap_tournament_id": donor_id,
                    **_movable_option_facts(
                        problem,
                        finding.host_club,
                        donor_date,
                        donor_start,
                        current_duration,
                        candidate,
                    ),
                },
            )
        )
        if len(out) >= _MAX_SWAP_OPTIONS:
            break
    return out, rejected


def _candidate_dates(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    current_date: Optional[date],
) -> List[date]:
    """Already-scheduled season dates to try, nearest-current-first.

    Only dates the plan already uses are considered -- this never invents a new
    season date. Dates on the other side of the Christmas split are excluded
    unless cross-half movement is explicitly enabled.
    """
    split = _parse_date(problem.get("christmas_split_date"))
    allow_cross_half = bool(problem.get("allow_cross_half_moves"))
    current_half = planning_half.tournament_half(current_date, split) if current_date else None
    seen = set()
    dates: List[date] = []
    for tournament in candidate.get("tournaments", []):
        if tournament.get("cancelled"):
            continue
        candidate_date = _parse_date(tournament.get("date"))
        if candidate_date is None or candidate_date == current_date or candidate_date in seen:
            continue
        seen.add(candidate_date)
        if not allow_cross_half and current_half is not None:
            if planning_half.tournament_half(candidate_date, split) != current_half:
                continue
        dates.append(candidate_date)
    dates.sort(key=lambda value: abs((value - current_date).days) if current_date else 0)
    return dates[:_MAX_DATE_CANDIDATES]


def _clear_unresolved_placement(
    candidate: Mapping[str, Any],
    tournament: Mapping[str, Any],
    original_date: str,
) -> None:
    entries = candidate.get("unresolved_tournament_placements")
    if not isinstance(entries, list):
        return
    age_group = str(tournament.get("age_group") or "")
    candidate["unresolved_tournament_placements"] = [
        entry
        for entry in entries
        if not (
            isinstance(entry, Mapping)
            and str(entry.get("age_group")) == age_group
            and str(entry.get("date")) == original_date
        )
    ]


def _end_time(start: str, duration_minutes: int) -> str:
    from datetime import datetime, timedelta

    return (datetime.strptime(start, "%H:%M") + timedelta(minutes=duration_minutes)).strftime("%H:%M")
