"""Canonical semantics for reserved guest slots on a tournament.

A *guest slot* is an explicit, first-class reserved place inside one
tournament that is intentionally unavailable to normal RVV participant
selection. It exists so a team from another league/region can later apply to
participate without the planner mistaking a deliberately-short RVV roster for
an underfilled tournament, and without adding a fake RVV team that would
distort participation, hosting, travel or fairness metrics.

Two distinct things are represented here:

``guest_slots``
    A list of reservation records on a tournament. Each record is one place
    with a lifecycle status:

    - ``open``     reserved but not yet accepted by an external team;
    - ``filled``   accepted by a known external team (that team is stored as a
                   non-RVV ``guest`` participant);
    - ``released`` the reservation was withdrawn and no longer reserves a place.

``reserved_guest_slots``
    The *derived* number of active reservations (``open`` + ``filled``). It is
    persisted as a readability convenience only; the record list stays
    authoritative, and a legacy/operator payload that only carries the integer
    is normalized into that many ``open`` records.

A single implementation lives here so the verifier, participant capacity,
canonical lifecycle operations, exports and audit evidence cannot disagree
about what a reservation means.
"""

from __future__ import annotations

import uuid
from typing import Any, Iterable, List, Mapping, Optional

GUEST_SLOT_OPEN = "open"
GUEST_SLOT_FILLED = "filled"
GUEST_SLOT_RELEASED = "released"
ACTIVE_GUEST_SLOT_STATUSES = (GUEST_SLOT_OPEN, GUEST_SLOT_FILLED)

# The immediate production requirement is JU10/JU12 guest opportunities, but
# the model is deliberately age-group-agnostic: nothing below special-cases an
# age group, so the capability can be enabled elsewhere without a migration.
DEFAULT_GUEST_AGE_GROUPS = ("JU10", "JU12")


def _is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def _field(container: Any, name: str, default: Any = None) -> Any:
    if container is None:
        return default
    if _is_mapping(container):
        value = container.get(name)
    else:
        value = getattr(container, name, None)
    return default if value is None else value


def _raw_field(container: Any, name: str, default: Any = None) -> Any:
    """Read a field without triggering a derived property of the same name.

    ``Tournament.reserved_guest_slots`` is a property computed from the record
    list, so reading it from inside the record normalizer would recurse. The
    raw field is what a legacy/simple payload actually persisted.
    """

    if container is None:
        return default
    if _is_mapping(container):
        value = container.get(name)
    else:
        value = getattr(container, "__dict__", {}).get(name)
    return default if value is None else value


def guest_slot_records(tournament: Any) -> List[dict]:
    """Return the tournament's reservation records, normalized and copied.

    A payload that only carries an integer ``reserved_guest_slots`` is
    normalized into that many synthetic ``open`` records so older/simpler
    operator input is understood without a separate code path.
    """

    raw = _raw_field(tournament, "guest_slots", None)
    records: List[dict] = []
    if isinstance(raw, list):
        for index, record in enumerate(raw):
            if not isinstance(record, Mapping):
                continue
            normalized = dict(record)
            normalized.setdefault("id", f"guest:{index + 1}")
            normalized.setdefault("status", GUEST_SLOT_OPEN)
            records.append(normalized)
    if records:
        return records

    legacy_count = _raw_field(tournament, "reserved_guest_slots", 0)
    try:
        count = int(legacy_count)
    except (TypeError, ValueError):
        count = 0
    return [
        {"id": f"guest:{index + 1}", "status": GUEST_SLOT_OPEN}
        for index in range(max(0, count))
    ]


def active_guest_slots(tournament: Any) -> List[dict]:
    """Return reservations that still occupy a place (``open`` or ``filled``)."""

    return [
        record
        for record in guest_slot_records(tournament)
        if str(record.get("status") or GUEST_SLOT_OPEN) in ACTIVE_GUEST_SLOT_STATUSES
    ]


def open_guest_slots(tournament: Any) -> List[dict]:
    """Return reservations not yet accepted by an external team."""

    return [
        record
        for record in guest_slot_records(tournament)
        if str(record.get("status") or GUEST_SLOT_OPEN) == GUEST_SLOT_OPEN
    ]


def filled_guest_slots(tournament: Any) -> List[dict]:
    """Return reservations accepted by a known external team."""

    return [
        record
        for record in guest_slot_records(tournament)
        if str(record.get("status") or GUEST_SLOT_OPEN) == GUEST_SLOT_FILLED
    ]


def released_guest_slots(tournament: Any) -> List[dict]:
    """Return reservations that were withdrawn and no longer reserve a place."""

    return [
        record
        for record in guest_slot_records(tournament)
        if str(record.get("status") or GUEST_SLOT_OPEN) == GUEST_SLOT_RELEASED
    ]


def active_guest_slot_count(tournament: Any) -> int:
    """Return how many places this tournament reserves for guests."""

    return len(active_guest_slots(tournament))


def open_guest_slot_count(tournament: Any) -> int:
    """Return how many reserved places are still waiting for an external team."""

    return len(open_guest_slots(tournament))


def reserved_guest_slots(tournament: Any) -> int:
    """Canonical accessor for the derived ``reserved_guest_slots`` count."""

    return active_guest_slot_count(tournament)


def is_guest_team(team: Any) -> bool:
    """True when *team* is an external guest participant, not an RVV season team."""

    return bool(_field(team, "guest", False))


def rvv_teams(tournament: Any) -> List[Any]:
    """Return the tournament's real RVV participants (guests excluded)."""

    teams = _field(tournament, "teams", []) or []
    return [team for team in teams if not is_guest_team(team)]


def guest_teams(tournament: Any) -> List[Any]:
    """Return the tournament's external guest participants."""

    teams = _field(tournament, "teams", []) or []
    return [team for team in teams if is_guest_team(team)]


def rvv_team_count(tournament: Any) -> int:
    """Number of real RVV teams participating in the tournament."""

    return len(rvv_teams(tournament))


def tournament_places(tournament: Any) -> int:
    """Total places the tournament currently accounts for.

    This is real RVV participants plus active guest reservations. A filled
    reservation is represented by a guest participant, so it is counted once
    through the participant list; counting records too would double it. Use
    :func:`open_guest_slot_count` for the still-unfilled share.
    """

    return rvv_team_count(tournament) + len(guest_teams(tournament))


def capacity_places(tournament: Any) -> int:
    """Places the tournament accounts for when validating its shape.

    Real participants, filled guests and still-open reservations all occupy a
    place. This is the count that must match the configured capacity/round
    shape, independent of how many of those places are filled by RVV teams.
    """

    return rvv_team_count(tournament) + active_guest_slot_count(tournament)


def has_open_guest_slots(tournament: Any) -> bool:
    """True when at least one reserved place is still waiting for a guest."""

    return open_guest_slot_count(tournament) > 0


def new_guest_slot(
    *,
    reserved_by: str = "operator",
    reserved_at: str = "",
    note: str = "",
    slot_id: Optional[str] = None,
) -> dict:
    """Build one canonical ``open`` reservation record."""

    return {
        "id": slot_id or f"guest-{uuid.uuid4().hex[:8]}",
        "status": GUEST_SLOT_OPEN,
        "reserved_at": reserved_at,
        "reserved_by": reserved_by,
        "note": note or "",
        "external_team": None,
        "filled_at": None,
        "released_at": None,
    }


def guest_slot_summary(tournament: Any) -> dict:
    """Return an audit/export-friendly summary of a tournament's reservations."""

    records = guest_slot_records(tournament)
    if not records:
        return {
            "reserved": 0,
            "open": 0,
            "filled": 0,
            "released": 0,
            "slots": [],
        }
    return {
        "reserved": active_guest_slot_count(tournament),
        "open": open_guest_slot_count(tournament),
        "filled": len(filled_guest_slots(tournament)),
        "released": len(released_guest_slots(tournament)),
        "slots": [dict(record) for record in records],
    }


def total_reserved_across(tournaments: Iterable[Any]) -> int:
    """Return the total active reservations across *tournaments*."""

    return sum(active_guest_slot_count(tournament) for tournament in tournaments)


__all__ = [
    "ACTIVE_GUEST_SLOT_STATUSES",
    "DEFAULT_GUEST_AGE_GROUPS",
    "GUEST_SLOT_FILLED",
    "GUEST_SLOT_OPEN",
    "GUEST_SLOT_RELEASED",
    "active_guest_slot_count",
    "active_guest_slots",
    "capacity_places",
    "filled_guest_slots",
    "guest_slot_records",
    "guest_slot_summary",
    "guest_teams",
    "has_open_guest_slots",
    "is_guest_team",
    "new_guest_slot",
    "open_guest_slot_count",
    "open_guest_slots",
    "released_guest_slots",
    "reserved_guest_slots",
    "rvv_team_count",
    "rvv_teams",
    "total_reserved_across",
    "tournament_places",
]
