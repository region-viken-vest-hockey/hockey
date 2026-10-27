"""Canonical structured club-profile validation and auditability."""

from __future__ import annotations

import pytest

from tournament_scheduler.clubs.profiles import (
    get_club_profile,
    profile_source_path,
    validate_profile_data,
)


def test_skien_profile_owns_full_miniputt_resource_footprint():
    profile = get_club_profile("Skien")
    requirement = profile.venue_for_arena("Skien ishall").requirement_for("miniputt")

    assert requirement.mode == "all_resources"
    assert requirement.resource_ids == {"bane-1", "bane-2"}
    assert requirement.evidence.kind == "operator_decision"
    assert requirement.evidence.reference == "docs/adr/0007-skien-miniputt-needs-full-ice-rink.md"


def test_sandefjord_profile_distinguishes_internal_allocation_authority():
    profile = get_club_profile("Sandefjord Penguins")
    policy = profile.availability_policies[0]

    assert policy.authority == "club_controlled"
    assert policy.planning_semantics == "available_for_internal_allocation"
    assert policy.outside_semantics == "fixed_busy"
    assert policy.evidence.kind == "source_document"
    assert policy.evidence.reference.startswith("https://github.com/region-viken-vest-hockey/hockey/issues/")


def test_generic_club_has_no_profile_and_keeps_generic_behavior():
    assert get_club_profile("Jar") is None
    assert profile_source_path("Jar") is None


def test_profile_source_path_is_lazy_loadable_and_club_specific():
    assert profile_source_path("Skien") == "tournament_scheduler/club_profiles/skien.json"
    assert profile_source_path("Sandefjord Penguins").endswith("sandefjord-penguins.json")


def test_consequential_policy_without_evidence_is_rejected():
    data = {
        "schema_version": 1,
        "club": "Example",
        "venues": [
            {
                "arena": "Example hall",
                "resources": [{"id": "rink", "labels": ["Rink"]}],
                "tournament_requirements": {
                    "miniputt": {
                        "mode": "all_resources",
                        "resource_ids": ["rink"],
                    }
                },
            }
        ],
    }

    with pytest.raises(ValueError, match="evidence must be an object"):
        validate_profile_data(data, source="test-profile")


def test_sandefjord_profile_declares_calendar_does_not_publish_bookings():
    profile = get_club_profile("Sandefjord Penguins")

    presence = profile.calendar_booking_presence
    assert presence is not None
    assert presence.publishes_tournament_bookings is False
    assert presence.evidence.kind == "operator_decision"


def test_calendar_booking_presence_is_optional_and_defaults_to_none():
    assert get_club_profile("Skien").calendar_booking_presence is None


def test_calendar_booking_presence_requires_evidence():
    data = {
        "schema_version": 1,
        "club": "Example",
        "venues": [{"arena": "Example hall"}],
        "calendar_booking_presence": {"publishes_tournament_bookings": False},
    }

    with pytest.raises(ValueError, match="evidence"):
        validate_profile_data(data, source="example.json")
