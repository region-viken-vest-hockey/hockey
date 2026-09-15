from tournament_scheduler.occupancy import required_ice_minutes


def test_required_ice_minutes_uses_configured_base_plus_per_round_buffer():
    assert required_ice_minutes(75, 6) == 105
    assert required_ice_minutes(175, 5) == 200
    assert required_ice_minutes(90, 3) == 105


def test_required_ice_minutes_rejects_missing_or_non_positive_inputs():
    assert required_ice_minutes(None, 4) == 0
    assert required_ice_minutes(0, 4) == 0
    assert required_ice_minutes(90, 0) == 0
