"""Planner-neutral availability semantics for calendar evidence.

Calendar evidence is not a binary "busy/free" fact. A scraped interval can be:

``fixed_busy``
    Occupied by a commitment the host club cannot displace automatically
    (a genuine external booking). Placement inside it is a real conflict.
``movable_busy``
    Currently occupies ice, but the host club itself controls it and may
    move/replace it to make room for an RVV tournament (the production
    example is Kongsberg's ``Åpen ishall``). It is a *candidate* placement
    opportunity for that host, not a hard conflict -- but using it carries
    an explicit host-confirmation requirement.
``free``
    Verified free interval.
``unknown``
    No trustworthy evidence; must never be assumed free.

The distinction is owned here, planner-independently, so it survives
normalization into the ``planning_problem`` availability contract and is
consumed identically by the baseline planner, the Stage 3 search/repair
capabilities, the verifier and the audit evidence.

Event titles are classified through explicit, source/club-configured rules
(``ClubCalendarSource.event_classification_rules``) rather than a global
phrase table: another club/source could use the same words differently.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Tuple


class CalendarAvailability(str, Enum):
    """Semantic availability classification for one calendar interval."""

    FIXED_BUSY = "fixed_busy"
    MOVABLE_BUSY = "movable_busy"
    FREE = "free"
    UNKNOWN = "unknown"


#: Provenance of one interval's classification, carried alongside the
#: availability value so a controller/audit can tell a *configured* fact apart
#: from an *unclassified* scraped event. ``unclassified`` is a fact, not a
#: verdict: the interval must still never be assumed free, but the controller
#: may investigate whether the host controls it (see the ``movable_busy``
#: interpretation overlay applied by the host-placement repair provider).
CLASSIFICATION_SOURCE_CONFIGURED = "configured"
CLASSIFICATION_SOURCE_CLUB_DEFAULT = "club_default"
CLASSIFICATION_SOURCE_UNCLASSIFIED = "unclassified"
CLASSIFICATION_SOURCE_INFERRED = "inferred"

#: Maps the semantic classification back onto the legacy ``kind`` tag the
#: interval contract already carried (``"external"``/``"club_controlled"``), so
#: existing consumers keep working while the explicit ``availability`` field
#: becomes the authoritative fact.
LEGACY_KIND_BY_AVAILABILITY: dict[CalendarAvailability, str] = {
    CalendarAvailability.FIXED_BUSY: "external",
    CalendarAvailability.MOVABLE_BUSY: "club_controlled",
    CalendarAvailability.FREE: "free",
    CalendarAvailability.UNKNOWN: "unknown",
}


def legacy_kind(availability: CalendarAvailability) -> str:
    """Return the earlier legacy ``kind`` tag for *availability*."""
    return LEGACY_KIND_BY_AVAILABILITY.get(availability, "external")


def normalize_event_name(name: str) -> str:
    """Unicode NFKC + casefold + collapsed whitespace, for rule matching."""
    return " ".join(unicodedata.normalize("NFKC", str(name)).casefold().split())


@dataclass(frozen=True)
class CalendarEventClassificationRule:
    """One source/club-configured event-title classification rule.

    ``pattern`` is matched as a normalized substring of the event title, so a
    rule cannot accidentally depend on exact case/whitespace/diacritic form.
    """

    pattern: str
    classification: CalendarAvailability
    reason: str

    def matches(self, event_name: str) -> bool:
        if not self.pattern:
            return False
        return normalize_event_name(self.pattern) in normalize_event_name(event_name)


def classify_event_name(
    event_name: str,
    rules: Optional[Iterable[CalendarEventClassificationRule]],
    *,
    default: CalendarAvailability = CalendarAvailability.FIXED_BUSY,
) -> Tuple[CalendarAvailability, str]:
    """Classify *event_name* against *rules* (first match wins).

    Returns ``(classification, reason)``. When no rule matches, the caller's
    *default* applies and the reason is empty -- there is no global phrase
    table, so an unconfigured club never silently turns bookings into movable
    capacity.
    """
    for rule in rules or ():
        if rule.matches(event_name):
            return rule.classification, rule.reason
    return default, ""


def interval_availability(entry: Mapping[str, Any]) -> CalendarAvailability:
    """Resolve one serialized ``club_busy_intervals`` entry's classification.

    Prefers the explicit ``availability`` field; falls back to the legacy
    ``kind`` tag (``club_controlled`` -> ``movable_busy``, anything else ->
    ``fixed_busy``) so older checkpoints and hand-built problems keep their
    previous, conservative meaning.
    """
    if not isinstance(entry, Mapping):
        return CalendarAvailability.UNKNOWN
    explicit = entry.get("availability")
    if explicit:
        try:
            return CalendarAvailability(str(explicit))
        except ValueError:
            pass
    kind = str(entry.get("kind", "external"))
    if kind == "club_controlled":
        return CalendarAvailability.MOVABLE_BUSY
    if kind == "free":
        return CalendarAvailability.FREE
    if kind == "unknown":
        return CalendarAvailability.UNKNOWN
    return CalendarAvailability.FIXED_BUSY


def host_confirmation_from_evidence(
    evidence: Optional[Mapping[str, Any]],
) -> Tuple[bool, Optional[str]]:
    """Return ``(requires_confirmation, reason)`` for a slot-search evidence dict.

    Shared by the baseline planner and the hosting-repair paths so every code
    path that consumes a movable slot records the same explicit
    host-confirmation requirement (and what must be moved) on the tournament.
    """
    if not evidence or not evidence.get("requires_host_confirmation"):
        return False, None
    event_title = str(evidence.get("calendar_event") or "").strip()
    detail = str(evidence.get("reason") or "").strip()
    if event_title and detail:
        return True, f"{event_title} — {detail}"
    return True, event_title or detail or (
        "Host-controlled interval must be moved/replaced before the placement is confirmed."
    )


def classification_rules_for_club(club: str) -> Tuple[CalendarEventClassificationRule, ...]:
    """Return the configured rule tuple for *club* (empty when unconfigured).

    Imported lazily to avoid a module import cycle: ``club_registry`` imports
    this module for the rule type, so this module must not import it eagerly.
    """
    from .club_registry import CLUB_REGISTRY

    entry = CLUB_REGISTRY.get(club)
    return tuple(entry.event_classification_rules) if entry is not None else ()


def club_default_availability(club: str) -> CalendarAvailability:
    """Default classification for a club with no matching event rule.

    A club whose registry entry declares ``club_controlled_calendar`` defaults
    its whole calendar to ``movable_busy`` (the earlier behavior); every other
    club defaults to ``fixed_busy``.
    """
    from .club_registry import CLUB_REGISTRY

    entry = CLUB_REGISTRY.get(club)
    if entry is not None and entry.club_controlled_calendar:
        return CalendarAvailability.MOVABLE_BUSY
    return CalendarAvailability.FIXED_BUSY


def classify_club_event(
    club: str,
    event_name: str,
) -> Tuple[CalendarAvailability, str]:
    """Classify one event title for *club* using its configured rules."""
    return classify_event_name(
        event_name,
        classification_rules_for_club(club),
        default=club_default_availability(club),
    )


def classify_club_event_detailed(
    club: str,
    event_name: str,
) -> Tuple[CalendarAvailability, str, str]:
    """Classify one event title and report *how* it was classified.

    Returns ``(availability, reason, classification_source)`` where
    ``classification_source`` is one of :data:`CLASSIFICATION_SOURCE_CONFIGURED`
    (an explicit per-club event rule matched),
    :data:`CLASSIFICATION_SOURCE_CLUB_DEFAULT` (the club declared its whole
    calendar club-controlled) or :data:`CLASSIFICATION_SOURCE_UNCLASSIFIED`
    (nothing is known about this title). Only the last case is ambiguous and
    eligible for a controller-requested inferred interpretation; a configured
    fact is always the preferred deterministic fast path.
    """
    for rule in classification_rules_for_club(club):
        if rule.matches(event_name):
            return rule.classification, rule.reason, CLASSIFICATION_SOURCE_CONFIGURED
    availability = club_default_availability(club)
    if availability == CalendarAvailability.MOVABLE_BUSY:
        return availability, "", CLASSIFICATION_SOURCE_CLUB_DEFAULT
    return availability, "", CLASSIFICATION_SOURCE_UNCLASSIFIED


def is_unclassified_event(
    club: str,
    event_name: str,
) -> bool:
    """True when nothing configured classifies *event_name* for *club*."""
    _, _, source = classify_club_event_detailed(club, event_name)
    return source == CLASSIFICATION_SOURCE_UNCLASSIFIED


def unclassified_intervals(
    club_busy_intervals: Optional[Mapping[str, Iterable[Mapping[str, Any]]]],
) -> list[dict[str, Any]]:
    """Flatten the intervals nothing configured has classified.

    Read-only exposure so the controller can see a raw scraped event as an
    *ambiguous* fact (club, date, interval, title) instead of only the
    conservative ``fixed_busy`` verdict. Classification is re-derived from the
    event title via the same per-club rules the normalizer uses -- the
    serialized interval contract itself is unchanged. It deliberately does not
    decide that any of them is movable; an explicit per-club rule remains the
    fast path and this list only marks what could be investigated.
    """
    out: list[dict[str, Any]] = []
    for club, entries in (club_busy_intervals or {}).items():
        for entry in entries or ():
            title = str(entry.get("calendar_event") or "")
            if not title or not is_unclassified_event(str(club), title):
                continue
            row = {"club": str(club)}
            for key in ("date", "start", "end", "calendar_event", "availability"):
                value = entry.get(key)
                if value:
                    row[key] = value
            out.append(row)
    out.sort(key=lambda row: (row["club"], row.get("date", ""), row.get("start", "")))
    return out
