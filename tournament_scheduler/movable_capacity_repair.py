"""Verified Stage 3 repairs that exploit host-controlled movable ice.

This provider owns the gap between calendar classification and candidate
selection: when a responsible host has a ``movable_busy`` window, Python may
move an existing manual-placement obligation into that window and, when the
current roster conflicts on the target date, enumerate bounded replacement
rosters. Every exposed option is independently verified before it reaches the
controller. The host/arena responsibility is never transferred.
"""

from __future__ import annotations

import copy
from itertools import combinations
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from .host_placement_repair import (
    _calendar_trusted,
    _candidate_weekend_dates,
    _clear_unresolved_placement,
    _effective_busy_intervals,
    _is_manual_slot_failure,
)
from .host_representation import clubs_represent_same_club
from .host_team_missing_repair import (
    RepairOption,
    _candidate_start_times,
    _codes,
    _duration_minutes,
    _find_tournament,
    _identity,
    _participation_counts,
    _primary_violation_reason,
    _regenerate_games,
    _same_date_identities,
    _slug,
    _team_ref,
    candidate_fingerprint,
)
from .planning_contract import (
    _parse_date,
    external_calendar_conflict,
    movable_calendar_opportunity,
    verify_candidate,
)

_MAX_DATES_PER_FINDING = 24
_MAX_REPLACEMENTS = 2
_MAX_ROSTER_VARIANTS = 32
_MAX_OPTIONS_PER_FINDING = 12


def enumerate_movable_capacity_repairs(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    run_id: str = "",
) -> Dict[str, Any]:
    """Expose hard-valid same-host moves into configured movable ice.

    The provider intentionally targets the narrow capability missing from
    ``host_placement_repair``: a date can be legal for the responsible host
    while the *current* roster is not. In that case this provider tries small
    one-for-one participant rotations before giving up on the date.
    """

    verification = verify_candidate(dict(candidate), dict(problem))
    fingerprint = candidate_fingerprint(candidate)
    pinned = {
        str(item)
        for item in (problem.get("manual_adjustments") or {}).get(
            "pinned_tournament_ids", []
        )
    }
    locked_dates = {
        str(item)
        for item in (problem.get("manual_adjustments") or {}).get("locked_dates", [])
    }
    banned_dates = {
        str(item)
        for item in (problem.get("manual_adjustments") or {}).get("banned_dates", [])
    }

    options: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []

    for tournament in candidate.get("tournaments", []):
        if tournament.get("cancelled") or not _is_manual_slot_failure(tournament):
            continue

        tournament_id = str(tournament.get("id") or "")
        host = str(tournament.get("host_club") or "")
        age_group = str(tournament.get("age_group") or "")
        original_date = str(tournament.get("date") or "")
        finding_id = f"movable_capacity:{tournament_id}"
        base = {
            "finding_id": finding_id,
            "tournament_id": tournament_id,
            "host_club": host,
            "age_group": age_group,
            "original_date": original_date,
        }

        if not host:
            rejected.append({**base, "reason": "missing_host_club"})
            continue
        if tournament_id in pinned:
            rejected.append({**base, "reason": "manual_restriction_forbids_mutation"})
            continue
        if not _calendar_trusted(problem, host):
            rejected.append({**base, "reason": "calendar_evidence_not_trusted"})
            continue

        current_date = _parse_date(original_date)
        duration = _duration_minutes(tournament, problem)
        busy = _effective_busy_intervals(problem, candidate)
        date_candidates = _candidate_weekend_dates(problem, current_date)[
            :_MAX_DATES_PER_FINDING
        ]
        finding_option_count = 0

        for target_date in date_candidates:
            date_iso = target_date.isoformat()
            if date_iso in banned_dates:
                rejected.append({**base, "date": date_iso, "reason": "banned_date"})
                continue
            if date_iso in locked_dates:
                rejected.append({**base, "date": date_iso, "reason": "locked_date"})
                continue

            starts: List[Tuple[str, Mapping[str, Any]]] = []
            for start_time in _candidate_start_times(tournament):
                if external_calendar_conflict(
                    busy, host, target_date, start_time, duration
                ):
                    continue
                opportunity = movable_calendar_opportunity(
                    busy, host, target_date, start_time, duration
                )
                if opportunity is not None:
                    starts.append((start_time, opportunity))

            if not starts:
                rejected.append(
                    {**base, "date": date_iso, "reason": "no_movable_capacity"}
                )
                continue

            occupied = _same_date_identities(
                candidate,
                target_date,
                except_tournament_id=tournament_id,
            )
            variants, variant_rejections = _roster_variants(
                candidate,
                problem,
                tournament,
                occupied,
                host,
            )
            for entry in variant_rejections:
                rejected.append({**base, "date": date_iso, **entry})

            if not variants:
                continue

            for start_time, opportunity in starts:
                for roster, removed, added in variants:
                    trial = copy.deepcopy(candidate)
                    trial_tournament = _find_tournament(trial, tournament_id)
                    if trial_tournament is None:
                        rejected.append({**base, "reason": "tournament_not_found"})
                        continue

                    trial_tournament["date"] = date_iso
                    trial_tournament["start_time"] = start_time
                    trial_tournament["teams"] = [dict(team) for team in roster]
                    trial_tournament["manual_booking_reason"] = None
                    _regenerate_games(trial_tournament, problem)
                    _clear_unresolved_placement(
                        trial, trial_tournament, original_date
                    )
                    result = verify_candidate(trial, dict(problem))
                    if not result.get("ok"):
                        rejected.append(
                            {
                                **base,
                                "date": date_iso,
                                "start_time": start_time,
                                "removed_teams": [_team_ref(team) for team in removed],
                                "added_teams": [_team_ref(team) for team in added],
                                "reason": _primary_violation_reason(result),
                                "violations": _codes(result),
                            }
                        )
                        continue

                    action = (
                        "move_to_movable_capacity_reselect_participants"
                        if removed or added
                        else "move_to_movable_capacity"
                    )
                    roster_key = tuple(sorted(_identity(team) for team in roster))
                    option_id = (
                        f"{fingerprint[:12]}:{finding_id}:{action}:"
                        f"{_slug((date_iso, start_time, roster_key))}"
                    )
                    effects = {
                        "manual_placement_required": -1,
                        "date_changed": int(date_iso != original_date),
                        "participant_replacements": len(removed),
                        "movable_capacity_used": 1,
                    }
                    for team in removed:
                        effects[f"participation.{team.get('label') or team.get('club')}"] = -1
                    for team in added:
                        effects[f"participation.{team.get('label') or team.get('club')}"] = 1

                    options.append(
                        RepairOption(
                            option_id=option_id,
                            finding_id=finding_id,
                            action=action,
                            tournament_id=tournament_id,
                            arguments={
                                "original_date": original_date,
                                "date": date_iso,
                                "start_time": start_time,
                                "roster": [_team_ref(team) for team in roster],
                                "removed_teams": [_team_ref(team) for team in removed],
                                "added_teams": [_team_ref(team) for team in added],
                            },
                            hard_feasible=True,
                            effects=effects,
                            evidence={
                                "verification_ok": True,
                                "responsible_host": host,
                                "availability": "movable_busy",
                                "requires_host_confirmation": True,
                                "classification_source": opportunity.get(
                                    "classification_source"
                                )
                                or "configured",
                                "calendar_event": opportunity.get(
                                    "calendar_event", ""
                                ),
                                "host_action_required": opportunity.get("reason")
                                or (
                                    "host-controlled interval may be moved or replaced "
                                    "for an RVV tournament"
                                ),
                                "date": date_iso,
                                "start_time": start_time,
                                "roster_source": (
                                    "alternate" if removed or added else "current"
                                ),
                                "removed_teams": [
                                    _team_ref(team) for team in removed
                                ],
                                "added_teams": [_team_ref(team) for team in added],
                            },
                        )
                    )
                    finding_option_count += 1
                    if finding_option_count >= _MAX_OPTIONS_PER_FINDING:
                        rejected.append(
                            {
                                **base,
                                "reason": "bounded_option_budget_exhausted",
                                "option_budget": _MAX_OPTIONS_PER_FINDING,
                            }
                        )
                        break
                if finding_option_count >= _MAX_OPTIONS_PER_FINDING:
                    break
            if finding_option_count >= _MAX_OPTIONS_PER_FINDING:
                break

        if finding_option_count == 0:
            rejected.append(
                {
                    **base,
                    "reason": "no_verified_movable_capacity_repair",
                    "dates_checked": [d.isoformat() for d in date_candidates],
                }
            )

    return {
        "run_id": run_id,
        "candidate_fingerprint": fingerprint,
        "verification": verification,
        "options": [option.to_dict() for option in options],
        "rejected_candidates": rejected,
    }


def apply_movable_capacity_repair_option(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    option_id: str,
    expected_fingerprint: str,
    run_id: str = "",
) -> Dict[str, Any]:
    """Atomically apply one verified movable-capacity option."""

    before = candidate_fingerprint(candidate)
    if before != expected_fingerprint:
        return {
            "ok": False,
            "reason": "stale_candidate_fingerprint",
            "before_fingerprint": before,
            "expected_fingerprint": expected_fingerprint,
        }

    repair_set = enumerate_movable_capacity_repairs(
        candidate, problem, run_id=run_id
    )
    option = next(
        (entry for entry in repair_set["options"] if entry["option_id"] == option_id),
        None,
    )
    if option is None:
        return {
            "ok": False,
            "reason": "unknown_or_stale_option",
            "before_fingerprint": before,
        }

    mutated = copy.deepcopy(candidate)
    tournament = _find_tournament(mutated, option["tournament_id"])
    if tournament is None:
        return {
            "ok": False,
            "reason": "tournament_not_found",
            "before_fingerprint": before,
        }

    arguments = option["arguments"]
    tournament["date"] = str(arguments["date"])
    tournament["start_time"] = str(arguments["start_time"])
    tournament["teams"] = [dict(team) for team in arguments["roster"]]
    tournament["manual_booking_reason"] = None
    _regenerate_games(tournament, problem)
    _clear_unresolved_placement(
        mutated, tournament, str(arguments["original_date"])
    )

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


def _roster_variants(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    tournament: Mapping[str, Any],
    occupied: Iterable[Tuple[str, str, str]],
    host: str,
) -> Tuple[
    List[Tuple[List[Mapping[str, Any]], List[Mapping[str, Any]], List[Mapping[str, Any]]]],
    List[Dict[str, Any]],
]:
    """Current roster or bounded same-age one-for-one replacements."""

    occupied_ids = set(occupied)
    current = [dict(team) for team in tournament.get("teams", []) or []]
    conflicting = [team for team in current if _identity(team) in occupied_ids]
    if not conflicting:
        return [(current, [], [])], []

    if len(conflicting) > _MAX_REPLACEMENTS:
        return [], [
            {
                "reason": "participant_replacement_budget_exhausted",
                "conflicting_teams": [_team_ref(team) for team in conflicting],
                "max_replacements": _MAX_REPLACEMENTS,
            }
        ]

    fixed = [team for team in current if _identity(team) not in occupied_ids]
    fixed_ids = {_identity(team) for team in fixed}
    counts = _participation_counts(candidate)
    registered = [
        team
        for team in problem.get("teams", [])
        if str(team.get("age_group") or "") == str(tournament.get("age_group") or "")
        and _identity(team) not in occupied_ids
        and _identity(team) not in fixed_ids
    ]
    registered.sort(key=lambda team: _replacement_rank(problem, team, counts))

    variants: List[
        Tuple[List[Mapping[str, Any]], List[Mapping[str, Any]], List[Mapping[str, Any]]]
    ] = []
    rejected: List[Dict[str, Any]] = []
    examined = 0
    for replacement_tuple in combinations(registered, len(conflicting)):
        examined += 1
        if examined > _MAX_ROSTER_VARIANTS:
            rejected.append(
                {
                    "reason": "replacement_roster_search_budget_exhausted",
                    "variants_checked": _MAX_ROSTER_VARIANTS,
                }
            )
            break
        replacements = [dict(team) for team in replacement_tuple]
        roster = [*fixed, *replacements]
        if len({_identity(team) for team in roster}) != len(roster):
            continue
        if not any(
            clubs_represent_same_club(str(team.get("club") or ""), host)
            for team in roster
        ):
            rejected.append(
                {
                    "reason": "replacement_roster_loses_host_representation",
                    "removed_teams": [_team_ref(team) for team in conflicting],
                    "added_teams": [_team_ref(team) for team in replacements],
                }
            )
            continue
        variants.append((roster, conflicting, replacements))

    if not variants and not rejected:
        rejected.append(
            {
                "reason": "no_registered_replacement_roster",
                "conflicting_teams": [_team_ref(team) for team in conflicting],
            }
        )
    return variants, rejected


def _replacement_rank(
    problem: Mapping[str, Any],
    team: Mapping[str, Any],
    counts: Mapping[Tuple[str, str, str], int],
) -> Tuple[int, str, str]:
    """Prefer teams furthest below their participation target, deterministically."""

    target = team.get("target_tournament_count")
    if not isinstance(target, int) or isinstance(target, bool):
        target = (problem.get("participation_targets_by_age_group") or {}).get(
            team.get("age_group")
        )
    if not isinstance(target, int) or isinstance(target, bool):
        target = problem.get("target_tournament_count")
    deficit = 0
    if isinstance(target, int) and not isinstance(target, bool):
        deficit = max(0, target - counts.get(_identity(team), 0))
    return (-deficit, str(team.get("club") or ""), str(team.get("label") or ""))
