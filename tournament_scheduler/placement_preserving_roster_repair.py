"""Verified roster repairs that keep an already-valid tournament placement.

When a tournament's host, date, arena and start time are all already legal and
the blocker is *which* teams are selected (a team double-booked on the same
date, a duplicated team in one tournament), the cheapest real repair is to
reselect the conflicting participants -- not to move the whole tournament. This
provider owns exactly that one substitution family:

* the placement fields (host/date/arena/start time) are never touched;
* only same-age-group registered teams that are not already committed on that
  date may replace a conflicting team;
* replacements are ranked by participation need (furthest below the canonical
  target first) so a repair tends to improve participation balance instead of
  silently degrading it;
* every exposed option is independently verified for the *whole* season before
  it reaches the controller, and games are regenerated for the mutated
  tournament.

It is a planner-neutral provider on the common ``local_repair_options``
boundary, so Stage 3 and promoted-season maintenance reach the same options.
This ordering matters: a valid placement must not be discarded by a broader
date/host/search provider merely because the initial roster conflicts, when a
hard-valid lower-cost roster substitution exists.
"""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .application.decisions import DecisionContext
from .host_representation import clubs_represent_same_club
from .host_team_missing_repair import (
    RepairOption,
    TeamIdentity,
    _codes,
    _find_tournament,
    _identity,
    _participation_counts,
    _participation_limit_reason,
    _participations_by_half,
    _primary_violation_reason,
    _regenerate_games,
    _same_date_identities,
    _slug,
    _team_ref,
    candidate_fingerprint,
)
from .participation_targets import resolve_season_target
from .planning_contract import _parse_date, verify_candidate

# The family owns participant-only repairs for these hard findings. Anything
# else (a missing host team, a bad date, an arena conflict) belongs to another
# provider, so this one stays silent for it.
PLACEMENT_PRESERVING_VIOLATION_CODES = frozenset(
    {
        "duplicate_participation_same_date",
        "duplicate_team_in_tournament",
    }
)

# A bounded local neighborhood: one or two substitutions are enough for the
# production double-booking shape, and a wide roster search belongs to the
# bounded neighborhood-search provider instead.
_MAX_REPLACEMENTS = 2
_MAX_ROSTER_VARIANTS = 32
_MAX_OPTIONS_PER_FINDING = 8


@dataclass(frozen=True)
class _Conflict:
    tournament_id: str
    age_group: str
    host_club: str
    date: str
    drop: Tuple[TeamIdentity, ...]
    keep_first: Tuple[TeamIdentity, ...]
    reasons: Tuple[str, ...]


def enumerate_placement_preserving_roster_repairs(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    run_id: str = "",
) -> Dict[str, Any]:
    """Return verified placement-preserving roster options plus rejection evidence."""
    verification = verify_candidate(dict(candidate), dict(problem))
    fingerprint = candidate_fingerprint(candidate)
    pinned = {
        str(item)
        for item in (problem.get("manual_adjustments") or {}).get(
            "pinned_tournament_ids", []
        )
    }
    active_codes = {
        str(violation.get("code"))
        for violation in verification.get("violations", []) or []
    }
    options: List[RepairOption] = []
    rejected: List[Dict[str, Any]] = []

    if not active_codes & PLACEMENT_PRESERVING_VIOLATION_CODES:
        return {
            "run_id": run_id,
            "candidate_fingerprint": fingerprint,
            "verification": verification,
            "options": [],
            "rejected_candidates": [],
        }

    for conflict in _conflicts(candidate):
        tournament = _find_tournament(candidate, conflict.tournament_id)
        if tournament is None:
            rejected.append(
                {
                    "finding_id": _finding_id(conflict.tournament_id),
                    "tournament_id": conflict.tournament_id,
                    "reason": "tournament_not_found",
                }
            )
            continue
        finding_id = _finding_id(conflict.tournament_id)
        base = {
            "finding_id": finding_id,
            "tournament_id": conflict.tournament_id,
            "host_club": conflict.host_club,
            "age_group": conflict.age_group,
            "date": conflict.date,
            "conflicting_teams": [_team_ref_by_identity(identity) for identity in conflict.drop],
            "conflict_reasons": list(conflict.reasons),
        }
        if conflict.tournament_id in pinned:
            rejected.append({**base, "reason": "manual_restriction_forbids_mutation"})
            continue
        if len(conflict.drop) > _MAX_REPLACEMENTS:
            rejected.append(
                {
                    **base,
                    "reason": "participant_replacement_budget_exhausted",
                    "max_replacements": _MAX_REPLACEMENTS,
                }
            )
            continue

        finding_options, finding_rejections = _finding_options(
            candidate,
            problem,
            tournament,
            conflict,
            finding_id,
            fingerprint,
            verification,
        )
        options.extend(finding_options)
        rejected.extend(finding_rejections)
        if not finding_options:
            rejected.append(
                {
                    **base,
                    "reason": "no_verified_placement_preserving_roster_repair",
                }
            )

    return {
        "run_id": run_id,
        "candidate_fingerprint": fingerprint,
        "verification": verification,
        "options": [option.to_dict() for option in options],
        "rejected_candidates": rejected,
    }


def build_placement_preserving_roster_decision_context(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    run_id: str,
    candidate_ref: Optional[str] = None,
) -> DecisionContext:
    repair_set = enumerate_placement_preserving_roster_repairs(
        candidate, problem, run_id=run_id
    )
    legal_ids = [option["option_id"] for option in repair_set["options"]]
    verification = repair_set["verification"]
    return DecisionContext(
        run_id=run_id,
        capability="placement_preserving_roster_repair",
        stage="stage3",
        objective=(
            "Choose one repository-verified roster substitution that keeps the "
            "current valid placement, or escalate if every substitution is illegal."
        ),
        facts={
            "candidate_fingerprint": repair_set["candidate_fingerprint"],
            "repair_options": repair_set["options"],
            "rejected_candidates": repair_set["rejected_candidates"],
        },
        baseline_hard_violations=tuple(
            f"{violation.get('code')}: {violation.get('message')}"
            for violation in verification.get("violations", [])
        ),
        warnings=()
        if legal_ids
        else (
            "No placement-preserving roster substitution is hard-valid; "
            "rejected_candidates explains why and a broader provider owns the finding.",
        ),
        candidate_ref=candidate_ref,
        available_actions=("apply_repair_option", "optimize_plan", "request_operator")
        if legal_ids
        else ("optimize_plan", "request_operator"),
        action_parameters={
            "apply_repair_option": {
                "option_id": {
                    "type": "string",
                    "enum": legal_ids,
                    "description": "Repository-generated repair option id to apply.",
                },
                "candidate_fingerprint": {
                    "type": "string",
                    "enum": [repair_set["candidate_fingerprint"]],
                },
            }
        }
        if legal_ids
        else {},
    )


def apply_placement_preserving_roster_repair_option(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    *,
    option_id: str,
    expected_fingerprint: str,
    run_id: str = "",
) -> Dict[str, Any]:
    """Atomically apply one verified roster option against an unchanged candidate."""
    before = candidate_fingerprint(candidate)
    if before != expected_fingerprint:
        return {
            "ok": False,
            "reason": "stale_candidate_fingerprint",
            "before_fingerprint": before,
            "expected_fingerprint": expected_fingerprint,
        }

    repair_set = enumerate_placement_preserving_roster_repairs(
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
    tournament["teams"] = [dict(team) for team in arguments["roster"]]
    _regenerate_games(tournament, problem)

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
# Conflict detection
# ---------------------------------------------------------------------------


def _conflicts(candidate: Mapping[str, Any]) -> List[_Conflict]:
    """Deterministic participant conflicts, one finding per affected tournament."""
    tournaments = [t for t in candidate.get("tournaments", []) if not t.get("cancelled")]
    occurrences: Dict[TeamIdentity, List[Tuple[Any, str]]] = {}
    for tournament in tournaments:
        t_id = str(tournament.get("id") or "")
        t_date = _parse_date(tournament.get("date"))
        for team in tournament.get("teams", []) or []:
            identity = _identity(team)
            occurrences.setdefault(identity, []).append((t_date, t_id))

    # same-date double bookings: identity -> tournament ids on that date
    same_date_conflict: Dict[TeamIdentity, set[str]] = {}
    for identity, entries in occurrences.items():
        by_date: Dict[Any, set[str]] = {}
        for t_date, t_id in entries:
            by_date.setdefault(t_date, set()).add(t_id)
        for t_ids in by_date.values():
            if len(t_ids) > 1:
                same_date_conflict.setdefault(identity, set()).update(t_ids)

    conflicts: List[_Conflict] = []
    for tournament in tournaments:
        t_id = str(tournament.get("id") or "")
        counts: Dict[TeamIdentity, int] = {}
        for team in tournament.get("teams", []) or []:
            identity = _identity(team)
            counts[identity] = counts.get(identity, 0) + 1

        drop: List[TeamIdentity] = []
        keep_first: List[TeamIdentity] = []
        reasons: List[str] = []
        for identity, count in counts.items():
            in_same_date_conflict = t_id in same_date_conflict.get(identity, set())
            if in_same_date_conflict:
                drop.extend([identity] * count)
                reasons.append("duplicate_participation_same_date")
            elif count > 1:
                # Keep the first occurrence, replace the extras.
                drop.extend([identity] * (count - 1))
                keep_first.append(identity)
                reasons.append("duplicate_team_in_tournament")
        if not drop:
            continue
        conflicts.append(
            _Conflict(
                tournament_id=t_id,
                age_group=str(tournament.get("age_group") or ""),
                host_club=str(tournament.get("host_club") or ""),
                date=str(tournament.get("date") or ""),
                drop=tuple(sorted(drop)),
                keep_first=tuple(sorted(set(keep_first))),
                reasons=tuple(sorted(set(reasons))),
            )
        )
    conflicts.sort(key=lambda conflict: conflict.tournament_id)
    return conflicts


def _finding_id(tournament_id: str) -> str:
    return f"placement_preserving_roster:{tournament_id}"


# ---------------------------------------------------------------------------
# Option enumeration
# ---------------------------------------------------------------------------


def _finding_options(
    candidate: Mapping[str, Any],
    problem: Mapping[str, Any],
    tournament: Mapping[str, Any],
    conflict: _Conflict,
    finding_id: str,
    fingerprint: str,
    before_verification: Mapping[str, Any],
) -> Tuple[List[RepairOption], List[Dict[str, Any]]]:
    rejected: List[Dict[str, Any]] = []
    t_date = _parse_date(tournament.get("date"))
    age_group = str(tournament.get("age_group") or "")
    host = str(tournament.get("host_club") or "")
    current = [dict(team) for team in tournament.get("teams", []) or []]
    drop_set = set(conflict.drop)
    keep_first = set(conflict.keep_first)

    fixed: List[Mapping[str, Any]] = []
    seen: set[TeamIdentity] = set()
    for team in current:
        identity = _identity(team)
        if identity in drop_set:
            # A duplicate-in-tournament identity keeps its first occurrence and
            # only replaces the extras; a same-date identity is dropped whole.
            if identity in keep_first and identity not in seen:
                seen.add(identity)
                fixed.append(team)
            continue
        if identity in seen:
            continue
        seen.add(identity)
        fixed.append(team)

    same_date = _same_date_identities(
        candidate, t_date, except_tournament_id=conflict.tournament_id
    )
    fixed_ids = {_identity(team) for team in fixed}
    counts = _participation_counts(candidate)
    half_counts = _participations_by_half(candidate, problem)
    registered = [
        team
        for team in problem.get("teams", []) or []
        if str(team.get("age_group") or "") == age_group
        and _identity(team) not in drop_set
        and _identity(team) not in fixed_ids
    ]
    registered.sort(key=lambda team: _replacement_rank(problem, team, counts))

    options: List[RepairOption] = []
    variants_checked = 0
    for replacement_tuple in combinations(registered, len(conflict.drop)):
        variants_checked += 1
        if variants_checked > _MAX_ROSTER_VARIANTS:
            rejected.append(
                {
                    "finding_id": finding_id,
                    "tournament_id": conflict.tournament_id,
                    "reason": "replacement_roster_search_budget_exhausted",
                    "variants_checked": _MAX_ROSTER_VARIANTS,
                }
            )
            break
        replacements = [dict(team) for team in replacement_tuple]
        base = {
            "finding_id": finding_id,
            "tournament_id": conflict.tournament_id,
            "removed_teams": [_team_ref_by_identity(identity) for identity in conflict.drop],
            "added_teams": [_team_ref(team) for team in replacements],
        }
        replacement_ids = {_identity(team) for team in replacements}
        if replacement_ids & fixed_ids:
            rejected.append({**base, "reason": "already_participating"})
            continue
        if replacement_ids & same_date:
            rejected.append({**base, "reason": "already_plays_same_date"})
            continue
        blocked = _participation_block_reason(
            problem,
            replacements,
            counts,
            half_counts,
            t_date,
            conflict.tournament_id,
        )
        if blocked is not None:
            rejected.append({**base, "reason": blocked})
            continue
        roster = [*fixed, *replacements]
        if not any(
            clubs_represent_same_club(str(team.get("club") or ""), host)
            for team in roster
        ):
            rejected.append(
                {**base, "reason": "replacement_roster_loses_host_representation"}
            )
            continue

        trial = copy.deepcopy(candidate)
        trial_tournament = _find_tournament(trial, conflict.tournament_id)
        if trial_tournament is None:
            rejected.append({**base, "reason": "tournament_not_found"})
            continue
        trial_tournament["teams"] = [dict(team) for team in roster]
        _regenerate_games(trial_tournament, problem)
        result = verify_candidate(trial, dict(problem))
        if not result.get("ok"):
            rejected.append(
                {
                    **base,
                    "reason": _primary_violation_reason(result),
                    "violations": _codes(result),
                }
            )
            continue

        removed_labels = [identity[1] or identity[0] for identity in conflict.drop]
        added_labels = [str(team.get("label") or "") for team in replacements]
        option_id = (
            f"{fingerprint[:12]}:{finding_id}:replace_participants:"
            f"{_slug((conflict.tournament_id, tuple(added_labels), tuple(removed_labels)))}"
        )
        options.append(
            RepairOption(
                option_id=option_id,
                finding_id=finding_id,
                action="replace_participants",
                tournament_id=conflict.tournament_id,
                arguments={
                    "roster": [_team_ref(team) for team in roster],
                    "removed_teams": [
                        _team_ref_by_identity(identity) for identity in conflict.drop
                    ],
                    "added_teams": [_team_ref(team) for team in replacements],
                },
                hard_feasible=True,
                effects=_effects(
                    current,
                    roster,
                    conflict.drop,
                    replacements,
                    before_verification,
                    result,
                ),
                evidence={
                    "verification_ok": True,
                    "placement_unchanged": True,
                    "host_club": host,
                    "date": conflict.date,
                    "arena": tournament.get("arena"),
                    "start_time": tournament.get("start_time"),
                    "conflict_reasons": list(conflict.reasons),
                    "removed_teams": [
                        _team_ref_by_identity(identity) for identity in conflict.drop
                    ],
                    "added_teams": [_team_ref(team) for team in replacements],
                },
            )
        )
        if len(options) >= _MAX_OPTIONS_PER_FINDING:
            rejected.append(
                {
                    "finding_id": finding_id,
                    "tournament_id": conflict.tournament_id,
                    "reason": "bounded_option_budget_exhausted",
                    "option_budget": _MAX_OPTIONS_PER_FINDING,
                }
            )
            break

    if not options and not rejected:
        rejected.append(
            {
                "finding_id": finding_id,
                "tournament_id": conflict.tournament_id,
                "reason": "no_registered_replacement_roster",
            }
        )
    return options, rejected


def _participation_block_reason(
    problem: Mapping[str, Any],
    replacements: Sequence[Mapping[str, Any]],
    counts: Mapping[TeamIdentity, int],
    half_counts: Mapping[str, Mapping[TeamIdentity, int]],
    t_date: Any,
    tournament_id: str,
) -> Optional[str]:
    """The first hard participation ceiling a replacement would cross, if any."""
    for team in replacements:
        reason = _participation_limit_reason(
            _identity(team), counts, half_counts, problem, t_date, tournament_id
        )
        if reason is not None:
            return reason
    return None


def _effects(
    before_roster: Sequence[Mapping[str, Any]],
    after_roster: Sequence[Mapping[str, Any]],
    removed: Sequence[TeamIdentity],
    added: Sequence[Mapping[str, Any]],
    before_verification: Mapping[str, Any],
    after_verification: Mapping[str, Any],
) -> Dict[str, Any]:
    before_codes = Counter(_codes(before_verification))
    after_codes = Counter(_codes(after_verification))
    by_code = {
        code: after_codes.get(code, 0) - before_codes.get(code, 0)
        for code in set(before_codes) | set(after_codes)
        if after_codes.get(code, 0) - before_codes.get(code, 0)
    }
    counts_before = _roster_counts(before_roster)
    counts_after = _roster_counts(after_roster)

    effects: Dict[str, Any] = {
        "hard_violations": sum(after_codes.values()) - sum(before_codes.values()),
        "hard_violation_delta_by_code": by_code,
        "participant_replacements": len(removed),
        "placement_unchanged": True,
        "roster_size_delta": len(after_roster) - len(before_roster),
    }
    for identity in removed:
        label = identity[1] or identity[0]
        delta = counts_after.get(identity, 0) - counts_before.get(identity, 0)
        effects[f"participation.{label}"] = delta
    for team in added:
        identity = _identity(team)
        label = str(team.get("label") or team.get("club") or "")
        delta = counts_after.get(identity, 0) - counts_before.get(identity, 0)
        effects[f"participation.{label}"] = delta
    return effects


def _roster_counts(roster: Sequence[Mapping[str, Any]]) -> Dict[TeamIdentity, int]:
    counts: Dict[TeamIdentity, int] = {}
    for team in roster:
        identity = _identity(team)
        counts[identity] = counts.get(identity, 0) + 1
    return counts


def _replacement_rank(
    problem: Mapping[str, Any],
    team: Mapping[str, Any],
    counts: Mapping[TeamIdentity, int],
) -> Tuple[int, int, str, str]:
    """Prefer teams furthest below their canonical participation target."""
    identity = _identity(team)
    target = resolve_season_target(identity, problem, team=team)
    current = counts.get(identity, 0)
    deficit = max(0, target - current) if isinstance(target, int) else 0
    return (-deficit, current, str(team.get("club") or ""), str(team.get("label") or ""))


def _team_ref_by_identity(identity: TeamIdentity) -> Dict[str, Any]:
    return {"club": identity[0], "label": identity[1], "age_group": identity[2]}
