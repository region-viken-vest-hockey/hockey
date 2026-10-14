"""Coverage provenance for the daily BRP/Exigo calendar walk."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..models_calendar import CalendarEvent
from .fingerprints import stable_payload_sha256
from .source_integrity import (
    INTEGRITY_COMPLETE,
    INTEGRITY_FAILED,
    INTEGRITY_PARTIAL,
)

_BRP_EXIGO_STRATEGY = "brp_exigo"


def coverage_for_walk(
    *,
    requested_start: date,
    requested_end: date,
    inspected_dates: list[date],
    events: list[CalendarEvent],
    exceptions: list[str],
) -> dict[str, Any]:
    """Build coverage provenance from requested days, never event sparsity."""
    requested_date_count = max(0, (requested_end - requested_start).days + 1)
    errors = list(exceptions)
    if requested_date_count == 0:
        errors.append(
            "BRP/Exigo-skraping fikk en ugyldig periode der sluttdato er før startdato."
        )
    if not errors and len(inspected_dates) == requested_date_count:
        status = INTEGRITY_COMPLETE
    elif inspected_dates:
        status = INTEGRITY_PARTIAL
    else:
        status = INTEGRITY_FAILED

    event_dates = [event.datetime.date() for event in events]
    return {
        "strategy": _BRP_EXIGO_STRATEGY,
        "status": status,
        "navigation_complete": status == INTEGRITY_COMPLETE,
        "requested_start": requested_start.isoformat(),
        "requested_end": requested_end.isoformat(),
        "observed_start": min(inspected_dates).isoformat() if inspected_dates else None,
        "observed_end": max(inspected_dates).isoformat() if inspected_dates else None,
        "event_observed_start": min(event_dates).isoformat() if event_dates else None,
        "event_observed_end": max(event_dates).isoformat() if event_dates else None,
        "requested_date_count": requested_date_count,
        "inspected_date_count": len(inspected_dates),
        "event_count": len(events),
        "event_fingerprint": _event_fingerprint(events),
        "exceptions": errors,
    }


def _event_fingerprint(events: list[CalendarEvent]) -> str:
    return stable_payload_sha256([
        {
            "date": event.date,
            "name": event.name,
            "datetime": event.datetime.isoformat(),
            "duration_minutes": event.duration_minutes,
            "location": event.location,
            "resources": [
                {"id": resource.id, "label": resource.label}
                for resource in event.resources
            ],
            "order_id": event.order_id,
        }
        for event in events
    ])


def failure_message(day: date, exc: Exception) -> str:
    """Return a date-bound, non-secret description of one failed request."""
    detail = f"{type(exc).__name__}: {exc}"
    return f"BRP/Exigo-skraping feilet for {day.isoformat()}: {detail}"
