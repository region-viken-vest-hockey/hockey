"""Canonical operator banned-date lifecycle for promoted-season maintenance.

A **banned date** is a global planning restriction: no tournament may be
scheduled on it, regardless of host or participants. It is distinct from a
typed request constraint (one club/team cannot participate) and from the
holiday-policy exclusions owned by :mod:`tournament_scheduler.date_policy`.

This module owns the durable record, its deterministic identity/validation, the
derived report over a canonical plan, and the projection into the *existing*
normalized ``planning_problem.manual_adjustments.banned_dates`` read path that
the planner, verifier, candidate-weekend enumeration, repair/search and
optimizer already consume. It deliberately does not derive its own forbidden
dates and does not add a second verifier rule: the existing
``banned_date_used`` hard rule in ``planning_contract`` remains the single
authority.

Recording a ban is a policy/decision write and is allowed even while the current
schedule still uses that date -- the date becomes a hard violation until the
schedule is repaired. Policy mutation and schedule repair stay separate
operations, exactly like ``season add-constraint``.
"""

from __future__ import annotations

from datetime import date as _date
from typing import Any, Mapping

from tournament_scheduler.canonical_state import BANNED_DATES_KEY
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

ACTIVE = "active"
RELEASED = "released"

BANNED_DATE_PREFIX = "banned-date"
BANNED_DATE_TYPE = "banned_date"
#: Reason recorded when a canonical operator ban is projected into the
#: planning problem's ``manual_adjustments.banned_dates``.
BANNED_DATE_REASON = "banned by operator"


class BannedDateError(ValueError):
    """Raised when a banned-date definition is malformed."""


def _parse_iso_date(value: Any, field: str) -> _date:
    try:
        return _date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise BannedDateError(f"Invalid {field}: {value!r}; expected YYYY-MM-DD") from exc


def banned_date_id(on_date: str, request_id: str) -> str:
    """Deterministic identity for one operator ban of *on_date*.

    Two records for the same date are never active at once, so the id is
    stable for change detection while the request id remains provenance.
    """

    digest = stable_payload_sha256(
        {"date": str(on_date), "request_id": str(request_id)}
    )
    return f"{BANNED_DATE_PREFIX}:{digest[:16]}"


def validate_and_normalize(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one operator ban payload and return its normalized record form."""

    if not isinstance(raw, Mapping):
        raise BannedDateError("A banned date must be an object")
    on_date = _parse_iso_date(raw.get("date"), "date").isoformat()
    request_id = str(raw.get("request_id") or "").strip()
    if not request_id:
        raise BannedDateError("A banned date requires a stable request_id")
    return {
        "id": banned_date_id(on_date, request_id),
        "type": BANNED_DATE_TYPE,
        "date": on_date,
        "request_id": request_id,
        "note": str(raw.get("note") or ""),
    }


def banned_date_records(
    decisions: Mapping[str, Any],
    *,
    include_released: bool = False,
) -> list[dict[str, Any]]:
    """Return the durable banned-date records, newest provenance preserved."""

    records: list[dict[str, Any]] = []
    for record in decisions.get(BANNED_DATES_KEY, []) or []:
        if not isinstance(record, Mapping):
            continue
        status = str(record.get("status") or ACTIVE)
        if status != ACTIVE and not include_released:
            continue
        records.append(dict(record))
    return records


def active_banned_date_records(decisions: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return only the currently-active banned-date records."""

    return [
        record
        for record in banned_date_records(decisions, include_released=True)
        if str(record.get("status") or ACTIVE) == ACTIVE
    ]


def active_banned_dates(decisions: Mapping[str, Any]) -> list[str]:
    """Return the sorted ISO dates currently banned for the whole season."""

    return sorted({str(record.get("date") or "") for record in active_banned_date_records(decisions) if record.get("date")})


def _affected_tournament_ids(plan: Mapping[str, Any], on_date: str) -> list[str]:
    return sorted(
        str(tournament.get("id") or "")
        for tournament in plan.get("tournaments", []) or []
        if tournament.get("id")
        and not tournament.get("cancelled")
        and str(tournament.get("date") or "") == on_date
    )


def banned_date_report(
    plan: Mapping[str, Any],
    decisions: Mapping[str, Any],
    *,
    include_released: bool = False,
) -> list[dict[str, Any]]:
    """Derived lifecycle + current-satisfaction report for banned dates.

    Each entry reports whether the date is currently satisfied (no tournament
    scheduled on it) and the exact affected tournament ids, so an agent can
    derive the repair scope from one command.
    """

    entries: list[dict[str, Any]] = []
    for record in banned_date_records(decisions, include_released=include_released):
        status = str(record.get("status") or ACTIVE)
        on_date = str(record.get("date") or "")
        if status == ACTIVE:
            affected = _affected_tournament_ids(plan, on_date)
            satisfied: bool | None = not affected
        else:
            affected = []
            satisfied = None
        entries.append(
            {
                **record,
                "status": status,
                "satisfied": satisfied,
                "affected_tournament_ids": affected,
            }
        )
    return entries


def append_banned_dates(decisions: dict[str, Any], records: list[dict[str, Any]]) -> None:
    """Append banned-date records, ignoring duplicate ids."""

    existing = decisions.setdefault(BANNED_DATES_KEY, [])
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


def project_banned_dates_into_problem(
    problem: Mapping[str, Any] | None,
    decisions: Mapping[str, Any],
) -> dict[str, Any]:
    """Return *problem* with the active canonical bans in its banned_dates.

    This is the one projection that makes the canonical operator lifecycle flow
    through the existing normalized planning contract. It is a pure union with
    any bans already carried by the problem, so a policy flip never silently
    drops a ban that originated elsewhere.
    """

    projected = dict(problem or {})
    active = active_banned_dates(decisions)
    if not active:
        return projected
    manual = dict(projected.get("manual_adjustments") or {})
    existing = [str(value) for value in (manual.get("banned_dates") or []) if str(value)]
    manual["banned_dates"] = sorted(set(existing) | set(active))
    projected["manual_adjustments"] = manual
    return projected


__all__ = [
    "ACTIVE",
    "BANNED_DATE_PREFIX",
    "BANNED_DATE_REASON",
    "BANNED_DATE_TYPE",
    "RELEASED",
    "BannedDateError",
    "active_banned_date_records",
    "active_banned_dates",
    "append_banned_dates",
    "banned_date_id",
    "banned_date_records",
    "banned_date_report",
    "project_banned_dates_into_problem",
    "validate_and_normalize",
]
