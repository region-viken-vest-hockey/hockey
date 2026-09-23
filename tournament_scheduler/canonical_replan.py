"""Baseline-aware replanning of a promoted canonical season (issue #355).

Once a season is promoted, a normal planning run must not silently
regenerate a wholly new season.  This module owns the bounded replan
operation: it takes the canonical current schedule as the search baseline,
builds a lock-enforcing ``planning_problem`` from it, runs the same Stage 3
engine boundary every other planner uses, and returns a candidate together
with its change cost, canonical-lock violations and hard verification.

Nothing here writes canonical state.  The candidate is a transient Stage 3
artifact; persisting it is a separate, explicitly verified
:func:`tournament_scheduler.season_state.apply_candidate` (or the normal
promotion path).
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any, Dict, Iterable, Optional

from tournament_scheduler.canonical_baseline import (
    build_canonical_baseline,
    change_cost,
    verify_canonical_locks,
)
from tournament_scheduler.planning_contract import build_planning_problem, verify_candidate
from tournament_scheduler.participation_targets import search_evidence_from_acceptances
from tournament_scheduler.season_state import (
    DEFAULT_SEASON_ROOT,
    load_decisions,
    load_participation_acceptances,
    load_schedule,
)
from tournament_scheduler.stage3_engine import DEFAULT_ENGINE, run_planner


def replan_around_baseline(
    *,
    season: str,
    config: Dict[str, Any],
    scraping_result: Optional[Dict[str, Any]],
    start_date: date,
    end_date: date,
    root: str | os.PathLike[str] = DEFAULT_SEASON_ROOT,
    waivers: Optional[Iterable[Dict[str, Any]]] = None,
    engine: str = DEFAULT_ENGINE,
    request: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Search around the canonical baseline and return a verified candidate.

    The returned dict contains the ``candidate``, the enriched ``problem``
    (including the ``canonical_baseline`` section), the weighted
    ``change_cost`` relative to canonical state, the canonical
    ``lock_violations``, and the hard ``verification`` result.  Callers must
    reject any result with lock violations or a failing verification before
    persisting it.
    """
    schedule = load_schedule(season, root=root)
    decisions = load_decisions(season, root=root)
    from tournament_scheduler.published_baseline import (
        assert_season_allows_global_regeneration,
    )

    assert_season_allows_global_regeneration(decisions, operation="season replan")
    baseline = build_canonical_baseline(schedule, decisions)
    baseline_candidate = dict(schedule.get("plan") or {})

    problem = build_planning_problem(
        config,
        scraping_result,
        start_date,
        end_date,
        waivers=waivers,
        canonical_baseline=baseline,
    )
    # Active canonical operator banned dates are canonical decisions, not
    # transient config: project them into the existing banned_dates read path so
    # the optimizer and its verifier respect them.
    from tournament_scheduler.canonical_banned_dates import (
        project_banned_dates_into_problem,
    )
    from tournament_scheduler.canonical_holiday_exceptions import (
        project_exceptions_into_problem,
    )

    problem = project_exceptions_into_problem(problem, decisions)
    problem = project_banned_dates_into_problem(problem, decisions)
    # A persisted operator acceptance is a durable canonical decision, not a
    # property of the transient config: the replan verifier must honour it too,
    # so escalation cannot silently "fix" an accepted deviation.
    problem["participation_search_evidence"] = search_evidence_from_acceptances(
        load_participation_acceptances(season, root=root)
    )
    candidate = run_planner(
        engine=engine,
        problem=problem,
        baseline=baseline_candidate,
        request=dict(request or {}),
    )
    return {
        "candidate": candidate,
        "baseline": baseline,
        "baseline_candidate": baseline_candidate,
        "problem": problem,
        "change_cost": change_cost(baseline, candidate),
        "lock_violations": verify_canonical_locks(baseline, candidate),
        "verification": verify_candidate(candidate, problem),
    }
