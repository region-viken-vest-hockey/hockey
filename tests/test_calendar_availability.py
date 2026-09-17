"""Tests for planner-neutral calendar availability semantics (issue #373)."""

from tournament_scheduler.calendar_availability import (
    CLASSIFICATION_SOURCE_CONFIGURED,
    CLASSIFICATION_SOURCE_UNCLASSIFIED,
    CalendarAvailability,
    CalendarEventClassificationRule,
    classify_club_event,
    classify_club_event_detailed,
    classify_event_name,
    host_confirmation_from_evidence,
    interval_availability,
    legacy_kind,
    unclassified_intervals,
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


def test_classification_provenance_distinguishes_configured_from_ambiguous():
    """Only an explicit per-club rule (or a declared club-controlled calendar)
    is a configured fact; an unconfigured title stays 'unclassified' so it is
    never silently flattened into an indistinguishable hard booking."""
    availability, reason, source = classify_club_event_detailed("Kongsberg", "Åpen ishall")
    assert (availability, source) == (CalendarAvailability.MOVABLE_BUSY, CLASSIFICATION_SOURCE_CONFIGURED)
    assert reason

    availability, _, source = classify_club_event_detailed("Jar", "Åpen ishall")
    assert (availability, source) == (CalendarAvailability.FIXED_BUSY, CLASSIFICATION_SOURCE_UNCLASSIFIED)

    availability, _, source = classify_club_event_detailed("Sandefjord Penguins", "fast istid")
    assert (availability, source) == (CalendarAvailability.FIXED_BUSY, CLASSIFICATION_SOURCE_CONFIGURED)


def test_unclassified_intervals_expose_only_ambiguous_events():
    intervals = {
        "Kongsberg": [
            {
                "date": "2026-11-21",
                "start": "10:00",
                "end": "14:00",
                "calendar_event": "Åpen ishall",
                "availability": "movable_busy",
            },
            {
                "date": "2026-11-21",
                "start": "16:00",
                "end": "18:00",
                "calendar_event": "Ukjent arrangement",
                "availability": "fixed_busy",
            },
        ],
        "Jar": [
            {
                "date": "2026-11-22",
                "start": "09:00",
                "end": "10:00",
                "calendar_event": "Trening",
                "availability": "fixed_busy",
            }
        ],
        "Sandefjord Penguins": [
            {
                "date": "2026-11-22",
                "start": "12:00",
                "end": "18:00",
                "calendar_event": "Sandefjord Penguins fast istid (opptatt utenom tildelt helgevindu)",
                "availability": "fixed_busy",
            }
        ],
    }
    # Kongsberg's configured "Åpen ishall" and Sandefjord's configured fixed
    # allocation are deterministic facts, not ambiguous ones; only the
    # genuinely unconfigured titles are exposed.
    assert unclassified_intervals(intervals) == [
        {
            "club": "Jar",
            "date": "2026-11-22",
            "start": "09:00",
            "end": "10:00",
            "calendar_event": "Trening",
            "availability": "fixed_busy",
        },
        {
            "club": "Kongsberg",
            "date": "2026-11-21",
            "start": "16:00",
            "end": "18:00",
            "calendar_event": "Ukjent arrangement",
            "availability": "fixed_busy",
        },
    ]
    assert unclassified_intervals(None) == []
