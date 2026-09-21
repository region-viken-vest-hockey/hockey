"""Canonical holiday-policy date exception lifecycle.

A holiday-date exception is a season-level decision that allows one date which
would otherwise be excluded only by the derived holiday/date policy. It does
not edit the Norwegian public-holiday calendar and does not override explicit
operator banned dates.
"""

from __future__ import annotations

from datetime import date as _date
from typing import Any, Mapping

from tournament_scheduler.canonical_state import HOLIDAY_DATE_EXCEPTIONS_KEY
from tournament_scheduler.date_policy import holiday_exclusions, problem_window
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

ACTIVE = "active"
RELEASED = "released"

HOLIDAY_EXCEPTION_PREFIX = "holiday-date-exception"
HOLIDAY_EXCEPTION_TYPE = "holiday_date_exception"


class HolidayDateExceptionError(ValueError):
    """Raised when a holiday-date exception payload is malformed."""


def _parse_iso_date(value: Any, field: str) -> _date:
    try:
        return _date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise HolidayDateExceptionError(
            f"Invalid {field}: {value!r}; expected YYYY-MM-DD"
        ) from exc


def holiday_date_exception_id(on_date: str) -> str:
    """Deterministic identity for the active exception for *on_date*."""

    digest = stable_payload_sha256({"date": str(on_date)})
    return f"{HOLIDAY_EXCEPTION_PREFIX}:{digest[:16]}"


def validate_and_normalize(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one exception payload and return normalized record fields."""

    if not isinstance(raw, Mapping):
        raise HolidayDateExceptionError("A holiday-date exception must be an object")
    on_date = _parse_iso_date(raw.get("date"), "date").isoformat()
    reason = str(raw.get("reason") or raw.get("note") or "").strip()
    if not reason:
        raise HolidayDateExceptionError("A holiday-date exception requires a reason")
    return {
        "id": holiday_date_exception_id(on_date),
        "type": HOLIDAY_EXCEPTION_TYPE,
        "date": on_date,
        "reason": reason,
    }


def exception_records(
    decisions: Mapping[str, Any],
    *,
    include_released: bool = False,
) -> list[dict[str, Any]]:
    """Return durable holiday-date exception records."""

    records: list[dict[str, Any]] = []
    for record in decisions.get(HOLIDAY_DATE_EXCEPTIONS_KEY, []) or []:
        if not isinstance(record, Mapping):
            continue
        status = str(record.get("status") or ACTIVE)
        if status != ACTIVE and not include_released:
            continue
        records.append(dict(record))
    return records


def active_exception_records(decisions: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        record
        for record in exception_records(decisions, include_released=True)
        if str(record.get("status") or ACTIVE) == ACTIVE
    ]


def active_exception_dates(decisions: Mapping[str, Any]) -> list[str]:
    """Return sorted ISO dates currently excepted from derived holiday policy."""

    return sorted(
        {
            str(record.get("date") or "")
            for record in active_exception_records(decisions)
            if record.get("date")
        }
    )


def _affected_tournament_ids(plan: Mapping[str, Any], on_date: str) -> list[str]:
    return sorted(
        str(tournament.get("id") or "")
        for tournament in plan.get("tournaments", []) or []
        if tournament.get("id")
        and not tournament.get("cancelled")
        and str(tournament.get("date") or "") == on_date
    )


def _derived_reason(problem: Mapping[str, Any] | None, on_date: str) -> str | None:
    parsed = _parse_iso_date(on_date, "date")
    start, end = problem_window(problem)
    if start is None or end is None:
        return None
    return holiday_exclusions(start, end).get(parsed)


def exception_report(
    plan: Mapping[str, Any],
    decisions: Mapping[str, Any],
    *,
    problem: Mapping[str, Any] | None = None,
    include_released: bool = False,
) -> list[dict[str, Any]]:
    """Lifecycle + evidence report for holiday-policy exceptions."""

    entries: list[dict[str, Any]] = []
    for record in exception_records(decisions, include_released=include_released):
        status = str(record.get("status") or ACTIVE)
        on_date = str(record.get("date") or "")
        derived_reason = _derived_reason(problem, on_date) if on_date else None
        entries.append(
            {
                **record,
                "status": status,
                "derived_holiday_policy_reason": derived_reason,
                "currently_overrides_derived_policy": status == ACTIVE and bool(derived_reason),
                "affected_tournament_ids": _affected_tournament_ids(plan, on_date)
                if status == ACTIVE
                else [],
            }
        )
    return entries


def append_exceptions(decisions: dict[str, Any], records: list[dict[str, Any]]) -> None:
    """Append exception records, ignoring duplicate ids."""

    existing = decisions.setdefault(HOLIDAY_DATE_EXCEPTIONS_KEY, [])
    existing_ids = {
        str(record.get("id") or "")
        for record in existing
        if isinstance(record, Mapping)
    }
    for record in records:
        record_id = str(record.get("id") or "")
        if record_id and record_id not in existing_ids:
            existing.append(dict(record))
            existing_ids.add(record_id)


def project_exceptions_into_problem(
    problem: Mapping[str, Any] | None,
    decisions: Mapping[str, Any],
) -> dict[str, Any]:
    """Return *problem* with active holiday exceptions projected into it."""

    projected = dict(problem or {})
    active = active_exception_dates(decisions)
    if not active:
        return projected
    existing = [str(value) for value in (projected.get("holiday_date_exceptions") or []) if str(value)]
    projected["holiday_date_exceptions"] = sorted(set(existing) | set(active))
    return projected


__all__ = [
    "ACTIVE",
    "RELEASED",
    "HOLIDAY_EXCEPTION_PREFIX",
    "HOLIDAY_EXCEPTION_TYPE",
    "HolidayDateExceptionError",
    "active_exception_dates",
    "active_exception_records",
    "append_exceptions",
    "exception_records",
    "exception_report",
    "holiday_date_exception_id",
    "project_exceptions_into_problem",
    "validate_and_normalize",
]
