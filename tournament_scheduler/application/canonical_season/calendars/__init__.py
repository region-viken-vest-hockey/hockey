"""Compatibility exports for canonical-season calendar use cases."""

from .assessment import (
    _classify_club_calendar_bookings,
    booking_status_report,
    calendar_booking_assessment,
    calendar_booking_candidates,
    calendar_booking_findings,
)
from .associations import (
    _CALENDAR_EVENT_AGE_TOKEN_RE,
    _align_tournament_to_authoritative_interval,
    _align_tournament_to_calendar_event,
    _auto_associate_own_calendar_event,
    _calendar_event_age_tokens,
    confirm_calendar_booking,
    release_calendar_booking,
)
from .club_sources import club_booking_sources, set_club_booking_source
from .common import (
    _AUTO_REFRESH_RECONCILE_CLUBS,
    _CALENDAR_PROBLEM_KEYS,
    _approved_placement_locked,
    _calendar_problem_payload,
    _calendar_source_summaries,
    _event_interval,
    _manual_conflict_with_classification,
    _overlaps,
    _record_rejected_booking_evidence,
    _tournament_duration_minutes,
    _tournament_interval,
)
from .manual_assertions import (
    _manual_assertion_matches_existing,
    clear_manual_booking_assertion,
    set_manual_booking_assertion,
)
from .reconciliation import reconcile_calendar_bookings
from .refresh import refresh_calendars
from .source_policy import (
    _SOURCE_POLICY_SCHEMA_VERSION,
    _source_policy_changes,
    _source_policy_entry,
    _source_policy_from_evidence,
    _source_policy_signature,
    _source_policy_snapshot,
)

__all__ = [
    "_AUTO_REFRESH_RECONCILE_CLUBS",
    "_CALENDAR_EVENT_AGE_TOKEN_RE",
    "_CALENDAR_PROBLEM_KEYS",
    "_SOURCE_POLICY_SCHEMA_VERSION",
    "_align_tournament_to_authoritative_interval",
    "_align_tournament_to_calendar_event",
    "_approved_placement_locked",
    "_auto_associate_own_calendar_event",
    "_calendar_event_age_tokens",
    "_calendar_problem_payload",
    "_calendar_source_summaries",
    "_classify_club_calendar_bookings",
    "_event_interval",
    "_manual_assertion_matches_existing",
    "_manual_conflict_with_classification",
    "_overlaps",
    "_record_rejected_booking_evidence",
    "_source_policy_changes",
    "_source_policy_entry",
    "_source_policy_from_evidence",
    "_source_policy_signature",
    "_source_policy_snapshot",
    "_tournament_duration_minutes",
    "_tournament_interval",
    "booking_status_report",
    "calendar_booking_assessment",
    "calendar_booking_candidates",
    "calendar_booking_findings",
    "clear_manual_booking_assertion",
    "club_booking_sources",
    "confirm_calendar_booking",
    "reconcile_calendar_bookings",
    "refresh_calendars",
    "release_calendar_booking",
    "set_club_booking_source",
    "set_manual_booking_assertion",
]
