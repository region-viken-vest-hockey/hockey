"""Tests for planner-neutral calendar availability semantics (issue #373)."""

from tournament_scheduler.calendar_availability import (
    CalendarAvailability,
    CalendarEventClassificationRule,
    classify_club_event,
    classify_event_name,
    host_confirmation_from_evidence,
    interval_availability,
    legacy_kind,
)


def test_enum_covers_the_four_required_classes():
    assert {item.value for item in CalendarAvailability} == {
        "fixed_busy",
        "movable_busy",
        "free",
        "unknown",
    }


def test_event_rule_matching_is_normalized_substring():
    rules = (
        CalendarEventClassificationRule(
            pattern="ÅPEN  ISHALL",
            classification=CalendarAvailability.MOVABLE_BUSY,
            reason="host-controlled open ice",
        ),
    )
    availability, reason = classify_event_name("Trening - åpen ishall (hall 1)", rules)
    assert availability == CalendarAvailability.MOVABLE_BUSY
    assert reason == "host-controlled open ice"


def test_unmatched_event_keeps_the_conservative_default():
    rules = (
        CalendarEventClassificationRule(
            pattern="åpen ishall",
            classification=CalendarAvailability.MOVABLE_BUSY,
            reason="open ice",
        ),
    )
    availability, reason = classify_event_name("Ekstern kamp", rules)
    assert availability == CalendarAvailability.FIXED_BUSY
    assert reason == ""


def test_interval_availability_prefers_explicit_field_over_legacy_kind():
    assert interval_availability({"availability": "movable_busy", "kind": "external"}) == (
        CalendarAvailability.MOVABLE_BUSY
    )
    assert interval_availability({"kind": "club_controlled"}) == CalendarAvailability.MOVABLE_BUSY
    assert interval_availability({"kind": "external"}) == CalendarAvailability.FIXED_BUSY
    # An older/hand-built entry with no tag defaults to the safe fixed_busy.
    assert interval_availability({"start": "10:00"}) == CalendarAvailability.FIXED_BUSY


def test_legacy_kind_mapping_is_stable():
    assert legacy_kind(CalendarAvailability.FIXED_BUSY) == "external"
    assert legacy_kind(CalendarAvailability.MOVABLE_BUSY) == "club_controlled"


def test_kongsberg_open_ice_is_club_scoped_not_global():
    availability, reason = classify_club_event("Kongsberg", "Åpen ishall")
    assert availability == CalendarAvailability.MOVABLE_BUSY
    assert "open ice" in reason
    # The identical phrase must not be movable for a club without the rule.
    other_availability, _ = classify_club_event("Jar", "Åpen ishall")
    assert other_availability == CalendarAvailability.FIXED_BUSY


def test_host_confirmation_from_evidence_formats_event_and_reason():
    requires, reason = host_confirmation_from_evidence(
        {
            "requires_host_confirmation": True,
            "calendar_event": "Åpen ishall",
            "reason": "host-controlled open ice",
        }
    )
    assert requires is True
    assert reason == "Åpen ishall — host-controlled open ice"
    assert host_confirmation_from_evidence(None) == (False, None)
    assert host_confirmation_from_evidence({"availability": "movable_busy"}) == (False, None)
