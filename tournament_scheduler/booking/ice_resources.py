"""Ice-resource footprint model for calendar booking evidence.

Some halls expose one bookable ice surface as several separately bookable
resources (for example Skien's ``Bane 1`` and ``Bane 2``). A booking occupies
one or more of those resources. Two bookings on different resources at the
same time are two real occupancies, never duplicate copies of one event.

Resource detail is modelled only for clubs that declare a tournament footprint
in ``MINIPUTT_RESOURCE_FOOTPRINTS``. Other clubs keep resource-agnostic
evidence, for example Frisk Asker, whose resource labels are audit metadata
only. A booking that spans several resources is stored with a joined LOCATION
label (``"Ishockey - bane 1 + Ishockey - bane 2"``) by the source scraper.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

# Labels the footprint table names, e.g. "Bane 1" -> "bane-1". Club names map to
# the labels a miniputt tournament occupies at that club's arena.
MINIPUTT_RESOURCE_FOOTPRINTS: dict[str, tuple[str, ...]] = {
    # Skien's BRP/Exigo calendar books the hockey ice as two resources. A
    # miniputt tournament is played across the whole surface, so it needs both;
    # a booking on one resource alone is partial occupancy.
    "Skien": ("Bane 1", "Bane 2"),
}

_BANE_LABEL_RE = re.compile(r"\bbane\s*(\d+)\b", re.IGNORECASE)

COVERAGE_COVERS = "covers"
COVERAGE_PARTIAL = "partial"
COVERAGE_DISJOINT = "disjoint"


def resource_ids_in_text(text: Any) -> frozenset[str]:
    """Return the stable resource ids named in a source LOCATION label.

    ``"Ishockey - bane 1 + Ishockey - bane 2"`` maps to ``{"bane-1", "bane-2"}``.
    A label without a recognised resource number yields an empty set.
    """

    return frozenset(f"bane-{int(number)}" for number in _BANE_LABEL_RE.findall(str(text or "")))


def tournament_resource_footprint(club: str) -> frozenset[str]:
    """Return the resource ids a miniputt tournament hosted by *club* requires.

    Empty when the club declares no footprint.
    """

    return frozenset().union(*(resource_ids_in_text(label) for label in MINIPUTT_RESOURCE_FOOTPRINTS.get(club, ())))


def interval_resource_fields(club: str, location: Any) -> dict[str, list[str]]:
    """Return the resource evidence to store on one busy interval.

    Only footprint clubs get a ``resources`` key, so every other club's interval
    evidence and fingerprint stay exactly as before.
    """

    if not tournament_resource_footprint(club):
        return {}
    resources = sorted(resource_ids_in_text(location))
    return {"resources": resources} if resources else {}


def event_resource_ids(event: Mapping[str, Any]) -> frozenset[str]:
    """Return the resource ids a busy-interval event occupies."""

    labels: Iterable[Any] = event.get("resources") or ()
    return frozenset(str(label) for label in labels)


def footprint_coverage(
    required: frozenset[str],
    occupied: frozenset[str],
) -> str | None:
    """Compare a tournament's required footprint with an event's occupancy.

    Returns ``None`` when either side is unknown, so the event is judged exactly
    as before. Otherwise:

    * ``covers``: the event occupies every required resource;
    * ``partial``: it occupies some, but not all, of the required resources;
    * ``disjoint``: it occupies none of them.
    """

    if not required or not occupied:
        return None
    if required <= occupied:
        return COVERAGE_COVERS
    if required & occupied:
        return COVERAGE_PARTIAL
    return COVERAGE_DISJOINT
