"""CP-SAT hard-invariant diagnostics, split out of `stage3_cpsat.py`.

issue #323 P0: after the baseline/Stage 3 host-derivation fix, a slot's fixed
`host_club` should always have at least one roster-eligible representing team
for this age group. `raise_host_not_represented` fails loudly (via the same
`CpSatNoCandidate` fallback used for a genuine solver infeasibility) instead
of silently building a model that can't enforce host representation at all.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any, Dict, NoReturn, Optional


def raise_host_not_represented(
    cp_sat_no_candidate: type,
    *,
    half_label: str,
    feasibility_only: bool,
    team_count: int,
    slot_count: int,
    solve_budget_seconds: float,
    seed: int,
    started: float,
    tournament_id: Any,
    host_club: str,
    age_group: str,
    baseline_fingerprint: Optional[str],
    problem_fingerprint: Optional[str],
) -> NoReturn:
    diagnostics: Dict[str, Any] = {
        "half": half_label,
        "mode": "feasibility_only" if feasibility_only else "quality",
        "team_count": team_count,
        "slot_count": slot_count,
        "solve_budget_seconds": float(solve_budget_seconds),
        "seed": int(seed),
        "status": "HOST_NOT_REPRESENTED_IN_ROSTER",
        "runtime_seconds": round(perf_counter() - started, 6),
        "violated_constraint": "host_representation",
        "tournament_id": tournament_id,
        "host_club": host_club,
        "age_group": age_group,
    }
    raise cp_sat_no_candidate(
        "HOST_NOT_REPRESENTED_IN_ROSTER",
        perf_counter() - started,
        baseline_candidate_fingerprint=baseline_fingerprint,
        problem_fingerprint=problem_fingerprint,
        diagnostics=diagnostics,
    )
