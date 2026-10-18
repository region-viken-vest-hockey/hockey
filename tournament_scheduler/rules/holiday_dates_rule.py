"""Canonical holiday-date hard rule for the planner-independent rules model.

Uses the same effective policy as the planner and verifier
(:func:`tournament_scheduler.planning.date_policy.effective_holiday_policy`):
derived holiday exclusion < accepted holiday-date exception < operator ban.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from tournament_scheduler.models import SeasonPlan


def _active_tournaments(plan: SeasonPlan) -> list[Any]:
    return [t for t in plan.tournaments if not t.cancelled]


def _iso_dates(raw: Any) -> set[date]:
    parsed: set[date] = set()
    for value in raw or []:
        try:
            parsed.add(date.fromisoformat(str(value)))
        except ValueError:
            continue
    return parsed


def holiday_date_rule(plan: SeasonPlan) -> dict[str, Any]:
    """Hard rule: no tournament on a canonical holiday-excluded date.

    The active excluded dates are derived from the canonical
    :mod:`tournament_scheduler.planning.date_policy` and surfaced directly here, so the
    operator/audit can see the policy actually being enforced rather than only
    its violations.
    """
    from tournament_scheduler.planning.date_policy import effective_holiday_policy

    active = _active_tournaments(plan)
    manual = plan.manual_adjustments or {}
    excluded: dict[Any, str] = {}
    if plan.start_date and plan.end_date:
        # Same precedence as the planner and verifier: derived holiday exclusion
        # < accepted holiday-date exception < operator ban.
        excluded = effective_holiday_policy(
            plan.start_date,
            plan.end_date,
            exceptions=_iso_dates(manual.get("holiday_date_exceptions")),
            banned=_iso_dates(manual.get("banned_dates")),
        )
    used = sorted(
        f"{tournament.id} ({tournament.date.isoformat()})"
        for tournament in active
        if tournament.date in excluded
    )
    excluded_weekends = sorted(
        excluded_date.isoformat()
        for excluded_date in excluded
        if excluded_date.weekday() in (5, 6)
    )
    return {
        "id": "holiday_dates_not_used",
        "title": "Helligdagsdatoer må ikke brukes",
        "type": "hard",
        "scope": "dato",
        "owner": "deterministic_verifier",
        "description": (
            "Turneringer skal ikke legges i ferie-/helligdagshelger eller helgen rett "
            "foran en helligdag."
        ),
        "configured_value": (
            f"{len(excluded_weekends)} sperret(e) helgedato(er)"
            if excluded_weekends
            else "Ingen i vinduet"
        ),
        "excluded_dates": excluded_weekends,
        "status": "Oppfylt" if not used else f"{len(used)} avvik: {', '.join(used)}",
        "ok": not used,
    }
