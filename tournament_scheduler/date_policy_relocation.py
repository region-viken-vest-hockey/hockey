"""Deterministic batch relocation of tournaments off canonical excluded dates.

Date admissibility is a canonical hard rule (see :mod:`tournament_scheduler.date_policy`).
A promoted season can carry several tournaments on excluded dates at once --
for example after the policy was strengthened or a candidate was produced by a
path that predated the rule. Because every ordinary repair provider commits
only a fully verified candidate, no single-tournament repair can make progress
while the other excluded-date tournaments remain, so this module owns the
narrow batch operation the maintenance loop cannot express: move every
excluded-date tournament to another admissible date for the *same* responsible
host, then verify the whole candidate once.

It never changes a host, a roster, a participation count or a hard rule, and it
never invents a date outside the planning window or outside the tournament's
own planning half (unless cross-half movement is explicitly enabled). An
obligation that cannot be relocated is reported as unresolved with the
deterministic rejection reasons for every date considered.
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from . import planning_half
from .candidate_weekends import season_weekend_dates
from .date_policy import problem_date_exclusions
from .host_team_missing_repair import (
    RepairOption,
    _candidate_start_times,
    candidate_fingerprint,
)
from .planning_contract import (
    _parse_date,
    _team_identity,
    external_calendar_conflict,
    verify_candidate,
)

# Stable finding identity for the batch relocation, surfaced by
# ``season_maintenance`` so the operation is reachable through the ordinary
# findings/repair boundary rather than a bespoke command.
RELOCATION_FINDING_PREFIX = "date_policy_relocation"
_MAX_DATES_PER_TOURNAMENT = 60
_MAX_START_TIMES = 14


def relocation_finding_id(tournament_id: str) -> str:
    """Stable finding identity for one excluded-date tournament's relocation."""
    return f"{RELOCATION_FINDING_PREFIX}:{tournament_id}"


def relocate_excluded_date_tournaments(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    demote_unresolved: bool = True,
) -> Dict[str, Any]:
    """Return one fully verified candidate with excluded-date tournaments moved.

    The returned dict always contains ``candidate`` (the best effort plan),
    ``moves`` (successful relocations), ``unresolved`` (obligations that could
    not be relocated, with rejection evidence) and ``verification`` (the
    independent verifier result for ``candidate``).

    When *demote_unresolved* is true, an obligation that could not be
    relocated in the bounded search is demoted to an explicit
    ``unresolved_tournament_placements`` obligation (with its rejection
    evidence) instead of being left scheduled on a forbidden date -- the
    honest "no legal replacement found" outcome the date policy requires.
    """
    working = copy.deepcopy(dict(candidate))
    excluded = set(problem_date_exclusions(problem))
    targets = [
        tournament
        for tournament in working.get("tournaments", [])
        if not tournament.get("cancelled")
        and _parse_date(tournament.get("date")) in excluded
    ]
    # Latest excluded dates have the smallest admissible neighborhood (for
    # example Easter at the very end of the season window), so give them first
    # choice of the remaining admissible dates.
    targets.sort(key=lambda tournament: (str(tournament.get("date")), str(tournament.get("id"))), reverse=True)

    moves: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []
    for tournament in targets:
        tournament_id = str(tournament.get("id"))
        original_date = str(tournament.get("date") or "")
        anchor = _parse_date(original_date)
        rejection_reasons: List[Dict[str, Any]] = []
        placement = _find_relocation(
            working, problem, tournament_id, anchor, excluded, rejection_reasons
        )
        if placement is None:
            unresolved.append(
                {
                    "finding_id": relocation_finding_id(tournament_id),
                    "tournament_id": tournament_id,
                    "original_date": original_date,
                    "host_club": tournament.get("host_club"),
                    "age_group": tournament.get("age_group"),
                    "reason": "no_admissible_date_found",
                    "rejected_candidates": rejection_reasons,
                }
            )
            continue
        new_date, start_time = placement
        moved = _find_tournament(working, tournament_id)
        if moved is None:
            continue
        moved["date"] = new_date.isoformat()
        moved["start_time"] = start_time
        # A relocated tournament is no longer a manual-placement item.
        if moved.get("manual_booking_reason") is not None:
            moved["manual_booking_reason"] = None
        moves.append(
            {
                "finding_id": relocation_finding_id(tournament_id),
                "tournament_id": tournament_id,
                "host_club": tournament.get("host_club"),
                "age_group": tournament.get("age_group"),
                "from_date": original_date,
                "to_date": new_date.isoformat(),
                "start_time": start_time,
            }
        )

    verification = verify_candidate(working, dict(problem))
    demotions: List[Dict[str, Any]] = []
    if demote_unresolved and unresolved:
        from .placement_normalization import REASON_EXCLUDED_DATE, demote_tournament_to_unplaced

        for entry in unresolved:
            obligation = demote_tournament_to_unplaced(
                working,
                str(entry["tournament_id"]),
                reason=REASON_EXCLUDED_DATE,
                problem=problem,
                evidence={
                    "original_date": entry["original_date"],
                    "reason": "no_admissible_replacement_found",
                    "rejected_candidates": list(entry["rejected_candidates"]),
                },
            )
            if obligation is not None:
                demotions.append(dict(obligation))
        verification = verify_candidate(working, dict(problem))
    return {
        "candidate": working,
        "candidate_fingerprint": candidate_fingerprint(working),
        "moves": moves,
        "unresolved": unresolved,
        "demotions": demotions,
        "verification": verification,
    }


def _find_relocation(
    working: Mapping[str, Any],
    problem: Mapping[str, Any],
    tournament_id: str,
    anchor: Optional[date],
    excluded: Set[date],
    rejection_reasons: List[Dict[str, Any]],
) -> Optional[Tuple[date, str]]:
    tournament = _find_tournament(working, tournament_id)
    if tournament is None or anchor is None:
        return None
    for candidate_date in _admissible_dates(problem, anchor, excluded):
        if candidate_date == anchor:
            continue
        start_time, reason = _first_available_start_time(working, problem, tournament, candidate_date)
        if start_time is None:
            rejection_reasons.append(
                {"date": candidate_date.isoformat(), "reason": reason or "no_available_slot"}
            )
            continue
        return candidate_date, start_time
    return None


def _admissible_dates(
    problem: Mapping[str, Any],
    anchor: date,
    excluded: Set[date],
) -> List[date]:
    split = _parse_date(problem.get("christmas_split_date"))
    allow_cross_half = bool(problem.get("allow_cross_half_moves"))
    anchor_half = planning_half.tournament_half(anchor, split)
    dates = [
        candidate_date
        for candidate_date in season_weekend_dates(problem)
        if candidate_date not in excluded
        and (allow_cross_half or planning_half.tournament_half(candidate_date, split) == anchor_half)
    ]
    dates.sort(key=lambda candidate_date: (abs((candidate_date - anchor).days), candidate_date))
    return dates[:_MAX_DATES_PER_TOURNAMENT]


def _first_available_start_time(
    working: Mapping[str, Any],
    problem: Mapping[str, Any],
    tournament: Mapping[str, Any],
    candidate_date: date,
) -> Tuple[Optional[str], Optional[str]]:
    last_reason: Optional[str] = None
    for start_time in _candidate_start_times(tournament)[:_MAX_START_TIMES]:
        reason = _placement_conflict(
            working, problem, str(tournament.get("id")), candidate_date, start_time
        )
        if reason is None:
            return start_time, None
        last_reason = reason
    return None, last_reason


def _placement_conflict(
    working: Mapping[str, Any],
    problem: Mapping[str, Any],
    tournament_id: str,
    candidate_date: date,
    start_time: str,
) -> Optional[str]:
    """Return a deterministic rejection reason, or ``None`` when the move fits."""
    tournament = _find_tournament(working, tournament_id)
    if tournament is None:
        return "tournament_not_found"
    date_iso = candidate_date.isoformat()
    duration = _occupancy_minutes(tournament, problem)
    host_club = str(tournament.get("host_club") or "")
    calendar_status = (problem.get("club_calendar_status") or {}).get(host_club, "unknown")
    if calendar_status == "known" and external_calendar_conflict(
        problem.get("club_busy_intervals") or {},
        host_club,
        candidate_date,
        start_time,
        duration,
    ):
        return "host_external_calendar_conflict"

    start_minutes = _time_to_minutes(start_time)
    if start_minutes is None:
        return "invalid_start_time"
    end_minutes = start_minutes + duration
    arena = str(tournament.get("arena") or "")
    teams = {_team_identity(team) for team in tournament.get("teams", []) if isinstance(team, Mapping)}

    for other in working.get("tournaments", []):
        if other.get("cancelled") or str(other.get("id")) == tournament_id:
            continue
        if str(other.get("date") or "") != date_iso:
            continue
        other_teams = {
            _team_identity(team) for team in other.get("teams", []) if isinstance(team, Mapping)
        }
        if teams & other_teams:
            return "team_already_plays_on_date"
        if arena and str(other.get("arena") or "") == arena:
            other_start = _time_to_minutes(str(other.get("start_time") or ""))
            if other_start is None:
                continue
            other_end = other_start + _occupancy_minutes(other, problem)
            if start_minutes < other_end and other_start < end_minutes:
                return "arena_interval_conflict"
    return None


def _occupancy_minutes(tournament: Mapping[str, Any], problem: Mapping[str, Any]) -> int:
    """Configured base ice time plus the canonical per-round buffer.

    Mirrors :func:`tournament_scheduler.occupancy.tournament_required_ice_minutes`
    (the duration the verifier actually uses), so an arena overlap is never
    missed because a repair estimate used only the flat configured ice time.
    """
    ice_time = (problem.get("ice_time_minutes") or {}).get(tournament.get("age_group"))
    rounds = max(
        (int(game.get("round_number") or 0) for game in tournament.get("games") or []),
        default=0,
    )
    if not isinstance(ice_time, int) or ice_time <= 0 or rounds <= 0:
        return 0
    return ice_time + 5 * rounds


def _time_to_minutes(value: str) -> Optional[int]:
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
    except (AttributeError, TypeError, ValueError):
        return None
    return hour * 60 + minute


def _find_tournament(candidate: Mapping[str, Any], tournament_id: str) -> Optional[Dict[str, Any]]:
    return next(
        (
            tournament
            for tournament in candidate.get("tournaments", [])
            if str(tournament.get("id")) == str(tournament_id)
        ),
        None,
    )


# ---------------------------------------------------------------------------
# Repair-provider boundary (findings/repair/apply)
# ---------------------------------------------------------------------------


def enumerate_date_policy_relocations(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    run_id: str = "",
) -> Dict[str, Any]:
    """One verified batch relocation option per excluded-date tournament.

    The generic single-tournament repair providers cannot repair a state with
    several excluded-date tournaments at once (each commits only a fully valid
    candidate), so this provider exposes the batch mutation through the same
    option/apply boundary. Applying any one option relocates every excluded
    date tournament the bounded search can place and demotes the rest to
    explicit unresolved obligations.
    """
    fingerprint = candidate_fingerprint(candidate)
    result = relocate_excluded_date_tournaments(candidate, problem)
    options: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []
    if not result["moves"] and not result["demotions"]:
        return {
            "run_id": run_id,
            "candidate_fingerprint": fingerprint,
            "options": [],
            "rejected_candidates": [],
            "verification": result["verification"],
        }
    if not result["verification"].get("ok"):
        rejected.append(
            {
                "reason": "relocation_candidate_not_hard_valid",
                "remaining_violation_codes": sorted(
                    {str(v.get("code")) for v in result["verification"].get("violations", [])}
                ),
            }
        )
        return {
            "run_id": run_id,
            "candidate_fingerprint": fingerprint,
            "options": [],
            "rejected_candidates": rejected,
            "verification": result["verification"],
        }

    moved_ids = {str(move["tournament_id"]) for move in result["moves"]}
    demoted_ids = {
        str(entry.get("source_tournament_id") or entry.get("id")) for entry in result["demotions"]
    }
    for move in result["moves"]:
        options.append(
            RepairOption(
                option_id=f"{fingerprint[:12]}:{RELOCATION_FINDING_PREFIX}:{move['tournament_id']}",
                finding_id=relocation_finding_id(str(move["tournament_id"])),
                action="relocate_excluded_date_tournament",
                tournament_id=str(move["tournament_id"]),
                arguments={},
                hard_feasible=True,
                effects={"date_changed": 1, "holiday_date_relocated": 1},
                evidence={
                    "verification_ok": True,
                    "from_date": move["from_date"],
                    "to_date": move["to_date"],
                    "start_time": move["start_time"],
                    "batch_relocation": True,
                    "batch_move_count": len(result["moves"]),
                },
            )
        )
    for entry in result["demotions"]:
        source_id = str(entry.get("source_tournament_id") or entry.get("id") or "")
        options.append(
            RepairOption(
                option_id=f"{fingerprint[:12]}:{RELOCATION_FINDING_PREFIX}:{source_id}",
                finding_id=relocation_finding_id(source_id),
                action="relocate_excluded_date_tournament",
                tournament_id=source_id,
                arguments={},
                hard_feasible=True,
                effects={"unplaced_obligation_created": 1},
                evidence={
                    "verification_ok": True,
                    "unresolved": True,
                    "batch_relocation": True,
                    "batch_demoted_count": len(result["demotions"]),
                },
            )
        )
    # A move and a demotion can never name the same tournament; keep them
    # documented so the ids stay unique even if that invariant changes.
    assert moved_ids.isdisjoint(demoted_ids)
    return {
        "run_id": run_id,
        "candidate_fingerprint": fingerprint,
        "options": [option.to_dict() for option in options],
        "rejected_candidates": rejected,
        "verification": result["verification"],
    }


def apply_date_policy_relocation_option(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    option_id: str,
    expected_fingerprint: str,
    run_id: str = "",
) -> Dict[str, Any]:
    """Atomically apply the batch relocation and return the verified candidate."""
    before = candidate_fingerprint(candidate)
    if before != expected_fingerprint:
        return {
            "ok": False,
            "reason": "stale_candidate_fingerprint",
            "before_fingerprint": before,
            "expected_fingerprint": expected_fingerprint,
        }
    repair_set = enumerate_date_policy_relocations(candidate, problem, run_id=run_id)
    option = next(
        (entry for entry in repair_set["options"] if entry["option_id"] == option_id), None
    )
    if option is None:
        return {"ok": False, "reason": "unknown_or_stale_option", "before_fingerprint": before}
    result = relocate_excluded_date_tournaments(candidate, problem)
    if not result["verification"].get("ok"):
        return {
            "ok": False,
            "reason": "verification_failed",
            "before_fingerprint": before,
            "verification": result["verification"],
        }
    return {
        "ok": True,
        "candidate": result["candidate"],
        "option_id": option_id,
        "before_fingerprint": before,
        "after_fingerprint": result["candidate_fingerprint"],
        "verification": result["verification"],
        "moves": result["moves"],
        "demotions": result["demotions"],
        "effects": option.get("effects", {}),
    }
