"""Tests for the bounded responsibility-preserving placement repair."""

from datetime import date

from tournament_scheduler.responsibility_preserving_repair import (
    find_same_host_date_placement,
)


def test_finds_first_acceptable_date_with_a_verified_slot():
    searched = []

    def slot_search(candidate):
        searched.append(candidate)
        if candidate == date(2026, 10, 24):
            return ("Jar", "11:00", "13:00")
        return None

    result = find_same_host_date_placement(
        current_date=date(2026, 10, 10),
        candidate_dates=[date(2026, 10, 10), date(2026, 10, 17), date(2026, 10, 24)],
        slot_search=slot_search,
        is_acceptable_date=lambda d: True,
    )

    assert result.repaired
    assert result.chosen_date == date(2026, 10, 24)
    assert result.chosen_slot == ("Jar", "11:00", "13:00")
    # The current/failed date is never re-searched.
    assert date(2026, 10, 10) not in searched
    assert result.dates_checked == [date(2026, 10, 17), date(2026, 10, 24)]
    assert result.exhausted is False


def test_rejected_dates_are_not_recorded_as_searched():
    searched = []

    def slot_search(candidate):
        searched.append(candidate)
        return None

    result = find_same_host_date_placement(
        current_date=date(2026, 10, 10),
        candidate_dates=[date(2026, 10, 17), date(2026, 10, 24)],
        slot_search=slot_search,
        is_acceptable_date=lambda d: d != date(2026, 10, 17),
    )

    assert not result.repaired
    assert result.exhausted is True
    assert searched == [date(2026, 10, 24)]
    assert result.dates_checked == [date(2026, 10, 24)]


def test_empty_candidate_set_marks_bounded_search_exhausted():
    result = find_same_host_date_placement(
        current_date=date(2026, 10, 10),
        candidate_dates=[],
        slot_search=lambda d: ("Jar", "11:00", "13:00"),
        is_acceptable_date=lambda d: True,
    )

    assert not result.repaired
    assert result.exhausted is True
    assert result.dates_checked == []
