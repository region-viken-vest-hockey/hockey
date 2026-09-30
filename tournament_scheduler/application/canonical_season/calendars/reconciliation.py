"""Calendar booking reconciliation use case."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.calendar_bookings import (
    TOURNAMENT_BOOKING_EVIDENCE_KEY,
    booking_status_report as _booking_status_report,
)
from tournament_scheduler.canonical_state import (
    canonical_state_revision,
)

from ..shared import (
    _operator_identity,
    _now_iso,
    _append_decision_history,
    _resolve_plan_problem,
)

from .assessment import _classify_club_calendar_bookings

def reconcile_calendar_bookings(
    service,
    *,
    season: str,
    club: str,
    actor: str | None = None,
    note: str = "",
    problem: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Classify every hosted tournament for one club against current calendar evidence.

    See :func:`_classify_club_calendar_bookings` for the classification rules;
    this action additionally persists the classification as tournament booking
    evidence unless ``dry_run`` is set.
    """

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule["plan"]
    resolved_problem = _resolve_plan_problem(schedule, problem, decisions) or {}
    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    rows, records = _classify_club_calendar_bookings(
        plan=plan,
        resolved_problem=resolved_problem,
        decisions=decisions,
        club=club,
        actor=actor,
        note=note,
        now=now,
        source_revision=canonical_state_revision(schedule, decisions),
    )
    result = {"season": season, "club": club, "dry_run": dry_run, "classified": rows, "count": len(rows)}
    if dry_run:
        return result
    updated = dict(decisions)
    prior = [dict(record) for record in updated.get(TOURNAMENT_BOOKING_EVIDENCE_KEY) or [] if not (isinstance(record, Mapping) and str(record.get("tournament_id") or "") in {str(r.get("tournament_id") or "") for r in records})]
    updated[TOURNAMENT_BOOKING_EVIDENCE_KEY] = prior + records
    updated["updated_at"] = now
    _append_decision_history(
        updated,
        event="reconcile_calendar_bookings",
        tournament_id="",
        actor=resolved_actor,
        now=now,
        note=note,
        details={"club": club, "classified": rows},
    )
    committed = service._commit(snapshot.with_decisions(updated))
    result["canonical_state_revision"] = canonical_state_revision(committed.schedule, committed.decisions)
    result["booking_status"] = _booking_status_report(problem=resolved_problem, plan=plan, decisions=committed.decisions)
    return result
