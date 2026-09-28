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
    MANUAL_QUEUE_ACTION_BOOK_OR_RECONFIRM,
    MANUAL_QUEUE_CLEARS_WHEN,
    MANUAL_QUEUE_EXPLICIT_REJECTION,
    MANUAL_QUEUE_HOST_CONFIRMATION_REQUIRED,
    MANUAL_QUEUE_MANUAL_PLACEMENT,
    MANUAL_QUEUE_RECONFIRMATION_REQUIRED,
    OPERATIONAL_ACTION_REQUIRED,
    OPERATIONAL_BOOKED,
    OPERATIONAL_NOT_BOOKED,
    STALE,
    TOURNAMENT_BOOKING_EVIDENCE_KEY,
    booking_status_report,
    new_booking_evidence_record,
    new_manual_assertion_record,
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

    def test_accepted_confirmation_wins_over_retained_provisional_metadata(self):
        # A provisional/manual reason or a movable-interval flag set at plan
        # build time can stay on a tournament after the operator confirms the
        # slot. That retained metadata is historical and must never demote an
        # accepted confirmation or drop its operational lock.
        assert (
            operational_booking_state(
                status=BOOKING_MANUALLY_BOOKED,
                manual_booking_reason="kalender utilgjengelig",
            )
            == OPERATIONAL_BOOKED
        )
        assert (
            operational_booking_state(
                status=BOOKING_CONFIRMED_BOOKED,
                requires_host_confirmation=True,
            )
            == OPERATIONAL_BOOKED
        )

    def test_unestablished_slot_without_confirmation_is_action(self):
        # Without an accepted confirmation the provisional placeholder and the
        # movable host interval are manual work, not booked ice.
        assert (
            operational_booking_state(
                status=BOOKING_UNKNOWN,
                manual_booking_reason="kalender utilgjengelig",
            )
            == OPERATIONAL_ACTION_REQUIRED
        )
        assert (
            operational_booking_state(
                status=BOOKING_AMBIGUOUS,
                requires_host_confirmation=True,
            )
            == OPERATIONAL_ACTION_REQUIRED
        )
        assert (
            operational_booking_state(status=BOOKING_UNKNOWN, manual_booking_reason=" ")
            == OPERATIONAL_NOT_BOOKED
        )


def _tournament(tournament_id, **overrides):
    tournament = {
        "id": tournament_id,
        "date": "2026-09-12",
        "arena": "Arena A",
        "age_group": "U10",
        "host_club": "A",
        "start_time": "10:00",
        "games": [],
    }
    tournament.update(overrides)
    return tournament


def _problem(events=None):
    return {
        "ice_time_minutes": {"U10": 120},
        "club_calendar_status": {"A": "known"},
        "club_busy_intervals": {"A": list(events or [])},
    }


def _manual_assertion(tournament, problem, *, status="booked", **overrides):
    kwargs = {
        "tournament": tournament,
        "booking_status": status,
        "problem": problem,
        "actor": "booker",
        "note": "host emailed the club",
        "reference": "email:1",
        "source_scope": "tournament",
        "stated_interval": None,
        "asserted_at": "2026-09-01T00:00:00+00:00",
        "source_revision": "rev-1",
    }
    kwargs.update(overrides)
    return new_manual_assertion_record(**kwargs)


def _report(plan, decisions, problem):
    return booking_status_report(plan=plan, decisions=decisions, problem=problem)


class TestManualBookingQueue:
    """The action-required tournaments become structured manual work items."""

    def test_explicit_rejection_carries_reason_source_owner_and_resolution(self):
        tournament = _tournament("t1")
        problem = _problem()
        assertion = _manual_assertion(
            tournament,
            problem,
            status="not-booked",
            note="club cannot host the assigned weekend",
            reference="email:reject-host",
        )
        report = _report(
            {"tournaments": [tournament]},
            {"manual_booking_assertions": [assertion]},
            problem,
        )
        row = report["tournaments"][0]
        assert row["operational_state"] == OPERATIONAL_ACTION_REQUIRED
        work = row["manual_work"]
        assert work["reason_code"] == MANUAL_QUEUE_EXPLICIT_REJECTION
        assert work["owner"] == "A"
        assert work["action"] == MANUAL_QUEUE_ACTION_BOOK_OR_RECONFIRM
        assert work["source"]["reference"] == "email:reject-host"
        assert work["source"]["note"] == "club cannot host the assigned weekend"
        assert work["source"]["asserted_by"] == "booker"
        assert work["source"]["assertion_id"] == assertion["id"]
        assert work["resolution"]["status"] == "open"
        assert work["resolution"]["clears_when"] == MANUAL_QUEUE_CLEARS_WHEN
        assert report["manual_booking_queue"] == [work]

    def test_missing_calendar_evidence_is_not_manual_work(self):
        """Absence of a calendar event is an observation, never host rejection."""

        tournament = _tournament("t1")
        report = _report({"tournaments": [tournament]}, {}, _problem())
        row = report["tournaments"][0]
        assert row["status"] == BOOKING_UNKNOWN
        # Missing/ambiguous evidence is never promoted to the manual queue.
        assert row["operational_state"] != OPERATIONAL_ACTION_REQUIRED
        assert "manual_work" not in row
        assert report["manual_booking_queue"] == []

    def test_manual_placement_and_host_confirmation_have_distinct_reasons(self):
        placement = _tournament("placement", manual_booking_reason="kalender utilgjengelig")
        movable = _tournament("movable", requires_host_confirmation=True)
        report = _report({"tournaments": [placement, movable]}, {}, _problem())
        by_id = {row["tournament_id"]: row for row in report["tournaments"]}
        assert by_id["placement"]["manual_work"]["reason_code"] == MANUAL_QUEUE_MANUAL_PLACEMENT
        assert (
            by_id["movable"]["manual_work"]["reason_code"]
            == MANUAL_QUEUE_HOST_CONFIRMATION_REQUIRED
        )

    def test_slot_change_reconfirmation_keeps_traceable_assertion(self):
        original = _tournament("t1")
        problem = _problem()
        assertion = _manual_assertion(original, problem)
        moved = _tournament("t1", date="2026-09-19")
        report = _report(
            {"tournaments": [moved]},
            {"manual_booking_assertions": [assertion]},
            problem,
        )
        work = report["tournaments"][0]["manual_work"]
        assert work["reason_code"] == MANUAL_QUEUE_RECONFIRMATION_REQUIRED
        assert work["source"]["assertion_id"] == assertion["id"]
        assert "tournament_date_changed" in work["stale_reasons"]

    def test_calendar_rejection_carries_calendar_source(self):
        tournament = _tournament("t1")
        problem = _problem()
        record = new_booking_evidence_record(
            tournament=tournament,
            status=BOOKING_CONFIRMED_NOT_BOOKED,
            problem=problem,
            actor="booker",
            note="host confirmed the slot is unavailable",
            checked_at="2026-09-02T00:00:00+00:00",
            source_revision="rev-2",
            reason="explicit_host_rejection",
        )
        report = _report(
            {"tournaments": [tournament]},
            {TOURNAMENT_BOOKING_EVIDENCE_KEY: [record]},
            problem,
        )
        work = report["tournaments"][0]["manual_work"]
        assert work["reason_code"] == MANUAL_QUEUE_EXPLICIT_REJECTION
        assert work["source"]["reason"] == "explicit_host_rejection"
        assert work["source"]["checked_by"] == "booker"
        assert work["source"]["note"] == "host confirmed the slot is unavailable"

    def test_proposed_alternatives_only_include_actionable_candidates(self):
        tournament = _tournament("t1")
        problem = _problem(
            events=[
                {
                    "date": "2026-09-12",
                    "start": "13:00",
                    "end": "15:00",
                    "availability": "fixed_busy",
                    "calendar_event": "Miniputt U10",
                }
            ]
        )
        assertion = _manual_assertion(
            tournament, problem, status="not-booked", reference="email:reject"
        )
        report = _report(
            {"tournaments": [tournament]},
            {"manual_booking_assertions": [assertion]},
            problem,
        )
        alternatives = report["tournaments"][0]["manual_work"]["proposed_alternatives"]
        assert [item["start"] for item in alternatives] == ["13:00"]
        assert alternatives[0]["relation"] == "same_date_time_shift"

    def test_untrusted_source_candidate_is_not_a_proposed_alternative(self):
        tournament = _tournament("t1")
        problem = _problem()
        problem["club_calendar_status"] = {"A": "untrusted"}
        problem["club_busy_intervals"] = {
            "A": [
                {
                    "date": "2026-09-12",
                    "start": "13:00",
                    "end": "15:00",
                    "availability": "fixed_busy",
                    "calendar_event": "Miniputt U10",
                }
            ]
        }
        assertion = _manual_assertion(
            tournament, problem, status="not-booked", reference="email:reject"
        )
        report = _report(
            {"tournaments": [tournament]},
            {"manual_booking_assertions": [assertion]},
            problem,
        )
        assert report["tournaments"][0]["manual_work"]["proposed_alternatives"] == []


class TestBookingConflictSignal:
    """A typed contradiction flag, not a re-parsed follow-up string.

    Publication scope holds a placement only when its own evidence actively
    contradicts it, so the booking projection exposes that contradiction
    explicitly instead of leaving each consumer to match reason strings.
    """

    def test_manual_confirmation_contradicted_by_calendar_sets_conflict(self):
        tournament = _tournament("t1")
        problem = _problem()
        assertion = _manual_assertion(tournament, problem)
        record = new_booking_evidence_record(
            tournament=tournament,
            status=BOOKING_CONFIRMED_NOT_BOOKED,
            problem=problem,
            actor="calendar",
            note="the slot is taken by a non-RVV activity",
            checked_at="2026-09-02T00:00:00+00:00",
            source_revision="rev-2",
            reason="explicit_host_rejection",
        )
        report = _report(
            {"tournaments": [tournament]},
            {
                "manual_booking_assertions": [assertion],
                TOURNAMENT_BOOKING_EVIDENCE_KEY: [record],
            },
            problem,
        )
        row = report["tournaments"][0]
        assert row["status"] == BOOKING_MANUALLY_BOOKED
        assert row["conflict"] is True
        assert report["counts"]["conflicts"] == 1

    def test_row_without_contradiction_has_no_conflict(self):
        tournament = _tournament("t1")
        report = _report({"tournaments": [tournament]}, {}, _problem())
        assert report["tournaments"][0]["conflict"] is False
