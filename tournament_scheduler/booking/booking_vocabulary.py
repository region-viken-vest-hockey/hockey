"""Booking evidence keys and status vocabulary; no workflow implementation."""

from __future__ import annotations

CALENDAR_BOOKING_ASSOCIATIONS_KEY = "calendar_booking_associations"
TOURNAMENT_BOOKING_EVIDENCE_KEY = "tournament_booking_evidence"
# A source-confirmed booking whose exact canonical change was refused by a hard
# operational check is preserved as durable evidence here, together with the
# specific conflict that refused it, so a genuine observed booking is never
# silently lost (ADR 0005: evidence acceptance and active placement acceptance
# are separate outcomes). The rejected record never moves, approves, locks or
# reconciles the tournament; it is an unresolved conflict for operator review.
REJECTED_BOOKING_EVIDENCE_KEY = "rejected_booking_evidence"
# Explicit operator/club assertions live in their own durable decisions key so a
# routine calendar reconcile/refresh that rewrites ``TOURNAMENT_BOOKING_EVIDENCE_KEY``
# can never erase or demote them.  A manual assertion is a statement about the
# booking whose authority is a person, not an event fingerprint.  The projection
# keeps that typed authority distinct from a calendar-derived observation.
MANUAL_BOOKING_ASSERTIONS_KEY = "manual_booking_assertions"
# A club-wide booking list (for example a spreadsheet or an email listing every
# home tournament) is a single source document, not a bag of independent
# per-tournament confirmations.  The source assertion is persisted separately so
# the original evidence, its version/fingerprint and the club scope survive even
# after every per-tournament interpretation is re-confirmed.  Each individual
# manual assertion may link to it, which keeps club provenance out of the
# assertion itself while preserving the per-ID authority.
CLUB_BOOKING_SOURCE_ASSERTIONS_KEY = "club_booking_source_assertions"
CLUB_BOOKING_SOURCE_SCOPE = "club_wide"
MANUAL_SOURCE_CLUB_CONFIRMATION = "manual_club_confirmation"
MANUAL_ASSERTION_ACTIVE = "active"
MANUAL_ASSERTION_SUPERSEDED = "superseded"
MANUAL_ASSERTION_REVOKED = "revoked"
REJECTED_BOOKING_UNRESOLVED = "unresolved"
REJECTED_BOOKING_RESOLVED = "resolved"
MANUAL_ASSERTION_SCOPES = ("tournament", "club_wide_interpretation")
BOOKING_AUTHORITY_MANUAL = MANUAL_SOURCE_CLUB_CONFIRMATION
BOOKING_AUTHORITY_MANUAL_INTERPRETATION = "manual_club_confirmation_interpretation"
BOOKING_AUTHORITY_CALENDAR = "calendar_event_association"
BOOKING_MANUALLY_BOOKED = "manually_booked"
BOOKING_MANUALLY_NOT_BOOKED = "manually_not_booked"
ACTIVE = "active"
STALE = "stale"
# A calendar association released because its tournament was canonically
# cancelled.  The raw event it pointed at is untouched and stays busy evidence.
CALENDAR_ASSOCIATION_SUPERSEDED = "superseded"
TOURNAMENT_CANCELLED_RELEASE_REASON = "tournament_cancelled"

# Read-only booking assessment vocabulary -------------------------------------
# The assessment is an evidence/report projection: it proposes plausible
# tournament<->calendar-event relations and keeps competing or unresolved cases
# visible.  It never persists an association or cancels a tournament.  Only a
# complete, coverage-proven, fresh trusted host-calendar window may contribute
# explicit absence evidence, and even then the result is a non-binding
# presumed-unscheduled review state rather than a canonical booking decision.
ASSESSMENT_SCHEMA_VERSION = 1
# Observed-source window needed around a tournament before absence can be claimed.
# It does not bound candidate search: a moved booking can sit anywhere in the season.
DEFAULT_ASSESSMENT_DATE_WINDOW_DAYS = 7

ASSESSMENT_ASSOCIATED = "associated"
ASSESSMENT_MANUALLY_ASSERTED = "manually_asserted"
ASSESSMENT_PROPOSED_UNCHANGED = "proposed_unchanged"
ASSESSMENT_PROPOSED_CHANGED_SLOT = "proposed_changed_slot"
ASSESSMENT_COMPETING_CANDIDATES = "competing_candidates"
ASSESSMENT_AMBIGUOUS = "ambiguous"
ASSESSMENT_UNMATCHED = "unmatched"
ASSESSMENT_NOT_CHECKABLE = "not_checkable"
ASSESSMENT_PRESUMED_UNSCHEDULED = "presumed_unscheduled"


_ASSESSMENT_UNRESOLVED = {
    ASSESSMENT_COMPETING_CANDIDATES,
    ASSESSMENT_AMBIGUOUS,
    ASSESSMENT_UNMATCHED,
    ASSESSMENT_PRESUMED_UNSCHEDULED,
    ASSESSMENT_NOT_CHECKABLE,
}

BOOKING_CONFIRMED_BOOKED = "confirmed_booked"
BOOKING_CONFIRMED_NOT_BOOKED = "confirmed_not_booked"
BOOKING_AMBIGUOUS = "ambiguous"
BOOKING_NOT_CHECKABLE = "not_checkable"
BOOKING_UNKNOWN = "unknown"
BOOKING_MANUAL_UNKNOWN = "manual_unknown"

# Unified operational booking state -------------------------------------------
# One operator-facing projection of the detailed booking evidence below. The
# detailed status/authority/follow-up fields stay available for audit and
# export details; this single value is what the season plan renders as the one
# top-level booking badge, so an operator can tell at a glance whether ice time
# is confirmed and protected or needs action.
OPERATIONAL_BOOKED = "booked"
OPERATIONAL_NOT_BOOKED = "not_booked"
OPERATIONAL_ACTION_REQUIRED = "action_required"
OPERATIONAL_CHANGED_SLOT_REVIEW = "changed_slot_review"
OPERATIONAL_PRESUMED_UNSCHEDULED = "presumed_unscheduled"
OPERATIONAL_UNKNOWN = "unknown"

# Manual-booking queue vocabulary --------------------------------------------
# A single operational ``action_required`` state covers different kinds of
# operator work. The queue distinguishes *why* a slot needs action -- an
# explicit host rejection, a confirmation invalidated by a slot change, a
# retained manual placement or a movable host interval -- so the operator sees
# the reason, the source evidence, the plausible calendar alternatives and the
# action that closes the item. These are derived projections of the same
# canonical evidence, never a second booking status or a persisted decision.
MANUAL_QUEUE_EXPLICIT_REJECTION = "explicit_rejection"
MANUAL_QUEUE_RECONFIRMATION_REQUIRED = "reconfirmation_required"
MANUAL_QUEUE_MANUAL_PLACEMENT = "manual_placement"
MANUAL_QUEUE_HOST_CONFIRMATION_REQUIRED = "host_confirmation_required"
MANUAL_QUEUE_ACTION_BOOK_OR_RECONFIRM = "book_or_reconfirm"
MANUAL_QUEUE_CLEARS_WHEN = "accepted_booking_assertion_or_valid_calendar_association"

_STATUS_BOOKED = "booked"
_STATUS_NOT_BOOKED = "not-booked"
MANUAL_BOOKING_STATUS_CHOICES = (_STATUS_BOOKED, _STATUS_NOT_BOOKED)
_MANUAL_CHOICE_TO_PROJECTION = {
    _STATUS_BOOKED: BOOKING_MANUALLY_BOOKED,
    _STATUS_NOT_BOOKED: BOOKING_MANUALLY_NOT_BOOKED,
}

_ATTENTION_BOOKING_STATUSES = {
    BOOKING_CONFIRMED_NOT_BOOKED,
    BOOKING_AMBIGUOUS,
    BOOKING_NOT_CHECKABLE,
    STALE,
    BOOKING_MANUALLY_NOT_BOOKED,
}

# A ``confirmed_not_booked`` record is authoritative only when an explicit
# source/operator rejected the booking. These reasons instead derive the
# conclusion from the absence of an overlapping event at the current canonical
# slot, which is an observation about that slot rather than proof about the
# tournament. The projection keeps them as ambiguity requiring review.
_ABSENCE_ONLY_NEGATIVE_REASONS = frozenset(
    {
        "no_overlapping_event_in_trustworthy_calendar",
        "no_covering_event_for_current_slot",
        "approved_placement_without_calendar_evidence",
    }
)

IDENTITY_DRIFT_ASSOCIATION_REASONS = frozenset(
    {"event_missing", "event_title_changed", "event_availability_changed"}
)
