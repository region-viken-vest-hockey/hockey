"""issue #313: consolidated temporal-coverage metric in `build_fairness_gate`.

Regression coverage for ``team_temporal_coverage`` — the single soft
fairness metric that measures the largest gap anywhere along a team's own
``season_start -> first tournament -> ... -> last tournament -> season_end``
chain. Replaces the older, separate ``team_finish_gap`` (peer-age-group-
relative) and ``team_intra_season_gap`` (boundary-blind) metrics: a team
that clusters all its tournaments into a short early window is flagged via
its finish gap even when its existing tournaments look well spaced from
each other, and a team that starts late or has a mid-season hole is caught
by the same single measurement.
"""

from __future__ import annotations

from datetime import date, datetime

from tournament_scheduler.models import Game, Roster, SeasonPlan, Team, Tournament
from tournament_scheduler.season_planner import SeasonPlanner
from tournament_scheduler.testing.canonical_input import OfflineScheduler


def _tournament(day: date, teams: list[Team], arena: str = "Kongsberghallen", host_club: str = "Kongsberg") -> Tournament:
    games = [
        Game(home=teams[i], away=teams[i + 1], parallel_slot=0, round_number=1)
        for i in range(len(teams) - 1)
    ]
    return Tournament(date=day, arena=arena, age_group=teams[0].age_group, teams=teams, games=games, host_club=host_club)


def _build_gate(teams: list[Team], plan: SeasonPlan) -> dict:
    planner = SeasonPlanner(
        scheduler=OfflineScheduler([]),
        roster=Roster(teams=teams),
        club_arenas={"Kongsberg": "Kongsberghallen", "Tonsberg": "Tonsberghallen", "Filler": "Fillerhallen"},
        parallel_games_for_age_group={"U9": 3},
    )
    planner._compute_game_counts(plan.tournaments)
    return planner._build_fairness_gate(plan)


def _metric(gate: dict, key: str) -> dict:
    return next(m for m in gate["metrics"] if m["key"] == key)


def test_team_temporal_coverage_warns_for_team_clustering_early_despite_long_season():
    team_a = Team(club="Kongsberg", label="Kongsberg A", age_group="U9")
    team_b = Team(club="Tonsberg", label="Tonsberg A", age_group="U9")

    plan = SeasonPlan(
        tournaments=[
            # Team A: only two tournaments, both early -- finishes Nov 8
            # even though the season runs to Apr 30.
            _tournament(date(2026, 10, 4), [team_a]),
            _tournament(date(2026, 11, 8), [team_a]),
            # Team B: five tournaments evenly spread across the whole season.
            _tournament(date(2026, 10, 11), [team_b]),
            _tournament(date(2026, 11, 20), [team_b]),
            _tournament(date(2026, 12, 30), [team_b]),
            _tournament(date(2027, 2, 8), [team_b]),
            _tournament(date(2027, 3, 20), [team_b]),
        ],
        start_date=datetime(2026, 9, 1),
        end_date=datetime(2027, 4, 30),
    )
    gate = _build_gate([team_a, team_b], plan)

    coverage = _metric(gate, "team_temporal_coverage")
    assert coverage["status"] == "warn"
    assert coverage["value"] == 24.7  # Nov 8 -> Apr 30 is 173 days
    assert "Kongsberg A" in coverage["detail"]

    offender_labels = {o["team"] for o in coverage["offenders"]}
    assert offender_labels == {"Kongsberg A"}


def test_team_temporal_coverage_passes_when_team_is_well_distributed():
    team_b = Team(club="Tonsberg", label="Tonsberg A", age_group="U9")

    plan = SeasonPlan(
        tournaments=[
            _tournament(date(2026, 10, 11), [team_b]),
            _tournament(date(2026, 11, 20), [team_b]),
            _tournament(date(2026, 12, 30), [team_b]),
            _tournament(date(2027, 2, 8), [team_b]),
            _tournament(date(2027, 3, 20), [team_b]),
        ],
        start_date=datetime(2026, 9, 1),
        end_date=datetime(2027, 4, 30),
    )
    gate = _build_gate([team_b], plan)

    coverage = _metric(gate, "team_temporal_coverage")
    assert coverage["status"] == "pass"
    assert coverage["value"] == 5.9  # Mar 20 -> Apr 30 is 41 days
    assert coverage["offenders"] == []


def test_offenders_include_every_team_over_threshold_not_just_the_worst():
    team_a = Team(club="Kongsberg", label="Kongsberg A", age_group="U9")
    team_b = Team(club="Tonsberg", label="Tonsberg A", age_group="U9")
    team_c = Team(club="Filler", label="Filler A", age_group="U9")

    plan = SeasonPlan(
        tournaments=[
            _tournament(date(2026, 10, 4), [team_a]),
            _tournament(date(2026, 11, 8), [team_a]),
            _tournament(date(2026, 9, 15), [team_c]),
            _tournament(date(2026, 10, 20), [team_c]),
            _tournament(date(2026, 10, 11), [team_b]),
            _tournament(date(2026, 11, 20), [team_b]),
            _tournament(date(2026, 12, 30), [team_b]),
            _tournament(date(2027, 2, 8), [team_b]),
            _tournament(date(2027, 3, 20), [team_b]),
        ],
        start_date=datetime(2026, 9, 1),
        end_date=datetime(2027, 4, 30),
    )
    gate = _build_gate([team_a, team_b, team_c], plan)

    coverage = _metric(gate, "team_temporal_coverage")
    offenders = coverage["offenders"]
    offender_labels = [o["team"] for o in offenders]

    # Both Team A (finish gap 173 days) and Team C (finish gap 192 days)
    # exceed the threshold -- the well-distributed Team B does not. One
    # extreme offender (C) must not hide the other (A).
    assert set(offender_labels) == {"Kongsberg A", "Filler A"}
    assert "Tonsberg A" not in offender_labels
    # Worst offender (largest gap) sorts first.
    assert offender_labels[0] == "Filler A"
    assert offenders[0]["max_gap_days"] >= offenders[1]["max_gap_days"]


def test_temporal_coverage_is_measurement_not_policy_gate_authority():
    team_a = Team(club="Kongsberg", label="Kongsberg A", age_group="U9")
    team_b = Team(club="Tonsberg", label="Tonsberg A", age_group="U9")

    plan = SeasonPlan(
        tournaments=[
            _tournament(date(2026, 10, 4), [team_a]),
            _tournament(date(2026, 11, 8), [team_a]),
            _tournament(date(2026, 10, 11), [team_b]),
        ],
        start_date=datetime(2026, 9, 1),
        end_date=datetime(2027, 4, 30),
    )
    gate = _build_gate([team_a, team_b], plan)

    coverage = _metric(gate, "team_temporal_coverage")
    assert coverage["status"] == "warn"
    assert coverage["provenance"] == "measurement"

    policy_keys = {m["key"] for m in gate["policy_gate"]["metrics"]}
    measurement_keys = {m["key"] for m in gate["measurements"]}
    assert "team_temporal_coverage" not in policy_keys
    assert "team_temporal_coverage" in measurement_keys


def test_cancelled_tournaments_do_not_count_toward_temporal_coverage():
    team_a = Team(club="Kongsberg", label="Kongsberg A", age_group="U9")

    late_cancelled = _tournament(date(2027, 4, 25), [team_a])
    late_cancelled.cancelled = True

    plan = SeasonPlan(
        tournaments=[
            _tournament(date(2026, 10, 4), [team_a]),
            _tournament(date(2026, 11, 8), [team_a]),
            late_cancelled,
        ],
        start_date=datetime(2026, 9, 1),
        end_date=datetime(2027, 4, 30),
    )
    gate = _build_gate([team_a], plan)

    coverage = _metric(gate, "team_temporal_coverage")
    # If the cancelled Apr-25 tournament counted, the finish gap would
    # nearly vanish (Apr 30 - Apr 25 = 5 days). It must not count.
    assert coverage["value"] == 24.7  # Nov 8 -> Apr 30 is 173 days
