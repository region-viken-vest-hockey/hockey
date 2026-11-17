"""Project valid manual authority and report accepted-interval feasibility."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.booking.booking_facts import _tournaments_by_id
from tournament_scheduler.booking.booking_vocabulary import _STATUS_BOOKED, MANUAL_BOOKING_ASSERTIONS_KEY
from tournament_scheduler.booking.manual_assertions import active_manual_assertions, manual_assertion_stale_reasons


def governing_floor_finding(
    tournament: Mapping[str, Any],
    accepted_minutes: Any,
) -> dict[str, Any] | None:
    """Return the governing-floor discrepancy finding for an accepted interval.

    A source-confirmed booking may record an exact interval shorter than the
    governing *planning* floor. That interval is preserved exactly and the
    shortfall is surfaced as a durable feasibility finding -- never a
    new-placement planning violation. Inadequate actual playing time and true
    arena overlaps are checked separately and remain hard.
    """

    from tournament_scheduler.shape.occupancy import governing_minimum_ice_time_minutes

    try:
        minutes = int(accepted_minutes)
    except (TypeError, ValueError):
        return None
    if minutes <= 0:
        return None
    age_group = str(tournament.get("age_group") or "")
    governing_floor = governing_minimum_ice_time_minutes(age_group)
    if governing_floor is None or minutes >= governing_floor:
        return None
    tournament_id = str(tournament.get("id") or "")
    return {
        "code": "ice_time_governing_minimum",
        "message": (
            f"Tournament {tournament_id} ({age_group}) has an accepted booking interval of "
            f"{minutes} minutes, below the governing minimum booking window of "
            f"{governing_floor} minutes"
        ),
        "tournament_id": tournament_id,
        "age_group": age_group,
        "configured_ice_time_minutes": minutes,
        "minimum_required_minutes": governing_floor,
        "accepted_booking_interval": True,
    }


def valid_active_manual_assertions(
    decisions: Mapping[str, Any] | None,
    *,
    problem: Mapping[str, Any] | None,
    plan: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return active manual ``booked`` assertions still matching the plan slot."""

    tournaments = _tournaments_by_id(plan)
    valid: list[dict[str, Any]] = []
    seen_tournaments: set[str] = set()
    for record in active_manual_assertions(decisions):
        tournament_id = str(record.get("tournament_id") or "")
        if not tournament_id or tournament_id in seen_tournaments:
            continue
        if str(record.get("booking_status") or "") != _STATUS_BOOKED:
            continue
        if manual_assertion_stale_reasons(
            record,
            problem=problem,
            tournament=tournaments.get(tournament_id),
        ):
            continue
        valid.append(dict(record))
        seen_tournaments.add(tournament_id)
    return valid


def project_manual_assertions_into_problem(
    problem: Mapping[str, Any] | None,
    decisions: Mapping[str, Any] | None,
    plan: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Attach currently-valid manual booking assertions to a verification input.

    The planning verifier stays strict and never reads this key; the projected
    evidence is consumed by the reconciliation/audit boundary to classify a
    source-confirmed accepted booking against the governing planning floor.
    """

    if problem is None:
        return None
    projected = dict(problem)
    projected[MANUAL_BOOKING_ASSERTIONS_KEY] = (
        valid_active_manual_assertions(decisions, problem=projected, plan=plan)
        if plan is not None
        else []
    )
    return projected
