"""Canonical per-tournament ice-time/duration override lifecycle.

The age-group ``ice_time_minutes`` configuration is the authoritative default
occupancy window for every tournament in that age group. A host may, however,
confirm that one specific tournament instance actually uses less ice than that
default (for example two tournaments sharing a two-hour window). This module
owns the durable, audited decision that binds one host-confirmed duration to one
canonical tournament.

An override is deliberately *decision-only*: it never edits the schedule or the
age-group configuration, so every placement/booking consumer must resolve the
effective duration through the projection owned here. The projected problem key
``ice_time_minutes_overrides`` is read by arena-interval verification and by the
calendar-booking interval-coverage check, so shortening a real host-confirmed
window never happens silently and always advances the canonical revision.

The same host-confirmation rationale as the ``booking-set`` stated-interval
follow-up path applies: a source-stated shorter window is retained as evidence
first; only an explicit operator-authorized override may narrow the canonical
occupancy used by verification.
"""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.canonical_state import ICE_TIME_OVERRIDES_KEY
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

ACTIVE = "active"
RELEASED = "released"

ICE_TIME_OVERRIDE_PREFIX = "ice-time-override"
ICE_TIME_OVERRIDE_TYPE = "tournament_ice_time_override"
#: Planning-problem key every duration consumer reads for per-tournament values.
ICE_TIME_OVERRIDES_PROBLEM_KEY = "ice_time_minutes_overrides"


class IceTimeOverrideError(ValueError):
    """Raised when a per-tournament ice-time override payload is malformed."""


def _positive_int(value: Any) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError) as exc:
        raise IceTimeOverrideError(
            f"Invalid ice-time override minutes: {value!r}; expected a positive integer"
        ) from exc
    if minutes <= 0:
        raise IceTimeOverrideError(
            f"Invalid ice-time override minutes: {value!r}; expected a positive integer"
        )
    return minutes


def ice_time_override_id(tournament_id: str, request_id: str, minutes: int) -> str:
    """Deterministic identity for one tournament's override decision.

    The request id plus the resolved duration are provenance: a repeated request
    for the same tournament and duration is idempotent, while a genuinely new
    decision gets a new record and supersedes the previous active one.
    """

    digest = stable_payload_sha256(
        {
            "tournament_id": str(tournament_id),
            "request_id": str(request_id),
            "minutes": int(minutes),
        }
    )
    return f"{ICE_TIME_OVERRIDE_PREFIX}:{digest[:16]}"


def validate_and_normalize(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one override payload and return its normalized record fields."""

    if not isinstance(raw, Mapping):
        raise IceTimeOverrideError("An ice-time override must be an object")
    tournament_id = str(raw.get("tournament_id") or "").strip()
    if not tournament_id:
        raise IceTimeOverrideError("An ice-time override requires a tournament_id")
    request_id = str(raw.get("request_id") or "").strip()
    if not request_id:
        raise IceTimeOverrideError("An ice-time override requires a stable request_id")
    minutes = _positive_int(raw.get("minutes"))
    return {
        "id": ice_time_override_id(tournament_id, request_id, minutes),
        "type": ICE_TIME_OVERRIDE_TYPE,
        "tournament_id": tournament_id,
        "minutes": minutes,
        "request_id": request_id,
        "note": str(raw.get("note") or ""),
        "reference": str(raw.get("reference") or ""),
    }


def override_records(
    decisions: Mapping[str, Any],
    *,
    include_released: bool = False,
) -> list[dict[str, Any]]:
    """Return durable override records, newest provenance preserved."""

    records: list[dict[str, Any]] = []
    for record in decisions.get(ICE_TIME_OVERRIDES_KEY, []) or []:
        if not isinstance(record, Mapping):
            continue
        status = str(record.get("status") or ACTIVE)
        if status != ACTIVE and not include_released:
            continue
        records.append(dict(record))
    return records


def active_override_records(decisions: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the active override records."""

    return [
        record
        for record in override_records(decisions, include_released=True)
        if str(record.get("status") or ACTIVE) == ACTIVE
    ]


def active_overrides(decisions: Mapping[str, Any]) -> dict[str, int]:
    """Return the effective active override ``{tournament_id: minutes}``.

    When several active records somehow exist for one tournament (for example a
    legacy hand-edited file), the most recently created one wins so the report
    and every duration consumer agree instead of silently disagreeing.
    """

    by_tournament: dict[str, dict[str, Any]] = {}
    for record in active_override_records(decisions):
        tournament_id = str(record.get("tournament_id") or "")
        if not tournament_id:
            continue
        previous = by_tournament.get(tournament_id)
        if previous is None or str(record.get("created_at") or "") >= str(
            previous.get("created_at") or ""
        ):
            by_tournament[tournament_id] = record
    resolved: dict[str, int] = {}
    for tournament_id, record in by_tournament.items():
        try:
            minutes = int(record.get("minutes") or 0)
        except (TypeError, ValueError):
            continue
        if minutes > 0:
            resolved[tournament_id] = minutes
    return resolved


def override_for_tournament(
    decisions: Mapping[str, Any],
    tournament_id: str,
) -> dict[str, Any] | None:
    """Return the active override record for one tournament, if any."""

    matches = [
        record
        for record in active_override_records(decisions)
        if str(record.get("tournament_id") or "") == tournament_id
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda record: str(record.get("created_at") or ""))[-1]


def append_overrides(decisions: dict[str, Any], records: list[dict[str, Any]]) -> None:
    """Append override records, ignoring duplicate ids."""

    existing = decisions.setdefault(ICE_TIME_OVERRIDES_KEY, [])
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


def overrides_from_problem(problem: Mapping[str, Any] | None) -> dict[str, int]:
    """Return the projected per-tournament overrides carried by *problem*."""

    raw = (problem or {}).get(ICE_TIME_OVERRIDES_PROBLEM_KEY) or {}
    if not isinstance(raw, Mapping):
        return {}
    resolved: dict[str, int] = {}
    for tournament_id, minutes in raw.items():
        try:
            value = int(minutes)
        except (TypeError, ValueError):
            continue
        if value > 0:
            resolved[str(tournament_id)] = value
    return resolved


def project_overrides_into_problem(
    problem: Mapping[str, Any] | None,
    decisions: Mapping[str, Any],
) -> dict[str, Any]:
    """Return *problem* with the active canonical overrides projected into it.

    The projection is a pure function of the durable decision records: a
    released override disappears and the age-group default applies again. Any
    override already carried by the problem is preserved unless a decision
    explicitly supersedes it, so a problem passed down from an outer caller is
    never silently stripped.
    """

    projected = dict(problem or {})
    active = active_overrides(decisions)
    if not active:
        return projected
    existing = overrides_from_problem(projected)
    existing.update(active)
    projected[ICE_TIME_OVERRIDES_PROBLEM_KEY] = existing
    return projected


def apply_overrides_to_plan(plan: Any, problem: Mapping[str, Any] | None) -> None:
    """Annotate a loaded plan's in-memory tournaments with the projected overrides.

    Export renderers receive ``Tournament`` objects plus only a per-age-group
    duration mapping, so they cannot resolve a per-tournament override on their
    own. Stamping the resolved value on the transient ``Tournament`` objects
    lets every renderer that calls ``occupancy.tournament_end_time`` agree with
    the verifier without a second resolution path. The annotation is never
    serialized back into the canonical schedule.
    """

    overrides = overrides_from_problem(problem)
    if not overrides:
        return
    for tournament in getattr(plan, "tournaments", []) or []:
        minutes = overrides.get(str(getattr(tournament, "id", "") or ""))
        if isinstance(minutes, int) and minutes > 0:
            tournament.ice_time_minutes_override = minutes


__all__ = [
    "ACTIVE",
    "ICE_TIME_OVERRIDE_PREFIX",
    "ICE_TIME_OVERRIDE_TYPE",
    "ICE_TIME_OVERRIDES_PROBLEM_KEY",
    "RELEASED",
    "IceTimeOverrideError",
    "active_override_records",
    "active_overrides",
    "append_overrides",
    "apply_overrides_to_plan",
    "ice_time_override_id",
    "override_for_tournament",
    "override_records",
    "overrides_from_problem",
    "project_overrides_into_problem",
    "validate_and_normalize",
]
