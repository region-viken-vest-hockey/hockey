"""Unit tests for tournament_scheduler.temporal_coverage."""

from __future__ import annotations

from datetime import date

from tournament_scheduler.temporal_coverage import (
    season_temporal_coverage,
    team_temporal_coverage,
    temporal_offenders,
)


def test_finish_gap_dominates_when_team_clusters_early():
    """Kongsberg-U11-like case: six tournaments finish early, season runs long."""
    coverage = team_temporal_coverage(
        team_key="Kongsberg U11",
        club="Kongsberg",
        age_group="U11",
        season_start=date(2026, 9, 1),
        season_end=date(2027, 3, 28),
        tournament_dates=[
            date(2026, 10, 11),
            date(2026, 10, 18),
            date(2026, 11, 8),
            date(2027, 1, 10),
            date(2027, 1, 31),
            date(2027, 2, 6),
        ],
    )
    # The Nov-8 -> Jan-10 holiday-season silence (63 days) is actually the
    # single largest gap here, bigger than the finish gap (50 days) — the
    # old finish-only check would have missed that this team's true worst
    # stretch is mid-season, not at the tail.
    assert coverage.max_intra_gap_days == 63
    assert coverage.max_gap_days == coverage.max_intra_gap_days
    assert coverage.finish_gap_days == 50
    assert coverage.max_gap_days > 45


def test_lead_gap_is_captured_even_with_tight_intra_season_spacing():
    """A team starting late but finishing on time is still flagged via lead_gap."""
    coverage = team_temporal_coverage(
        team_key="LateStarter",
        club="X",
        age_group="U10",
        season_start=date(2026, 9, 1),
        season_end=date(2027, 3, 1),
        tournament_dates=[date(2027, 2, 1), date(2027, 2, 15), date(2027, 3, 1)],
    )
    assert coverage.lead_gap_days == (date(2027, 2, 1) - date(2026, 9, 1)).days
    assert coverage.max_gap_days == coverage.lead_gap_days


def test_intra_season_gap_is_captured_even_with_good_boundaries():
    """A team that starts and finishes on time but has a long mid-season silence."""
    coverage = team_temporal_coverage(
        team_key="MidGap",
        club="X",
        age_group="U10",
        season_start=date(2026, 9, 1),
        season_end=date(2027, 1, 1),
        tournament_dates=[date(2026, 9, 5), date(2026, 12, 20), date(2026, 12, 28)],
    )
    assert coverage.max_intra_gap_days == (date(2026, 12, 20) - date(2026, 9, 5)).days
    assert coverage.max_gap_days == coverage.max_intra_gap_days


def test_well_distributed_team_has_small_max_gap():
    coverage = team_temporal_coverage(
        team_key="Even",
        club="X",
        age_group="U10",
        season_start=date(2026, 9, 1),
        season_end=date(2027, 3, 1),
        tournament_dates=[
            date(2026, 10, 1),
            date(2026, 11, 15),
            date(2026, 12, 20),
            date(2027, 1, 20),
            date(2027, 2, 15),
        ],
    )
    assert coverage.max_gap_days <= 46


def test_temporal_offenders_returns_all_teams_over_threshold_not_just_worst():
    season_start = date(2026, 9, 1)
    season_end = date(2027, 3, 28)
    dates_by_team = {
        "worst": [date(2026, 10, 1), date(2026, 10, 8)],
        "also_bad": [date(2026, 10, 1), date(2027, 1, 1)],
        "fine": [
            date(2026, 10, 1),
            date(2026, 11, 20),
            date(2027, 1, 5),
            date(2027, 2, 20),
            date(2027, 3, 25),
        ],
    }
    team_meta = {key: ("Club", "U10") for key in dates_by_team}
    coverages = season_temporal_coverage(season_start, season_end, dates_by_team, team_meta)
    offenders = temporal_offenders(coverages, threshold_days=60)

    offender_keys = {c.team_key for c in offenders}
    assert offender_keys == {"worst", "also_bad"}
    # Worst offender (largest gap) sorts first.
    assert offenders[0].team_key == "worst"
    assert offenders[0].max_gap_days >= offenders[1].max_gap_days


def test_team_with_no_tournaments_gets_full_season_span_as_gap():
    coverage = team_temporal_coverage(
        team_key="NoGames",
        club="X",
        age_group="U10",
        season_start=date(2026, 9, 1),
        season_end=date(2027, 3, 1),
        tournament_dates=[],
    )
    assert coverage.max_gap_days == (date(2027, 3, 1) - date(2026, 9, 1)).days
