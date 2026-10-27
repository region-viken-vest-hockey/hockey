"""Validated repository-owned club/venue operational profiles.

Profiles contain durable club-specific facts that affect more than one workflow,
such as a tournament's required resource footprint or club-controlled ice
availability.  They are data, not planner exceptions: planning, conflict
checking and booking reconciliation read the same typed profile API.

A club with no profile keeps the generic behavior of the existing registry and
calendar evidence paths.  Every consequential profile policy must carry an
auditable evidence or operator-decision reference.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from importlib.resources import files
from typing import Any, Mapping


PROFILE_SCHEMA_VERSION = 1
PROFILE_PACKAGE = "tournament_scheduler.club_profiles"


@dataclass(frozen=True)
class PolicyEvidence:
    kind: str
    reference: str
    summary: str
    recorded_at: str


@dataclass(frozen=True)
class VenueResource:
    id: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class ResourceRequirement:
    activity: str
    mode: str
    resource_ids: frozenset[str]
    evidence: PolicyEvidence


@dataclass(frozen=True)
class VenueProfile:
    arena: str
    resources: tuple[VenueResource, ...]
    requirements: tuple[ResourceRequirement, ...]

    def requirement_for(self, activity: str) -> ResourceRequirement | None:
        return next((item for item in self.requirements if item.activity == activity), None)


@dataclass(frozen=True)
class WeeklyAvailabilityWindow:
    weekdays: frozenset[int]
    start: str
    end: str


@dataclass(frozen=True)
class AvailabilityPolicy:
    id: str
    authority: str
    planning_semantics: str
    windows: tuple[WeeklyAvailabilityWindow, ...]
    outside_semantics: str
    evidence: PolicyEvidence

    def covers(self, on_date: date, start: str, end: str) -> bool:
        start_minutes = _hhmm_minutes(start)
        end_minutes = _hhmm_minutes(end)
        if start_minutes is None or end_minutes is None or end_minutes <= start_minutes:
            return False
        return any(
            on_date.weekday() in window.weekdays
            and (_hhmm_minutes(window.start) or 0) <= start_minutes
            and end_minutes <= (_hhmm_minutes(window.end) or 0)
            for window in self.windows
        )


@dataclass(frozen=True)
class CalendarBookingPresence:
    """Whether a club's public calendar evidences its tournament bookings.

    ``publishes_tournament_bookings=False`` records a documented source
    limitation: the club's calendar may show conflicting ice use, but it does
    not list its tournament bookings, so an absent event proves nothing about
    presence (#853).
    """

    publishes_tournament_bookings: bool
    evidence: PolicyEvidence


@dataclass(frozen=True)
class ClubProfile:
    club: str
    venues: tuple[VenueProfile, ...]
    availability_policies: tuple[AvailabilityPolicy, ...]
    source_path: str
    calendar_booking_presence: CalendarBookingPresence | None = None

    def venue_for_arena(self, arena: str | None = None) -> VenueProfile | None:
        if arena:
            match = next((item for item in self.venues if item.arena.casefold() == arena.casefold()), None)
            if match is not None:
                return match
        return self.venues[0] if len(self.venues) == 1 else None

    def availability_for_interval(self, on_date: date, start: str, end: str) -> AvailabilityPolicy | None:
        return next((policy for policy in self.availability_policies if policy.covers(on_date, start, end)), None)


def _hhmm_minutes(value: str) -> int | None:
    try:
        hour, minute = (int(part) for part in str(value).split(":", 1))
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 24 and 0 <= minute <= 59) or (hour == 24 and minute != 0):
        return None
    return hour * 60 + minute


def _required_string(data: Mapping[str, Any], key: str, source: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{source}: {key} must be a non-empty string")
    return value.strip()


def _evidence(data: Any, source: str) -> PolicyEvidence:
    if not isinstance(data, Mapping):
        raise ValueError(f"{source}: evidence must be an object")
    evidence = PolicyEvidence(
        kind=_required_string(data, "kind", source),
        reference=_required_string(data, "reference", source),
        summary=_required_string(data, "summary", source),
        recorded_at=_required_string(data, "recorded_at", source),
    )
    if evidence.kind not in {"operator_decision", "source_document"}:
        raise ValueError(f"{source}: unsupported evidence kind {evidence.kind!r}")
    return evidence


def validate_profile_data(data: Any, *, source: str = "club profile") -> ClubProfile:
    """Validate one decoded profile and return its immutable typed form."""

    if not isinstance(data, Mapping):
        raise ValueError(f"{source}: profile must be an object")
    if data.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise ValueError(f"{source}: schema_version must be {PROFILE_SCHEMA_VERSION}")
    club = _required_string(data, "club", source)

    venues: list[VenueProfile] = []
    for venue_index, raw_venue in enumerate(data.get("venues") or ()):
        venue_source = f"{source}: venues[{venue_index}]"
        if not isinstance(raw_venue, Mapping):
            raise ValueError(f"{venue_source} must be an object")
        resources: list[VenueResource] = []
        for resource_index, raw_resource in enumerate(raw_venue.get("resources") or ()):
            resource_source = f"{venue_source}.resources[{resource_index}]"
            if not isinstance(raw_resource, Mapping):
                raise ValueError(f"{resource_source} must be an object")
            labels = raw_resource.get("labels") or ()
            if not isinstance(labels, list) or not labels or not all(isinstance(label, str) and label for label in labels):
                raise ValueError(f"{resource_source}.labels must be a non-empty string list")
            resources.append(VenueResource(_required_string(raw_resource, "id", resource_source), tuple(labels)))
        resource_ids = {resource.id for resource in resources}
        if len(resource_ids) != len(resources):
            raise ValueError(f"{venue_source}: duplicate resource id")

        requirements: list[ResourceRequirement] = []
        for activity, raw_requirement in (raw_venue.get("tournament_requirements") or {}).items():
            requirement_source = f"{venue_source}.tournament_requirements.{activity}"
            if not isinstance(raw_requirement, Mapping):
                raise ValueError(f"{requirement_source} must be an object")
            required_ids = frozenset(raw_requirement.get("resource_ids") or ())
            if not required_ids or not required_ids <= resource_ids:
                raise ValueError(f"{requirement_source}.resource_ids must name configured resources")
            mode = _required_string(raw_requirement, "mode", requirement_source)
            if mode != "all_resources":
                raise ValueError(f"{requirement_source}: unsupported mode {mode!r}")
            requirements.append(
                ResourceRequirement(
                    activity=str(activity),
                    mode=mode,
                    resource_ids=required_ids,
                    evidence=_evidence(raw_requirement.get("evidence"), requirement_source),
                )
            )
        venues.append(
            VenueProfile(
                arena=_required_string(raw_venue, "arena", venue_source),
                resources=tuple(resources),
                requirements=tuple(requirements),
            )
        )

    policies: list[AvailabilityPolicy] = []
    for policy_index, raw_policy in enumerate(data.get("availability_policies") or ()):
        policy_source = f"{source}: availability_policies[{policy_index}]"
        if not isinstance(raw_policy, Mapping):
            raise ValueError(f"{policy_source} must be an object")
        authority = _required_string(raw_policy, "authority", policy_source)
        semantics = _required_string(raw_policy, "planning_semantics", policy_source)
        if authority != "club_controlled" or semantics != "available_for_internal_allocation":
            raise ValueError(f"{policy_source}: unsupported availability authority/semantics")
        windows: list[WeeklyAvailabilityWindow] = []
        for window_index, raw_window in enumerate(raw_policy.get("weekly_windows") or ()):
            window_source = f"{policy_source}.weekly_windows[{window_index}]"
            if not isinstance(raw_window, Mapping):
                raise ValueError(f"{window_source} must be an object")
            weekdays = frozenset(raw_window.get("weekdays") or ())
            start = _required_string(raw_window, "start", window_source)
            end = _required_string(raw_window, "end", window_source)
            if not weekdays or not all(isinstance(day, int) and 0 <= day <= 6 for day in weekdays):
                raise ValueError(f"{window_source}.weekdays must contain integers 0..6")
            if _hhmm_minutes(start) is None or _hhmm_minutes(end) is None or _hhmm_minutes(start) >= _hhmm_minutes(end):
                raise ValueError(f"{window_source}: invalid start/end")
            windows.append(WeeklyAvailabilityWindow(weekdays, start, end))
        if not windows:
            raise ValueError(f"{policy_source}: at least one weekly window is required")
        outside = _required_string(raw_policy, "outside_semantics", policy_source)
        if outside != "fixed_busy":
            raise ValueError(f"{policy_source}: unsupported outside_semantics {outside!r}")
        policies.append(
            AvailabilityPolicy(
                id=_required_string(raw_policy, "id", policy_source),
                authority=authority,
                planning_semantics=semantics,
                windows=tuple(windows),
                outside_semantics=outside,
                evidence=_evidence(raw_policy.get("evidence"), policy_source),
            )
        )

    if not venues and not policies:
        raise ValueError(f"{source}: profile must declare a venue or availability policy")
    return ClubProfile(
        club,
        tuple(venues),
        tuple(policies),
        source,
        calendar_booking_presence=_calendar_booking_presence(data.get("calendar_booking_presence"), source),
    )


def _calendar_booking_presence(data: Any, source: str) -> CalendarBookingPresence | None:
    if data is None:
        return None
    presence_source = f"{source}: calendar_booking_presence"
    if not isinstance(data, Mapping):
        raise ValueError(f"{presence_source} must be an object")
    publishes = data.get("publishes_tournament_bookings")
    if not isinstance(publishes, bool):
        raise ValueError(f"{presence_source}.publishes_tournament_bookings must be a boolean")
    return CalendarBookingPresence(
        publishes_tournament_bookings=publishes,
        evidence=_evidence(data.get("evidence"), presence_source),
    )


@lru_cache(maxsize=1)
def club_profiles() -> Mapping[str, ClubProfile]:
    """Load and validate all packaged profiles, keyed by canonical club name."""

    loaded: dict[str, ClubProfile] = {}
    for resource in sorted(files(PROFILE_PACKAGE).iterdir(), key=lambda item: item.name):
        if resource.name == "__init__.py" or not resource.name.endswith(".json"):
            continue
        source = f"tournament_scheduler/club_profiles/{resource.name}"
        with resource.open("r", encoding="utf-8") as handle:
            profile = validate_profile_data(json.load(handle), source=source)
        if profile.club in loaded:
            raise ValueError(f"{source}: duplicate profile for {profile.club}")
        loaded[profile.club] = profile
    return loaded


def get_club_profile(club: str) -> ClubProfile | None:
    """Return *club*'s profile, or ``None`` for generic clubs."""

    return club_profiles().get(club)


def profile_source_path(club: str) -> str | None:
    """Return the repository-relative data path a harness should lazy-load."""

    profile = get_club_profile(club)
    return profile.source_path if profile else None
