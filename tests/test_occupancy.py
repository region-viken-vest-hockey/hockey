from tournament_scheduler.occupancy import (
    effective_required_ice_minutes,
    governing_minimum_ice_time_minutes,
    minimum_playing_requirement_minutes,
    required_ice_minutes,
    tournament_end_time,
    tournament_required_ice_minutes,
)


class _FakeTournament:
    def __init__(self, age_group, games, start_time=None):
        self.age_group = age_group
        self.games = games
        self.start_time = start_time


class _FakeGame:
    def __init__(self, round_number):
        self.round_number = round_number


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


def test_effective_required_ice_minutes_reduces_by_missing_round_allowance_not_team_ratio():
    # JU12 (not a governing-minimum age group): configured for 8 teams / 5
    # rounds (110 = 5 * (15 + 5) + 10 headroom). Only 4 teams register -> 3
    # feasible rounds -> 2 rounds' worth of ice freed.
    occupancy = effective_required_ice_minutes(
        "JU12", 110, 15, 3, nominal_round_count=5
    )
    assert occupancy.nominal_format_minutes == 110
    assert occupancy.feasible_round_count == 3
    # 110 - 2 * (15 + 5) = 70, well above the 3-round floor of 60.
    assert occupancy.effective_requested_minutes == 70
    assert occupancy.booked_minutes == 70


def test_effective_required_ice_minutes_full_participation_keeps_configured_value():
    occupancy = effective_required_ice_minutes("JU12", 110, 15, 5, nominal_round_count=5)
    assert occupancy.effective_requested_minutes == 110


def test_effective_required_ice_minutes_never_drops_below_minimum_playing_floor():
    # Reduction would go to 90 - 3*20 = 30, but 2 rounds still need 2*20=40.
    occupancy = effective_required_ice_minutes("JU12", 90, 15, 2, nominal_round_count=5)
    assert occupancy.effective_requested_minutes == 40


def test_effective_required_ice_minutes_never_drops_below_governing_minimum():
    # U10 governing minimum is 120; a heavy reduction must not go below it.
    occupancy = effective_required_ice_minutes("U10", 150, 15, 1, nominal_round_count=5)
    assert occupancy.effective_requested_minutes == 120


def test_effective_required_ice_minutes_without_nominal_round_count_keeps_configured_value():
    occupancy = effective_required_ice_minutes("U14", 90, 15, 2, nominal_round_count=None)
    assert occupancy.effective_requested_minutes == 90


def test_effective_required_ice_minutes_never_shortens_a_confirmed_external_booking():
    occupancy = effective_required_ice_minutes(
        "JU12", 110, 15, 3, nominal_round_count=5, externally_booked_minutes=100
    )
    assert occupancy.effective_requested_minutes == 70
    assert occupancy.booked_minutes == 100


def test_tournament_required_ice_minutes_without_round_config_preserves_flat_behavior():
    tournament = _FakeTournament("JU12", [_FakeGame(1), _FakeGame(2), _FakeGame(3)])
    assert tournament_required_ice_minutes(tournament, {"JU12": 110}) == 110


def test_tournament_required_ice_minutes_adapts_with_round_config():
    tournament = _FakeTournament("JU12", [_FakeGame(1), _FakeGame(2), _FakeGame(3)])
    adapted = tournament_required_ice_minutes(
        tournament,
        {"JU12": 110},
        rounds_per_tournament={"JU12": 5},
        round_length_minutes={"JU12": 15},
    )
    assert adapted == 70


def test_tournament_end_time_uses_adapted_duration():
    tournament = _FakeTournament("JU12", [_FakeGame(1), _FakeGame(2), _FakeGame(3)], start_time="09:00")
    end = tournament_end_time(
        tournament,
        {"JU12": 110},
        rounds_per_tournament={"JU12": 5},
        round_length_minutes={"JU12": 15},
    )
    assert end == "10:10"


def test_participant_change_recomputes_duration_and_stales_booking_assertion():
    """Issue #473 end-to-end: a roster reduction shrinks the booked window,
    and any manual booking assertion recorded against the old (full) window
    is reported stale via the existing calendar-booking staleness check --
    no new staleness mechanism, just a fresh recomputed fact.
    """
    from tournament_scheduler.calendar_bookings import (
        manual_assertion_stale_reasons,
        tournament_occupancy_interval_facts,
    )

    problem = {
        "ice_time_minutes": {"JU12": 110},
        "round_length_minutes": {"JU12": 15},
        "rounds_per_tournament": {"JU12": 5},
    }
    full_tournament = {
        "age_group": "JU12",
        "date": "2026-11-01",
        "start_time": "09:00",
        "games": [{"round_number": r} for r in range(1, 6)],
    }
    full_interval = tournament_occupancy_interval_facts(full_tournament, problem)
    assert full_interval["duration_minutes"] == "110"

    assertion = {
        "asserted_interval": full_interval,
        "tournament_facts": {},
    }

    # A team withdraws: the tournament regenerates with only 3 feasible
    # rounds instead of 5.
    reduced_tournament = {
        "age_group": "JU12",
        "date": "2026-11-01",
        "start_time": "09:00",
        "games": [{"round_number": r} for r in range(1, 4)],
    }
    reduced_interval = tournament_occupancy_interval_facts(reduced_tournament, problem)
    assert reduced_interval["duration_minutes"] == "70"
    assert reduced_interval["nominal_format_minutes"] == "110"

    reasons = manual_assertion_stale_reasons(assertion, problem=problem, tournament=reduced_tournament)
    assert "tournament_duration_minutes_changed" in reasons
    assert "tournament_end_time_changed" in reasons
