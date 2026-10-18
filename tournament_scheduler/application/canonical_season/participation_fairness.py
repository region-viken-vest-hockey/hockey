"""Read-only club x age-group participation fairness use case."""

from __future__ import annotations

from typing import Any

from tournament_scheduler.participation.club_age_group_fairness import (
    build_club_age_group_participation_report,
)
from tournament_scheduler.season.state import canonical_state_revision

from .shared import _resolve_plan_problem


def participation_fairness_report(
    service,
    *,
    season: str,
    age_group: str | None = None,
    problem: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return booked-reality participation per club and age group.

    Booking state comes from the same ``booking-status`` projection the operator
    uses, so the fairness population can never drift from the booking evidence.
    """

    snapshot = service.load(season)
    plan = snapshot.schedule.get("plan") or {}
    resolved_problem = _resolve_plan_problem(snapshot.schedule, problem, snapshot.decisions) or {}
    status = service.booking_status_report(season=season, problem=problem)
    booking_states = {
        str(row.get("tournament_id") or ""): str(row.get("operational_state") or "")
        for row in status.get("tournaments") or []
    }
    report = build_club_age_group_participation_report(
        teams=resolved_problem.get("teams") or [],
        tournaments=plan.get("tournaments") or [],
        booking_states=booking_states,
        targets_by_age_group=resolved_problem.get("participation_targets_by_age_group") or {},
        split_date=resolved_problem.get("christmas_split_date"),
        age_group=age_group,
    )
    report["season"] = season
    report["canonical_state_revision"] = canonical_state_revision(snapshot.schedule, snapshot.decisions)
    return report
