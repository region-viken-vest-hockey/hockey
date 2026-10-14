"""Ice-resource footprint model for calendar booking evidence.

Some halls expose one bookable ice surface as several separately bookable
resources (for example Skien's ``Bane 1`` and ``Bane 2``). A booking occupies
one or more of those resources. Two bookings on different resources at the
same time are two real occupancies, never duplicate copies of one event.

Resource detail is modelled only for clubs whose validated club profile
declares a miniputt tournament footprint. Other clubs keep resource-agnostic
evidence, for example Frisk Asker, whose resource labels are audit metadata
only. The source scraper keeps a booking's resources as structured
``CalendarEvent.resources``; the joined LOCATION label is display text only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from tournament_scheduler.clubs.profiles import get_club_profile
from tournament_scheduler.models_calendar import SourceResource

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

    Empty when the club has no profile or declares no footprint. The profile's
    evidence-backed requirement is the single source used by planning and
    booking reconciliation.
    """

    profile = get_club_profile(club)
    venue = profile.venue_for_arena() if profile else None
    requirement = venue.requirement_for("miniputt") if venue else None
    return requirement.resource_ids if requirement else frozenset()


def resource_ids_for_club(club: str, resources: Iterable[SourceResource]) -> frozenset[str]:
    """Resolve source resource labels against *club*'s configured resources."""

    profile = get_club_profile(club)
    venue = profile.venue_for_arena() if profile else None
    if venue is None:
        return frozenset()
    normalized_labels = {
        str(label).casefold().strip(): resource.id
        for resource in venue.resources
        for label in resource.labels
    }
    resolved: set[str] = set()
    for source_resource in resources:
        label = source_resource.label.casefold().strip()
        if label in normalized_labels:
            resolved.add(normalized_labels[label])
            continue
        # Source labels may add a hall/category prefix while retaining the
        # configured resource label. Match the longest configured label first.
        matches = [resource_id for configured, resource_id in normalized_labels.items() if configured in label]
        if len(set(matches)) == 1:
            resolved.add(matches[0])
    return frozenset(resolved)


def interval_resource_fields(club: str, resources: Iterable[SourceResource]) -> dict[str, list[str]]:
    """Return the resource evidence to store on one busy interval.

    *resources* are the source resources the booking occupies. Only footprint
    clubs get a ``resources`` key, so every other club's interval evidence and
    fingerprint stay exactly as before.
    """

    if not tournament_resource_footprint(club):
        return {}
    ids = sorted(resource_ids_for_club(club, resources))
    return {"resources": ids} if ids else {}


def event_resource_ids(event: Mapping[str, Any]) -> frozenset[str]:
    """Return the resource ids a busy-interval event occupies."""

    labels: Iterable[Any] = event.get("resources") or ()
    return frozenset(str(label) for label in labels)


def event_affects_tournament_footprint(club: str, event: Mapping[str, Any]) -> bool:
    """Whether an event occupies any resource needed by a club's tournament.

    Missing resource detail stays conservative and affects the tournament.
    Clubs without a footprint keep generic behavior. A source event carrying
    only known disjoint resources does not block the configured surface.
    """

    required = tournament_resource_footprint(club)
    occupied = event_resource_ids(event)
    return not required or not occupied or bool(required & occupied)


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
