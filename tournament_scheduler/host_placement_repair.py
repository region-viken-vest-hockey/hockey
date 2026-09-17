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
  slot for the same host.

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
from .planning_contract import _parse_date, external_calendar_conflict, verify_candidate

# The canonical marker SeasonPlanner writes when no verified slot was found for
# the responsible host (see season_planner.py and the manual-schedule export).
# It deliberately excludes the calendar-untrusted reason: an unverified
# calendar cannot be repaired by slot search, only booked by hand.
MANUAL_SLOT_FAILURE_MARKER = "må plasseres manuelt"

# Bounded neighborhoods: enough alternatives for the controller to choose from
# without turning one unplaced tournament into a season-wide search.
_MAX_START_TIME_OPTIONS = 6
_MAX_DATE_CANDIDATES = 8
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
        date_options, date_rejected = _date_options(
            candidate, problem, tournament, finding, fingerprint
        )
        options.extend(time_options)
        options.extend(date_options)
        rejected.extend(time_rejected)
        rejected.extend(date_rejected)
        if not time_options and not date_options:
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
    tournament["date"] = str(arguments["date"])
    tournament["start_time"] = str(arguments["start_time"])
    tournament["manual_booking_reason"] = None
    _clear_unresolved_placement(mutated, tournament, original_date)

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


def _calendar_trusted(problem: Mapping[str, Any], host_club: str) -> bool:
    statuses = problem.get("club_calendar_status") or {}
    if not statuses:
        return False
    constituents = constituent_clubs(host_club) or [host_club]
    return all(statuses.get(part) == "known" for part in constituents)


def _busy_intervals(problem: Mapping[str, Any]) -> Mapping[str, Any]:
    return problem.get("club_busy_intervals") or {}


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
    busy = _busy_intervals(problem)
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
    busy = _busy_intervals(problem)
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
                },
            )
        )
        if len(out) >= _MAX_OPTIONS:
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
