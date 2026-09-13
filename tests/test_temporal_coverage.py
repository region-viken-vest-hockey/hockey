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


def test_inactive_before_christmas_half_does_not_create_false_lead_gap():
    """issue #319: U7 has before_christmas=0 -- no lead-gap penalty from season start."""
    season_start = date(2026, 9, 1)
    season_end = date(2027, 3, 28)
    split_date = date(2027, 1, 1)
    dates_by_team = {"u7_team": [date(2027, 1, 10), date(2027, 1, 24), date(2027, 2, 7)]}
    team_meta = {"u7_team": ("Club", "U7")}
    targets = {"U7": {"before_christmas": 0, "after_christmas": 3}}

    coverages = season_temporal_coverage(
        season_start,
        season_end,
        dates_by_team,
        team_meta,
        participation_targets_by_age_group=targets,
        split_date=split_date,
    )
    coverage = coverages[0]
    assert coverage.lead_gap_days == (date(2027, 1, 10) - split_date).days
    assert coverage.lead_gap_days < 60


def test_inactive_after_christmas_half_does_not_create_false_finish_gap():
    """Symmetric case: after_christmas=0 -- no finish-gap penalty against season end."""
    season_start = date(2026, 9, 1)
    season_end = date(2027, 3, 28)
    split_date = date(2027, 1, 1)
    dates_by_team = {"spring_off_team": [date(2026, 9, 12), date(2026, 9, 26), date(2026, 11, 15)]}
    team_meta = {"spring_off_team": ("Club", "U8")}
    targets = {"U8": {"before_christmas": 3, "after_christmas": 0}}

    coverages = season_temporal_coverage(
        season_start,
        season_end,
        dates_by_team,
        team_meta,
        participation_targets_by_age_group=targets,
        split_date=split_date,
    )
    coverage = coverages[0]
    assert coverage.finish_gap_days == (split_date - date(2026, 11, 15)).days
    assert coverage.finish_gap_days < 60


def test_both_halves_active_matches_default_behavior():
    """A team active both halves must score identically whether or not the
    new targets/split_date kwargs are supplied."""
    season_start = date(2026, 9, 1)
    season_end = date(2027, 3, 28)
    split_date = date(2027, 1, 1)
    dates_by_team = {"both_halves": [date(2026, 10, 1), date(2027, 2, 1)]}
    team_meta = {"both_halves": ("Club", "U11")}
    targets = {"U11": {"before_christmas": 3, "after_christmas": 3}}

    baseline = season_temporal_coverage(season_start, season_end, dates_by_team, team_meta)
    with_targets = season_temporal_coverage(
        season_start,
        season_end,
        dates_by_team,
        team_meta,
        participation_targets_by_age_group=targets,
        split_date=split_date,
    )
    assert with_targets[0] == baseline[0]


def test_genuine_intra_active_window_gap_still_flagged():
    """A real >60-day gap inside the active window must still be an offender
    even after inactive-half handling is applied."""
    season_start = date(2026, 9, 1)
    season_end = date(2027, 3, 28)
    split_date = date(2027, 1, 1)
    dates_by_team = {"holmen_u10": [date(2027, 1, 10), date(2027, 3, 13)]}
    team_meta = {"holmen_u10": ("Holmen Hockey Blå", "U10")}
    targets = {"U10": {"before_christmas": 0, "after_christmas": 3}}

    coverages = season_temporal_coverage(
        season_start,
        season_end,
        dates_by_team,
        team_meta,
        participation_targets_by_age_group=targets,
        split_date=split_date,
    )
    offenders = temporal_offenders(coverages, threshold_days=60)
    assert offenders and offenders[0].team_key == "holmen_u10"
    assert offenders[0].max_intra_gap_days == (date(2027, 3, 13) - date(2027, 1, 10)).days


def test_default_call_without_targets_is_unchanged():
    """Omitting the new kwargs entirely must reproduce prior behavior exactly."""
    season_start = date(2026, 9, 1)
    season_end = date(2027, 3, 28)
    dates_by_team = {"team": [date(2026, 10, 1)]}
    team_meta = {"team": ("Club", "U7")}

    coverages = season_temporal_coverage(season_start, season_end, dates_by_team, team_meta)
    coverage = coverages[0]
    assert coverage.lead_gap_days == (date(2026, 10, 1) - season_start).days
    assert coverage.finish_gap_days == (season_end - date(2026, 10, 1)).days
