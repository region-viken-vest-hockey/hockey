"""CP-SAT participation caps and goals for one solve group (issue #451).

Split out of :mod:`tournament_scheduler.stage3.cpsat` for its file-length
ratchet. Each eligible identity is capped by its hard participation maximum,
then pulled toward its configured target: a deficit term for shortfalls and,
in decomposed per-half solves, an overshoot term for excess.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from tournament_scheduler.participation.targets import resolve_hard_max
from tournament_scheduler.stage3.cpsat_slots import TeamIdentity, _resolve_participation_target

# Penalty per participation tournament a team is planned *over* its configured
# target (per-half or season). Below the deficit weight (5000) so closing a
# shortfall still wins, but nonzero so the goal is not unbounded. Only an
# explicit ``participation_hard_max`` is a hard cap.
PARTICIPATION_OVERSHOOT_PENALTY = 1000


def add_participation_terms(
    model: Any,
    *,
    eligible_identities: "set[TeamIdentity]",
    slot_indexes_by_age_group: "dict[str, list[int]]",
    x: Dict[Tuple[int, TeamIdentity], Any],
    baseline_participations: Dict[TeamIdentity, int],
    team_map: Dict[TeamIdentity, Dict[str, Any]],
    team_index: "dict[TeamIdentity, int]",
    problem: Optional[Dict[str, Any]],
    half_label: str,
    feasibility_only: bool,
) -> "tuple[list[Any], list[Any]]":
    """Add participation caps/goals for every eligible identity to *model*.

    Returns ``(deficit_terms, overshoot_terms)`` for the caller's objective.
    Extracted from ``cpsat._solve_slot_group`` to keep that module within its
    file-length ratchet (#451).
    """
    # The configured participation target (per-team override, or
    # the age group's authoritative before/after-Christmas value for this
    # half) is a *strong goal* (see
    # ``tournament_scheduler.participation.targets``): CP-SAT both caps the
    # assignment at the target and minimizes the remaining deficit, so it
    # never trades target compliance away for a lower-priority pairing
    # quality gain. An explicit ``participation_hard_max`` is a separate,
    # genuinely hard season-wide ceiling enforced by the independent
    # verifier after the solve. The baseline remains only a search hint
    # (`AddHint` below). Identities with no configured target (non-canonical
    # age groups) keep the original exact baseline-lock behavior as a
    # defensive fallback.
    #
    participation_deficit_terms: "list[Any]" = []
    participation_overshoot_terms: "list[Any]" = []
    for identity in eligible_identities:
        baseline_count = baseline_participations.get(identity, 0)
        vars_for_identity = [
            x[(slot_index, identity)]
            for slot_index in slot_indexes_by_age_group.get(identity[2], [])
            if (slot_index, identity) in x
        ]
        if not vars_for_identity:
            continue
        count_expr = sum(vars_for_identity)
        hard_max = resolve_hard_max(identity, problem, team=team_map.get(identity))
        if hard_max is not None:
            model.Add(count_expr <= hard_max)
        target = _resolve_participation_target(identity, team_map, problem, half_label)
        if target is None:
            model.Add(count_expr == baseline_count)
            continue
        # Targets are goals, not ceilings (#451): the canonical policy lets a
        # team exceed a half or season target when required roster slots force
        # it, so that overage is penalized rather than made infeasible.
        overshoot = model.NewIntVar(0, len(vars_for_identity), f"participation_overshoot_team{team_index[identity]}")
        model.Add(overshoot >= count_expr - target)
        participation_overshoot_terms.append(overshoot)
        if not feasibility_only:
            deficit = model.NewIntVar(0, target, f"participation_deficit_team{team_index[identity]}")
            model.Add(deficit >= target - count_expr)
            participation_deficit_terms.append(deficit)

    return participation_deficit_terms, participation_overshoot_terms
