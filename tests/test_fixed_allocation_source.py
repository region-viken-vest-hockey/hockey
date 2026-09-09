"""Tests for tournament_scheduler.pipeline.fixed_allocation_source (issue #261)."""

from __future__ import annotations

from datetime import datetime

from tournament_scheduler.pipeline.fixed_allocation_source import run_fixed_allocation_source
from tournament_scheduler.sandefjord_allocation import SANDEFJORD_CLUB_NAME


class TestRunFixedAllocationSource:
    def test_returns_events_for_registered_club_by_exact_name(self):
        events = run_fixed_allocation_source(
            SANDEFJORD_CLUB_NAME, datetime(2025, 9, 1), datetime(2025, 9, 7)
        )
        assert events
        assert all(e.location == "Bugården ishall" for e in events)

    def test_resolves_source_alias_to_canonical_club(self):
        events = run_fixed_allocation_source(
            "Sandefjord", datetime(2025, 9, 1), datetime(2025, 9, 7)
        )
        assert events

    def test_returns_empty_for_unregistered_source(self):
        events = run_fixed_allocation_source(
            "Not A Real Club", datetime(2025, 9, 1), datetime(2025, 9, 7)
        )
        assert events == []
