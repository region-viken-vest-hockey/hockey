"""CP-SAT slot/team setup helpers, split out of `stage3_cpsat.py`.

These functions build the immutable inputs `_solve_slot_group` optimizes
over (the fixed-skeleton tournament slots, the participant universe, and
per-half/per-team target resolution) -- a distinct, standalone concern from
the model-building/solving itself, and each is a pure function of its
explicit parameters (no shared solver-local closures).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from itertools import combinations
from typing import Any, Dict, Iterable, Optional, Tuple

from . import planning_half
from .game_generation import generate_round_robin_games
from .models import Team
from .planning_contract import _parse_date, _team_identity

TeamIdentity = Tuple[str, str, str]


@dataclass(frozen=True)
class _TournamentSlot:
    index: int
    tournament_id: str
    age_group: str
    on_date: date
    host_club: str
    roster_size: int
    baseline_team_ids: tuple[TeamIdentity, ...]
    pinned: bool


def _registered_team_map(
    candidate: Dict[str, Any], problem: Optional[Dict[str, Any]]
) -> Dict[TeamIdentity, Dict[str, Any]]:
    """Return the participant universe, preserving original team payloads."""
    result: Dict[TeamIdentity, Dict[str, Any]] = {}
    if problem:
        for raw in problem.get("teams", []) or []:
            if isinstance(raw, dict):
                result[_team_identity(raw)] = dict(raw)
    for tournament in candidate.get("tournaments", []) or []:
        for raw in tournament.get("teams", []) or []:
            if isinstance(raw, dict):
                result.setdefault(_team_identity(raw), dict(raw))
    return result


def _active_slots(candidate: Dict[str, Any], problem: Optional[Dict[str, Any]]) -> list[_TournamentSlot]:
    pinned_ids = {
        str(value)
        for value in (((problem or {}).get("manual_adjustments") or {}).get("pinned_tournament_ids", []) or [])
    }
    slots: list[_TournamentSlot] = []
    for index, tournament in enumerate(candidate.get("tournaments", []) or []):
        if tournament.get("cancelled"):
            continue
        on_date = _parse_date(tournament.get("date"))
        if on_date is None:
            continue
        baseline = tuple(_team_identity(team) for team in tournament.get("teams", []) or [])
        tournament_id = str(tournament.get("id", index))
        slots.append(
            _TournamentSlot(
                index=index,
                tournament_id=tournament_id,
                age_group=str(tournament.get("age_group", "")),
                on_date=on_date,
                host_club=str(tournament.get("host_club") or ""),
                roster_size=len(baseline),
                baseline_team_ids=baseline,
                pinned=tournament_id in pinned_ids,
            )
        )
    return slots


def _baseline_same_club_pairings(slots: Iterable[_TournamentSlot]) -> int:
    """Count same-club round-robin games implied by baseline rosters."""
    total = 0
    for slot in slots:
        for left, right in combinations(slot.baseline_team_ids, 2):
            if left[0] == right[0]:
                total += 1
    return total


def _parallel_games(tournament: Dict[str, Any], problem: Optional[Dict[str, Any]]) -> int:
    if problem:
        configured = (problem.get("parallel_games") or {}).get(tournament.get("age_group"))
        if isinstance(configured, int) and configured > 0:
            return configured
    games = tournament.get("games", []) or []
    if games:
        return max(int(game.get("parallel_slot", 0)) for game in games) + 1
    return 1


def _team_dict(identity: TeamIdentity, source: Dict[TeamIdentity, Dict[str, Any]]) -> Dict[str, Any]:
    raw = source.get(identity)
    if raw is not None:
        return dict(raw)
    club, label, age_group = identity
    return {"club": club, "label": label, "age_group": age_group}


def _games_to_dicts(teams: list[Team], parallel_games: int) -> list[Dict[str, Any]]:
    return [
        {
            "home": game.home.label,
            "away": game.away.label,
            "parallel_slot": game.parallel_slot,
            "round_number": game.round_number,
        }
        for game in generate_round_robin_games(teams, parallel_games)
    ]


def _resolve_participation_target(
    identity: TeamIdentity,
    team_map: Dict[TeamIdentity, Dict[str, Any]],
    problem: Optional[Dict[str, Any]],
    half_label: str,
) -> Optional[int]:
    """Resolve the authoritative participation target for *identity* in this half.

    An explicit per-team ``target_tournament_count`` override
    (season-wide by definition) always wins, mirroring
    ``SeasonPlanner._team_target_tournament_count``'s precedence. Otherwise
    the age group's ``participation_targets_by_age_group`` before/after
    value for *half_label* is the authoritative per-team, per-half target.
    Returns ``None`` when neither is configured (non-canonical age group),
    signalling the caller should fall back to the legacy baseline-lock
    behavior for that identity.
    """
    team = team_map.get(identity) or {}
    explicit = team.get("target_tournament_count")
    if isinstance(explicit, int):
        return explicit
    if half_label not in ("before_christmas", "after_christmas"):
        return None
    age_group = identity[2]
    targets = ((problem or {}).get("participation_targets_by_age_group") or {}).get(age_group) or {}
    half_target = targets.get(half_label)
    return half_target if isinstance(half_target, int) else None


def _resolve_split_date(
    problem: Optional[Dict[str, Any]], slots: "list[_TournamentSlot]"
) -> Optional[date]:
    """Resolve the shared Christmas-half boundary for *slots* (issue #298).

    Prefers ``problem["christmas_split_date"]`` -- the single boundary every
    engine already agrees on (:mod:`planning_half`) -- and only falls back to
    deriving it from the slot dates themselves when no problem contract is
    available (e.g. a bare candidate/tests).
    """
    raw = (problem or {}).get("christmas_split_date")
    if raw:
        parsed = _parse_date(raw)
        if parsed is not None:
            return parsed
    dates = [slot.on_date for slot in slots]
    if not dates:
        return None
    return planning_half.christmas_split_date(min(dates), max(dates))


def _group_slots_by_half(
    slots: "list[_TournamentSlot]", split_date: Optional[date]
) -> "list[tuple[str, list[_TournamentSlot]]]":
    """Partition *slots* into independent before/after-Christmas solver groups.

    Uses the shared :mod:`planning_half` contract from issue #293 as the
    solver boundary (issue #298 Phase 2): each half is solved as its own
    smaller participant-assignment model instead of one monolithic Oct-Apr
    model, which shrinks the assignment/pair-variable count and keeps a
    half-2 registration change from perturbing half-1's solve. Empty halves
    are dropped rather than solved as a no-op.
    """
    groups: "dict[str, list[_TournamentSlot]]" = defaultdict(list)
    for slot in slots:
        groups[planning_half.tournament_half(slot.on_date, split_date)].append(slot)
    ordered_labels = ["before_christmas", "after_christmas", "unsplit"]
    return [(label, groups[label]) for label in ordered_labels if groups.get(label)]
