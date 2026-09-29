"""Reconstructs the planning_problem contract for Stage 4's hard-verification gate."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from .state import PipelineState, StageName

# Calendar-evidence keys that all derive from the same scrape/refresh and must
# move together, never partially -- mixing a fresh ``club_busy_intervals``
# with a stale ``club_calendar_status`` can make a club look untrusted for
# evidence that has actually been proven current, or vice versa.
_CALENDAR_EVIDENCE_KEYS = (
    "club_busy_intervals",
    "club_calendar_status",
    "club_source_integrity",
    "club_source_integrity_details",
    "club_coverage_proven",
)


def _prefer_canonical_calendar_evidence(
    problem: dict[str, Any], canonical_schedule: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Overlay canonical, ``refresh-calendars``-updated calendar evidence.

    A promoted canonical season's own calendar evidence lives in
    ``schedule.json``'s ``verification_context.problem`` and is what every
    ordinary ``season`` command (``findings``, ``calendar-booking-findings``,
    ``booking-status``) verifies against via
    ``season_maintenance.load_context`` -> ``_problem_from_schedule``. The
    export gate historically rebuilt its calendar evidence from
    ``.pipeline/stage2_scraping.json`` instead -- a pipeline-local Stage 2
    checkpoint that is not refreshed by ``season refresh-calendars`` and can
    be arbitrarily older than the canonical evidence a real booking was just
    confirmed against, making an already-accepted booking look stale/
    unconfirmed at the export chokepoint while every other season command
    agrees it is fine. When canonical calendar evidence exists, it is always
    the more authoritative source for a promoted season; prefer it over
    whatever the pipeline checkpoint happens to hold, without touching any of
    the other effective_config-derived contract (window bounds, waivers,
    participation targets, etc.) built from the current run's config.
    """

    canonical_problem = (
        (canonical_schedule or {}).get("verification_context") or {}
    ).get("problem")
    if not isinstance(canonical_problem, Mapping):
        return problem
    if not canonical_problem.get("club_busy_intervals"):
        return problem
    from tournament_scheduler.calendar_availability import unclassified_intervals

    updated = dict(problem)
    for key in _CALENDAR_EVIDENCE_KEYS:
        if key in canonical_problem:
            updated[key] = canonical_problem[key]
    updated["unclassified_calendar_events"] = unclassified_intervals(
        updated.get("club_busy_intervals") or {}
    )
    return updated


def _build_export_verification_problem(
    effective_config: dict[str, Any], state: PipelineState
) -> dict[str, Any] | None:
    """Best-effort ``planning_problem`` for the Stage 4 hard-verification gate.

    Reconstructs the same problem contract Stage 3 was given so
    ``verify_candidate`` can check the problem-dependent hard invariants
    (arena interval conflicts, banned/locked dates, excluded host clubs,
    capacity, window bounds) at the actual export chokepoint, not only in
    the evidence bundle after export has already happened. Returns
    ``None`` (degrading to self-consistency-only verification) when the
    inputs can't be reconstructed, matching every other best-effort
    ``problem`` builder in this pipeline (e.g.
    ``cli.pipeline_orchestrator.verification._mid_planning_decision_problem``).
    """
    start_raw = effective_config.get("start_date")
    end_raw = effective_config.get("end_date")
    if not effective_config or not start_raw or not end_raw:
        return None
    try:
        from ..canonical_baseline import resolve_canonical_state
        from ..operator_waivers import load_active_waivers
        from ..planning_contract import build_planning_problem
        from ..season_maintenance import project_canonical_overlays

        start = datetime.strptime(str(start_raw), "%Y-%m-%d")
        end = datetime.strptime(str(end_raw), "%Y-%m-%d")
        scraping_result = state.read_stage(StageName.SCRAPING)
        # Baseline-aware verification (issue #355): when the season has been
        # promoted to canonical state, fold its approval/placement locks into
        # the problem so this export gate hard-rejects a candidate that moved
        # or dropped approved/booked work -- not only self-consistency.
        canonical_state = resolve_canonical_state(effective_config, start.date(), end.date())
        canonical_baseline = canonical_state["baseline"] if canonical_state else None
        problem = build_planning_problem(
            effective_config,
            scraping_result,
            start.date(),
            end.date(),
            waivers=load_active_waivers(state.work_dir),
            canonical_baseline=canonical_baseline,
        )
        if canonical_state:
            # Delegate to the single revision-bound effective canonical
            # projection so this export chokepoint honours exactly the same
            # overlays (banned dates, holiday-date exceptions, ice-time
            # overrides, calendar-booking associations, participation
            # withdrawals) that season repair/apply-repair already verified
            # the candidate against, instead of re-deriving a partial or
            # stricter problem from the frozen pipeline config.
            decisions = canonical_state["decisions"]
            canonical_schedule = canonical_state.get("schedule") or {}
            plan = canonical_schedule.get("plan") or {}
            problem = _prefer_canonical_calendar_evidence(problem, canonical_schedule)
            problem = project_canonical_overlays(problem, decisions=decisions, plan=plan)
        return problem
    except Exception:
        return None
