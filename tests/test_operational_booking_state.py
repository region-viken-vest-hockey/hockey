"""The unified operational booking state projection.

Season plans used to stack several booking-ish badges (detailed status,
calendar follow-ups, manual queue, approval) on one tournament. The canonical
``calendar_bookings.operational_booking_state`` collapses the detailed evidence
into one operator-facing state; these tests pin that mapping so the top-level
badge can never silently drift from the booking report.
"""

from __future__ import annotations

from tournament_scheduler.calendar_bookings import (
    BOOKING_AMBIGUOUS,
    BOOKING_CONFIRMED_BOOKED,
    BOOKING_CONFIRMED_NOT_BOOKED,
    BOOKING_MANUALLY_BOOKED,
    BOOKING_MANUALLY_NOT_BOOKED,
    BOOKING_MANUAL_UNKNOWN,
    BOOKING_NOT_CHECKABLE,
    BOOKING_UNKNOWN,
    OPERATIONAL_ACTION_REQUIRED,
    OPERATIONAL_BOOKED,
    OPERATIONAL_NOT_BOOKED,
    STALE,
    operational_booking_state,
)


class TestOperationalBookingState:
    def test_accepted_confirmation_is_booked_and_locked(self):
        assert operational_booking_state(status=BOOKING_CONFIRMED_BOOKED) == OPERATIONAL_BOOKED
        assert operational_booking_state(status=BOOKING_MANUALLY_BOOKED) == OPERATIONAL_BOOKED

    def test_explicit_rejection_and_stale_confirmation_require_action(self):
        assert operational_booking_state(status=BOOKING_CONFIRMED_NOT_BOOKED) == OPERATIONAL_ACTION_REQUIRED
        assert operational_booking_state(status=BOOKING_MANUALLY_NOT_BOOKED) == OPERATIONAL_ACTION_REQUIRED
        assert operational_booking_state(status=STALE) == OPERATIONAL_ACTION_REQUIRED

    def test_missing_or_unresolved_evidence_is_merely_not_booked(self):
        # Absence of calendar evidence and unresolved ambiguity must not be
        # promoted to the manual queue as if the host had rejected the slot.
        assert operational_booking_state(status=BOOKING_UNKNOWN) == OPERATIONAL_NOT_BOOKED
        assert operational_booking_state(status=BOOKING_AMBIGUOUS) == OPERATIONAL_NOT_BOOKED
        assert operational_booking_state(status=BOOKING_NOT_CHECKABLE) == OPERATIONAL_NOT_BOOKED
        assert operational_booking_state(status=BOOKING_MANUAL_UNKNOWN) == OPERATIONAL_NOT_BOOKED

    def test_unestablished_slot_is_action_regardless_of_detailed_status(self):
        # A provisional/manual placement or a movable host interval is not
        # established ice even if a stale detailed status once said "booked".
        assert (
            operational_booking_state(
                status=BOOKING_CONFIRMED_BOOKED,
                manual_booking_reason="kalender utilgjengelig",
            )
            == OPERATIONAL_ACTION_REQUIRED
        )
        assert (
            operational_booking_state(
                status=BOOKING_CONFIRMED_BOOKED,
                requires_host_confirmation=True,
            )
            == OPERATIONAL_ACTION_REQUIRED
        )
        assert (
            operational_booking_state(status=BOOKING_UNKNOWN, manual_booking_reason=" ")
            == OPERATIONAL_NOT_BOOKED
        )
