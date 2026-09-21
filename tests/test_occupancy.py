from tournament_scheduler.occupancy import (
    governing_minimum_ice_time_minutes,
    minimum_playing_requirement_minutes,
    required_ice_minutes,
)


def test_required_ice_minutes_uses_configured_booking_window_without_extra_round_buffer():
    assert required_ice_minutes(120, 5) == 120
    assert required_ice_minutes(175, 5) == 175
    assert required_ice_minutes(90, 3) == 90


def test_minimum_playing_requirement_includes_per_round_changeover():
    assert minimum_playing_requirement_minutes(15, 5) == 100
    assert minimum_playing_requirement_minutes(15, 3) == 60


def test_governing_minimum_applies_to_nihf_series_round_age_groups():
    assert governing_minimum_ice_time_minutes("U10") == 120
    assert governing_minimum_ice_time_minutes("JU10") == 120
    assert governing_minimum_ice_time_minutes("U12") is None


def test_required_ice_minutes_rejects_missing_or_non_positive_inputs():
    assert required_ice_minutes(None, 4) == 0
    assert required_ice_minutes(0, 4) == 0
    assert required_ice_minutes(90, 0) == 0
