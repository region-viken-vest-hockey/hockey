"""Repeat calendar confirmations keep or replace the stored association record."""

from __future__ import annotations

from tournament_scheduler.application.canonical_season.calendars.association_retention import (
    retain_repeat_association,
)
from tournament_scheduler.booking.calendar_bookings import CALENDAR_BOOKING_ASSOCIATIONS_KEY

_FP = "fp1"
_T = "t1"


def _record(**overrides):
    record = {
        "id": f"calendar_booking:{_FP}:{_T}",
        "status": "active",
        "event_fingerprint": _FP,
        "tournament_id": _T,
        "title": "Miniputt",
        "tournament_facts": {"arena": "A"},
        "tournament_interval": {"start": "10:00"},
        "source_revision": "rev1",
        "note": "first",
        "created_at": "2026-10-21T10:00:00+00:00",
        "created_by": "operator-a",
    }
    record.update(overrides)
    return record


def _candidate(**overrides):
    candidate = _record(source_revision="rev2", note="second", created_at="later", created_by="operator-b")
    candidate.update(overrides)
    return candidate


def _retain(existing, candidate):
    return retain_repeat_association(
        {CALENDAR_BOOKING_ASSOCIATIONS_KEY: existing},
        event_fingerprint=_FP,
        tournament_id=_T,
        association=candidate,
    )


def test_same_facts_keep_original_provenance_and_position():
    other = {"id": "other", "event_fingerprint": "fp2", "tournament_id": _T, "status": "active"}
    stored, records = _retain([other, _record()], _candidate())

    assert stored["note"] == "first"
    assert stored["created_by"] == "operator-a"
    assert records == [other, _record()]


def test_changed_interval_replaces_provenance():
    stored, records = _retain([_record()], _candidate(tournament_interval={"start": "11:00"}))

    assert stored["note"] == "second"
    assert records == [stored]


def test_changed_non_fact_field_replaces_provenance():
    stored, _ = _retain([_record()], _candidate(title="Renamed"))

    assert stored["note"] == "second"
    assert stored["title"] == "Renamed"


def test_inactive_previous_record_is_not_retained():
    # Matching is by event and tournament, as before, so the inactive record is replaced.
    stored, records = _retain([_record(status="superseded")], _candidate())

    assert stored["note"] == "second"
    assert records == [stored]


def test_no_previous_record_appends_candidate():
    stored, records = _retain([], _candidate())

    assert stored["note"] == "second"
    assert records == [stored]
