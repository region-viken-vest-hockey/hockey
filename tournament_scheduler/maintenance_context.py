"""Canonical maintenance context and overlay projection for season maintenance.

This module owns the construction of the maintenance-specific planning problem
by projecting canonical decisions (holiday exceptions, banned dates, etc.)
onto the promoted planning problem.
"""

from __future__ import annotations

from .canonical_baseline import build_canonical_baseline
from .participation_targets import search_evidence_from_acceptances
from .request_constraints import host_sibling_preference_evidence
from .season_state import DEFAULT_SEASON_ROOT
from .season_state import load_decisions
from .season_state import load_participation_acceptances
from .season_state import load_schedule
from typing import Any
from typing import Dict
from typing import Mapping
from typing import Tuple


def load_maintenance_context(
    season: str,
    *,
    root: str = DEFAULT_SEASON_ROOT,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Load the full maintenance context for a promoted season.

    Returns ``(schedule, decisions, plan, problem)`` where:
    - schedule: The promoted season schedule
    - decisions: Canonical decisions (locks + approval state)
    - plan: The current plan from the schedule
    - problem: The planning problem with canonical overlays projected

    The canonical baseline is built and injected into the problem under
    ``problem["canonical_baseline"]`` for use by verification and repair.

    Args:
        season: The season identifier
        root: The root directory for season data (default: DEFAULT_SEASON_ROOT)

    Returns:
        Tuple of (schedule, decisions, plan, problem) dictionaries
    """
    schedule = load_schedule(season, root=root)
    decisions = load_decisions(season, root=root)
    problem = _problem_from_schedule(schedule)
    problem["canonical_baseline"] = build_canonical_baseline(schedule, decisions)
    plan = dict(schedule.get("plan") or {})
    problem = project_canonical_overlays(problem, decisions=decisions, plan=plan)
    # A persisted operator acceptance is injected as verifier search evidence so
    # the same independent verifier that classifies every other deviation also
    # honours an explicit operator_accepted decision -- and stops honouring it
    # once the target changes or the deviation gets worse.
    problem["participation_search_evidence"] = search_evidence_from_acceptances(
        load_participation_acceptances(season, root=root)
    )
    # Host sibling participation preference evidence for published-season
    # maintenance: a soft operational preference that repair providers should
    # consider when ranking participant options.
    problem["host_sibling_preference_evidence"] = host_sibling_preference_evidence(plan, decisions)
    return schedule, decisions, plan, problem


class SeasonMaintenanceError(RuntimeError):
    """Raised when a maintenance request cannot be served safely."""


def _problem_from_schedule(schedule: Mapping[str, Any]) -> Dict[str, Any]:
    context = schedule.get("verification_context")
    problem = context.get("problem") if isinstance(context, Mapping) else None
    if not isinstance(problem, Mapping):
        raise SeasonMaintenanceError(
            "Canonical season carries no promoted verification-context problem; "
            "maintenance cannot reconstruct the planning contract without a re-promotion"
        )
    return dict(problem)


def project_canonical_overlays(
    problem: Mapping[str, Any],
    *,
    decisions: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> Dict[str, Any]:
    """Project live canonical decisions onto a promoted planning problem.

    This is the single facade for the canonical overlays that every verification
    consumer of the promoted problem must share: holiday-date exceptions,
    operator-banned dates, calendar-booking associations and durable
    participation withdrawals. Keeping it in one place is what lets
    ``season findings``/``season audit``/repair and ``season export`` agree with
    the apply-time verification boundary instead of each re-deriving a subset.

    The overlay projections may return the same mapping or a copy; every call
    is written back explicitly so ordering and equality are preserved.
    """
    from .calendar_bookings import (
        project_associations_into_problem,
        project_manual_assertions_into_problem,
    )
    from .canonical_banned_dates import project_banned_dates_into_problem
    from .canonical_holiday_exceptions import project_exceptions_into_problem
    from .canonical_ice_time_overrides import project_overrides_into_problem
    from .participation_withdrawals import project_into_problem

    projected: Dict[str, Any] = dict(problem)
    projected = project_exceptions_into_problem(projected, decisions)
    projected = project_banned_dates_into_problem(projected, decisions)
    # A host-confirmed per-tournament ice-time override must reach every
    # duration consumer before booking evidence is revalidated, otherwise the
    # evidence can look stale against the age-group default it superseded.
    projected = project_overrides_into_problem(projected, decisions)
    projected = project_associations_into_problem(projected, decisions, plan) or projected
    projected = project_manual_assertions_into_problem(projected, decisions, plan) or projected
    # A durable participation withdrawal reduces the eligible shape pool. Every
    # shape/round-count verifier must see the same reduced pool the apply-time
    # candidate was verified against, or a committed withdrawal looks
    # hard-invalid after the fact.
    projected = project_into_problem(projected, decisions=decisions, plan=plan)
    return projected
