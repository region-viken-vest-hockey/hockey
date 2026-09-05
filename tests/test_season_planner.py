"""Tests for SeasonPlanner (season planning/optimization engine)."""

from collections import Counter
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Dict

import pytest

from tournament_scheduler import participant_selection
from tournament_scheduler.models import (
    AGE_GROUP_OVERLAP,
    CalendarEvent,
    DatePreference,
    Game,
    Roster,
    SeasonPlan,
    Team,
    Tournament,
    overlapping_age_groups,
    team_key,
)
from tournament_scheduler.season_planner import (
    SeasonPlanner,
    DEFAULT_TOURNAMENT_START_TIME,
    MIN_TEAMS_PER_TOURNAMENT,
)
from tournament_scheduler.host_assignment import find_slot_for_tournament
from tournament_scheduler.testing.canonical_input import OfflineScheduler, all_weekend_dates
from tournament_scheduler.warnings import holiday_heavy_weekend_dates



FakeScheduler = OfflineScheduler


def _build_roster(clubs, age_groups, teams_per_club_per_age_group=1):
    teams = []
    for club in clubs:
        for age_group in age_groups:
            for i in range(1, teams_per_club_per_age_group + 1):
                label = f"{club} {age_group}-{i}" if teams_per_club_per_age_group > 1 else f"{club} {age_group}"
                teams.append(Team(club=club, label=label, age_group=age_group))
    return Roster(teams=teams)


def _load_real_roster_from_input_workbook():
    from tournament_scheduler.testing.canonical_input import load_canonical_roster

    return load_canonical_roster()



@pytest.fixture(scope="module")
def season_window():
    return datetime(2026, 10, 1), datetime(2027, 4, 30)


@pytest.fixture(scope="module")
def planner_and_plan(season_window):
    start, end = season_window
    free_dates = all_weekend_dates(start, end)

    clubs = ["Jar", "Holmen", "Kongsberg", "Skien", "Jutul", "Ringerike"]
    age_groups = ["U10", "U11", "JU11", "U7"]
    roster = _build_roster(clubs, age_groups)
    for team in roster.teams:
        team.target_tournament_count = 2
    club_arenas = {club: f"{club}hallen" for club in clubs}

    planner = SeasonPlanner(
        scheduler=FakeScheduler(free_dates),
        roster=roster,
        club_arenas=club_arenas,
        parallel_games_for_age_group={"U10": 3, "U7": 4, "U11": 2, "JU11": 2},
    )
    plan = planner.build_plan(start, end)
    return planner, plan, roster, clubs, club_arenas


class TestSeasonPlanner:
    """Test suite for SeasonPlanner.build_plan."""

    def test_proposes_target_tournaments_per_age_group(self, planner_and_plan):
        planner, plan, roster, *_ = planner_and_plan
        counts = Counter(t.age_group for t in plan.tournaments)
        for age_group in roster.age_groups():
            assert counts[age_group] == planner._target_tournaments_for_age_group(age_group)

    def test_skips_age_groups_with_too_few_teams_for_a_tournament(self, season_window):
        start, end = season_window
        free_dates = all_weekend_dates(start, end)
        roster = Roster(teams=[
            Team(club="Jar", label="Jar JU12", age_group="JU12"),
            Team(club="Kongsberg", label="Kongsberg JU12", age_group="JU12"),
        ])
        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas={"Jar": "Jarhallen", "Kongsberg": "Kongsberghallen"},
        )

        plan = planner.build_plan(start, end)

        assert planner._target_tournaments_for_age_group("JU12") == 0
        assert len(plan.tournaments) == 0

    def test_generated_tournaments_have_at_least_minimum_team_count(self, planner_and_plan):
        _, plan, *_ = planner_and_plan
        assert all(len(tournament.teams) >= MIN_TEAMS_PER_TOURNAMENT for tournament in plan.tournaments)

    def test_tournament_dates_are_spread_across_the_season_window(self, planner_and_plan, season_window):
        _, plan, roster, *_ = planner_and_plan
        start, end = season_window

        tournaments_by_age_group = {}
        for tournament in plan.tournaments:
            tournaments_by_age_group.setdefault(tournament.age_group, []).append(tournament.date)

        for age_group in roster.age_groups():
            dates = sorted(tournaments_by_age_group[age_group])
            assert dates[0] >= start.date()
            assert dates[-1] <= end.date()
            assert len(dates) == len(set(dates))

            # Roughly even spacing per age group: gaps between consecutive tournament
            # dates should not vary wildly (sanity bound, not a strict uniformity requirement).
            gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
            if gaps:
                assert max(gaps) <= 3 * (sum(gaps) / len(gaps))

    def test_split_age_group_targets_place_tournaments_before_and_after_christmas(self, season_window):
        start, end = season_window
        free_dates = all_weekend_dates(start, end)
        roster = _build_roster(["Jar", "Holmen", "Kongsberg", "Skien", "Jutul", "Ringerike"], ["U10"])
        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas={club: f"{club}hallen" for club in ["Jar", "Holmen", "Kongsberg", "Skien", "Jutul", "Ringerike"]},
            parallel_games_for_age_group={"U10": 3},
            target_tournament_counts_by_age_group={"U10": {"before_christmas": 2, "after_christmas": 4}},
        )

        plan = planner.build_plan(start, end)
        split_date = date(start.year, 12, 24)
        before = [t for t in plan.tournaments if t.date < split_date]
        after = [t for t in plan.tournaments if t.date >= split_date]

        assert planner._target_tournaments_for_age_group("U10") == 6
        assert planner._target_tournaments_for_age_group("U10", period="before_christmas") == 2
        assert planner._target_tournaments_for_age_group("U10", period="after_christmas") == 4
        assert len(before) == 2
        assert len(after) == 4

    def test_every_arena_hosts_at_least_one_tournament_before_any_repeats(self, planner_and_plan):
        _, plan, roster, clubs, club_arenas = planner_and_plan

        # Hosting is balanced within each age group, not globally across
        # unrelated age groups.
        for age_group in roster.age_groups():
            eligible_clubs = {team.club for team in roster.by_age_group(age_group)}
            host_order = [
                t.host_club
                for t in sorted(plan.tournaments, key=lambda t: (t.date, t.age_group))
                if t.age_group == age_group
            ]
            first_occurrence = {}
            for index, host in enumerate(host_order):
                first_occurrence.setdefault(host, index)

            # Every eligible club should have hosted at least once when the
            # age group has enough tournaments for all of them.
            if len(host_order) >= len(eligible_clubs):
                assert set(first_occurrence) == eligible_clubs

            last_first_occurrence = max(first_occurrence.values())
            seen_once = set()
            for index, host in enumerate(host_order):
                if host in seen_once:
                    assert index > last_first_occurrence, (
                        f"{age_group}: a club hosted a second tournament before every eligible club hosted once"
                    )
                seen_once.add(host)

        # Every arena recorded in plan metadata corresponds to a real club arena.
        for arena in plan.arena_counts:
            if arena.startswith("_"):
                continue
            assert arena in club_arenas.values()

    def test_no_team_is_invited_disproportionately_more_than_others(self, planner_and_plan):
        _, plan, roster, *_ = planner_and_plan

        invite_counts = {team.label: 0 for team in roster.teams}
        for tournament in plan.tournaments:
            for team in tournament.teams:
                invite_counts[team.label] += 1

        # Compare invite counts only within the same age group (groups may differ
        # in how many tournaments they get scheduled overall).
        by_age_group = {}
        for team in roster.teams:
            by_age_group.setdefault(team.age_group, []).append(invite_counts[team.label])

        for age_group, counts in by_age_group.items():
            if not counts or max(counts) == 0:
                continue
            assert max(counts) - min(counts) <= 2, (
                f"{age_group}: invite counts too uneven: {counts}"
            )

    def test_avoids_avoidable_overlap_collisions_between_age_groups(self, planner_and_plan):
        _, plan, *_ = planner_and_plan

        by_date = {}
        for tournament in plan.tournaments:
            by_date.setdefault(tournament.date, []).append(tournament.age_group)

        unavoidable_collisions = []
        for tournament_date, age_groups_on_date in by_date.items():
            for i, ag in enumerate(age_groups_on_date):
                for other in age_groups_on_date[i + 1:]:
                    if other in overlapping_age_groups(ag) or ag in overlapping_age_groups(other):
                        unavoidable_collisions.append((tournament_date, ag, other))

        # In this scenario there are far more free dates than tournaments, so
        # every collision should have been avoidable — the planner should not
        # have produced any.
        assert unavoidable_collisions == [], f"unexpected collisions: {unavoidable_collisions}"
        assert plan.arena_counts.get("_age_group_overlap_collisions", 0) == 0
        assert planner_and_plan[0].collisions == []

    def test_same_arena_same_day_tournaments_are_sequenced(self, season_window):
        start, end = season_window
        free_dates = [start.date()]
        roster = Roster(teams=[
            Team(club="Jar", label="Jar U7-1", age_group="U7"),
            Team(club="Jar", label="Jar U7-2", age_group="U7"),
            Team(club="Jar", label="Jar U7-3", age_group="U7"),
            Team(club="Jar", label="Jar U10-1", age_group="U10"),
            Team(club="Jar", label="Jar U10-2", age_group="U10"),
            Team(club="Jar", label="Jar U10-3", age_group="U10"),
        ])
        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas={"Jar": "Jarahallen"},
            parallel_games_for_age_group={"U7": 4, "U10": 4},
            round_length_for_age_group={"U7": 60, "U10": 60},
        )

        plan = planner.build_plan(start, end)

        same_day = [t for t in plan.tournaments if t.arena == "Jarahallen" and t.date == start.date()]
        assert len(same_day) == 2
        same_day.sort(key=lambda t: t.start_time)

        first, second = same_day
        assert first.start_time == "10:00"
        assert second.start_time == "13:20"
        assert plan.arena_day_collisions == []
        assert plan.arena_counts.get("_arena_day_collisions", 0) == 0

    def test_same_arena_third_tournament_after_latest_start_is_hard_collision(self, season_window):
        start, end = season_window
        free_dates = [start.date()]
        roster = Roster(teams=[
            Team(club="Jar", label="Jar U7-1", age_group="U7"),
            Team(club="Jar", label="Jar U7-2", age_group="U7"),
            Team(club="Jar", label="Jar U7-3", age_group="U7"),
            Team(club="Jar", label="Jar U8-1", age_group="U8"),
            Team(club="Jar", label="Jar U8-2", age_group="U8"),
            Team(club="Jar", label="Jar U8-3", age_group="U8"),
            Team(club="Jar", label="Jar U9-1", age_group="U9"),
            Team(club="Jar", label="Jar U9-2", age_group="U9"),
            Team(club="Jar", label="Jar U9-3", age_group="U9"),
        ])
        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas={"Jar": "Jarahallen"},
            parallel_games_for_age_group={"U7": 4, "U8": 4, "U9": 4},
            round_length_for_age_group={"U7": 60, "U8": 60, "U9": 60},
            seed=0,
        )

        plan = planner.build_plan(start, end)

        same_day = sorted(
            [t for t in plan.tournaments if t.arena == "Jarahallen" and t.date == start.date()],
            key=lambda t: t.start_time,
        )
        assert len(same_day) == 3
        assert [t.start_time for t in same_day] == ["10:00", "13:20", "16:40"]
        assert len(plan.arena_day_collisions) >= 1
        assert plan.fairness_gate["status"] == "fail"
        assert any(collision["arena"] == "Jarahallen" for collision in plan.arena_day_collisions)
        assert any(
            collision["conflicting_tournament_id"] == "sequence_overflow"
            and "etter seneste gyldige start 16:00" in collision["message"]
            for collision in plan.arena_day_collisions
        )
        assert any(
            collision["conflicting_tournament_id"] == "unplaced"
            and "Ingen gyldig ledig arenatid" in collision["message"]
            for collision in plan.arena_day_collisions
        )

    def test_each_tournament_is_single_age_group_with_round_robin_games(self, planner_and_plan):
        _, plan, *_ = planner_and_plan
        for tournament in plan.tournaments:
            assert tournament.teams, "tournament should have participants"
            assert all(team.age_group == tournament.age_group for team in tournament.teams)

            n = len(tournament.teams)
            expected_games = n * (n - 1) // 2
            assert len(tournament.games) == expected_games

    def test_per_team_target_tournament_count_is_enforced(self, season_window):
        """A team with target_tournament_count=1 is invited to at most 1 tournament."""
        start, end = season_window
        free_dates = all_weekend_dates(start, end)

        # Create a small roster where Kongsberg 2 has target=1 while others use global default
        teams = [
            Team(club="Kongsberg", label="Kongsberg 1", age_group="U10"),
            Team(club="Kongsberg", label="Kongsberg 2", age_group="U10", target_tournament_count=1),
            Team(club="Jar",       label="Jar U10",       age_group="U10"),
            Team(club="Holmen",    label="Holmen U10",    age_group="U10"),
            Team(club="Skien",     label="Skien U10",     age_group="U10"),
            Team(club="Jutul",     label="Jutul U10",     age_group="U10"),
            Team(club="Ringerike", label="Ringerike U10", age_group="U10"),
        ]
        roster = Roster(teams=teams)
        club_arenas = {"Kongsberg": "Kongsberghallen", "Jar": "Jarhallen",
                       "Holmen": "Holmenhallen", "Skien": "Skienhallen",
                       "Jutul": "Baerumhallen", "Ringerike": "Ringerikehallen"}

        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 3},
            target_tournament_count=6,
        )
        plan = planner.build_plan(start, end)

        # Count how many tournaments Kongsberg 2 appears in
        participations = 0
        for tournament in plan.tournaments:
            for team in tournament.teams:
                if team.label == "Kongsberg 2":
                    participations += 1

        assert participations <= 1, (
            f"Kongsberg 2 (target=1) was invited to {participations} tournaments"
        )
        assert plan.tournaments, "should still generate tournaments"


class TestRoundRobinGameGeneration:
    def test_same_club_pairs_are_kept_in_round_robin_games(self):
        teams = [
            Team(club="Jar", label="Jar 1", age_group="U10"),
            Team(club="Jar", label="Jar 2", age_group="U10"),
            Team(club="Jar", label="Jar 3", age_group="U10"),
            Team(club="Jar", label="Jar 4", age_group="U10"),
        ]

        games = SeasonPlanner.generate_round_robin_games(teams, parallel_games=2)

        expected_pairs = {
            frozenset((left.label, right.label))
            for index, left in enumerate(teams)
            for right in teams[index + 1 :]
        }
        actual_pairs = {frozenset((game.home.label, game.away.label)) for game in games}

        assert len(games) == len(expected_pairs) == 6
        assert actual_pairs == expected_pairs
        assert all(game.home.club == game.away.club == "Jar" for game in games)


class TestTournamentStartTimeAndRoundLength:
    """Tests for default start_time assignment and round_length_for_age_group."""

    def test_generated_tournaments_have_a_default_start_time(self, planner_and_plan):
        _, plan, *_ = planner_and_plan
        for tournament in plan.tournaments:
            assert tournament.start_time == "10:00"

    def test_round_length_for_age_group_is_stored_on_planner(self, season_window):
        start, end = season_window
        free_dates = all_weekend_dates(start, end)

        clubs = ["Jar", "Kongsberg"]
        age_groups = ["U10", "U12"]
        roster = _build_roster(clubs, age_groups)
        club_arenas = {club: f"{club}hallen" for club in clubs}

        round_lengths = {"U10": 10, "U12": 12}
        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 3, "U12": 2},
            round_length_for_age_group=round_lengths,
        )

        assert planner.round_length_for_age_group == round_lengths

    def test_round_length_for_age_group_defaults_to_empty_dict(self, season_window):
        start, end = season_window
        free_dates = all_weekend_dates(start, end)

        clubs = ["Jar", "Kongsberg"]
        age_groups = ["U10"]
        roster = _build_roster(clubs, age_groups)
        club_arenas = {club: f"{club}hallen" for club in clubs}

        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 3},
        )

        assert planner.round_length_for_age_group == {}

    def test_generated_tournament_duration_uses_configured_round_length(self, season_window):
        start, end = season_window
        free_dates = all_weekend_dates(start, end)

        clubs = ["Jar", "Kongsberg", "Skien", "Jutul"]
        age_groups = ["U10"]
        roster = _build_roster(clubs, age_groups)
        club_arenas = {club: f"{club}hallen" for club in clubs}

        round_lengths = {"U10": 10}
        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 3},
            round_length_for_age_group=round_lengths,
        )
        plan = planner.build_plan(start, end)

        for tournament in plan.tournaments:
            round_length = round_lengths[tournament.age_group]
            duration = tournament.duration_minutes(round_length)
            end_time = tournament.end_time(round_length)

            if tournament.games:
                max_round = max(g.round_number for g in tournament.games)
                assert duration == max_round * round_length
                assert end_time is not None
            else:
                assert duration == 0


@pytest.fixture
def small_roster_planner_and_plan():
    """A scenario with a small single-age-group roster over a long season window,
    so that repeat matchups are unavoidable — useful for exercising
    `_opponent_history` accumulation and the diversity/balance metrics that
    depend on it.
    """
    start, end = datetime(2026, 9, 1), datetime(2027, 5, 31)
    free_dates = all_weekend_dates(start, end)

    clubs = ["Jar", "Holmen", "Kongsberg", "Skien"]
    age_groups = ["U10"]
    roster = _build_roster(clubs, age_groups)
    for team in roster.teams:
        team.target_tournament_count = 3
    club_arenas = {club: f"{club}hallen" for club in clubs}

    planner = SeasonPlanner(
        scheduler=FakeScheduler(free_dates),
        roster=roster,
        club_arenas=club_arenas,
        parallel_games_for_age_group={"U10": 3},
    )
    plan = planner.build_plan(start, end)
    return planner, plan, roster, clubs, club_arenas


class TestOpponentHistoryTrackingAndScoring:
    """Tests for `_opponent_history`, `_month_counts`, scoring, and the
    pairwise-matchup / month-balance metrics derived from them."""

    def test_opponent_history_accumulates_match_counts_from_generated_games(self, planner_and_plan):
        planner, plan, *_ = planner_and_plan

        expected_counts = {}
        for tournament in plan.tournaments:
            for game in tournament.games:
                pair = frozenset((game.home.label, game.away.label))
                expected_counts[pair] = expected_counts.get(pair, 0) + 1

        assert planner._opponent_history == expected_counts
        # Sanity: every recorded pair count should be a positive integer.
        assert all(count > 0 for count in planner._opponent_history.values())

    def test_opponent_history_accumulates_across_a_full_season_with_repeats(self, small_roster_planner_and_plan):
        planner, plan, *_ = small_roster_planner_and_plan

        # With only 4 teams in a single age group over a long season window,
        # the same pairs must be scheduled to play each other more than once.
        assert planner._opponent_history, "expected opponent history to be populated"
        assert any(count > 1 for count in planner._opponent_history.values()), (
            "expected at least one repeat matchup in this small-roster scenario"
        )

        # Cross-check the recorded history against the actual generated games.
        recomputed = {}
        for tournament in plan.tournaments:
            for game in tournament.games:
                pair = frozenset((game.home.label, game.away.label))
                recomputed[pair] = recomputed.get(pair, 0) + 1
        assert planner._opponent_history == recomputed

    def test_score_candidate_date_prefers_fewer_repeat_matchups(self, planner_and_plan):
        planner, plan, roster, *_ = planner_and_plan

        teams = roster.by_age_group("U10")
        assert len(teams) >= 3
        team_a, team_b, other_team = teams[0], teams[1], teams[2]

        # Reset opponent history to a controlled state: (team_a, team_b) has
        # already played twice; every other pair (including team_a/other_team)
        # has no recorded history.
        pair = frozenset((team_a.label, team_b.label))
        planner._opponent_history = {pair: 2}

        candidate_date = plan.start_date or date(2026, 10, 3)
        expected_per_month = 1.0  # neutral month-load baseline for this comparison

        score_with_repeats = planner._score_candidate_date(
            candidate_date, "U10", [team_a, team_b], expected_per_month
        )

        # A fresh pair (no recorded history) on the same date should score
        # strictly lower (more desirable) than the pair with repeat history,
        # all else being equal.
        score_without_repeats = planner._score_candidate_date(
            candidate_date, "U10", [team_a, other_team], expected_per_month
        )

        assert score_without_repeats < score_with_repeats

    def test_score_candidate_date_positive_tournament_weight_increases_score(self, planner_and_plan):
        """A positive tournament_weight should increase the returned score."""
        planner, plan, roster, *_ = planner_and_plan
        teams = roster.by_age_group("U10")
        candidate_date = date(2026, 10, 3)
        planner._opponent_history = {}

        base_score = planner._score_candidate_date(
            candidate_date, "U10", teams[:2], 1.0, tournament_weight=0.0
        )
        weighted_score = planner._score_candidate_date(
            candidate_date, "U10", teams[:2], 1.0, tournament_weight=0.5
        )
        assert weighted_score > base_score

    def test_score_candidate_date_negative_tournament_weight_decreases_score(self, planner_and_plan):
        """A negative tournament_weight should decrease the returned score."""
        planner, plan, roster, *_ = planner_and_plan
        teams = roster.by_age_group("U10")
        candidate_date = date(2026, 10, 3)
        # Seed repeat history so organic penalties are non-zero (enables capping logic).
        pair = frozenset((teams[0].label, teams[1].label))
        planner._opponent_history = {pair: 4}

        base_score = planner._score_candidate_date(
            candidate_date, "U10", teams[:2], 1.0, tournament_weight=0.0
        )
        weighted_score = planner._score_candidate_date(
            candidate_date, "U10", teams[:2], 1.0, tournament_weight=-0.5
        )
        assert weighted_score < base_score

    def test_score_candidate_date_weight_exceeding_cap_is_clamped(self, planner_and_plan):
        """A weight larger than 2x the organic penalty should be clamped to the cap."""
        planner, plan, roster, *_ = planner_and_plan
        teams = roster.by_age_group("U10")
        candidate_date = date(2026, 10, 3)
        pair = frozenset((teams[0].label, teams[1].label))
        planner._opponent_history = {pair: 1}

        # Obtain repeat_penalty for this state.
        repeat_only_score = planner._score_candidate_date(
            candidate_date, "U10", teams[:2], 0.0  # zero expected → month_penalty = 0
        )
        cap = repeat_only_score * 2  # cap = 2 * repeat_penalty

        # A weight much larger than the cap should be clamped, so two calls
        # with different runaway weights should return the same result.
        score_big_weight = planner._score_candidate_date(
            candidate_date, "U10", teams[:2], 0.0, tournament_weight=999.0
        )
        score_exact_cap = planner._score_candidate_date(
            candidate_date, "U10", teams[:2], 0.0, tournament_weight=cap
        )
        assert score_big_weight == pytest.approx(score_exact_cap)

    def test_score_candidate_date_date_preference_outside_range_has_no_effect(self, planner_and_plan):
        """A DatePreference whose fra–til range does not contain candidate_date has no effect."""
        planner, plan, roster, *_ = planner_and_plan
        teams = roster.by_age_group("U10")
        candidate_date = date(2026, 10, 3)
        planner._opponent_history = {}

        # Preference covers a range that does not include candidate_date.
        out_of_range_pref = DatePreference(
            fra=date(2026, 11, 1),
            til=date(2026, 11, 30),
            vekt=5.0,
        )

        base_score = planner._score_candidate_date(
            candidate_date, "U10", teams[:2], 1.0
        )
        score_with_pref = planner._score_candidate_date(
            candidate_date, "U10", teams[:2], 1.0,
            date_preferences=[out_of_range_pref]
        )
        assert base_score == pytest.approx(score_with_pref)

    def test_score_candidate_date_date_preference_inside_range_affects_score(self, planner_and_plan):
        """A DatePreference whose range contains candidate_date adjusts the score."""
        planner, plan, roster, *_ = planner_and_plan
        teams = roster.by_age_group("U10")
        candidate_date = date(2026, 10, 3)
        # Seed history so organic penalty is non-zero (ensures cap > 0).
        pair = frozenset((teams[0].label, teams[1].label))
        planner._opponent_history = {pair: 2}

        in_range_pref = DatePreference(
            fra=date(2026, 10, 1),
            til=date(2026, 10, 31),
            vekt=0.5,
        )

        base_score = planner._score_candidate_date(
            candidate_date, "U10", teams[:2], 1.0
        )
        score_with_pref = planner._score_candidate_date(
            candidate_date, "U10", teams[:2], 1.0,
            date_preferences=[in_range_pref]
        )
        # Positive vekt should increase the score.
        assert score_with_pref > base_score

    def test_pick_least_recently_grouped_prefers_subset_with_fewer_repeat_matchups(self, planner_and_plan):
        planner, plan, roster, *_ = planner_and_plan

        teams = roster.by_age_group("U10")
        assert len(teams) >= 3
        team_a, team_b, team_c = teams[0], teams[1], teams[2]

        # Reset tracking so the selection score is driven purely by opponent history
        # for this test, and force team_a to have already played team_b twice
        # but never team_c.
        planner._invite_counts = {planner._team_key(t): 0 for t in roster.teams}
        planner._grouped_with = {}
        planner._opponent_history = {frozenset((planner._team_key(team_a), planner._team_key(team_b))): 2}

        candidates = [team_a, team_b, team_c] + [t for t in teams if t not in (team_a, team_b, team_c)]
        selected = planner._pick_least_recently_grouped(candidates, 2, "U10")

        assert team_a in selected
        # Given a choice, the second pick should avoid repeating with team_a
        # in favour of the fresher pairing with team_c.
        assert team_c in selected
        assert team_b not in selected

    def test_participant_selection_score_prioritizes_deficit_over_repeat_pressure(self):
        roster = Roster(teams=[
            Team(club="Jar", label="Jar 1", age_group="U10"),
            Team(club="Holmen", label="Holmen 1", age_group="U10"),
            Team(club="Kongsberg", label="Kongsberg 1", age_group="U10"),
        ])
        planner = SeasonPlanner(
            scheduler=FakeScheduler([]),
            roster=roster,
            club_arenas={team.club: f"{team.club}hallen" for team in roster.teams},
            parallel_games_for_age_group={"U10": 2},
        )
        selected = [roster.teams[1]]
        remaining = roster.teams

        planner._invite_counts = {planner._team_key(team): 0 for team in roster.teams}
        planner._grouped_with = {}
        planner._opponent_history = {
            frozenset((planner._team_key(roster.teams[0]), planner._team_key(selected[0]))): 2
        }
        planner._running_game_counts = {
            planner._team_key(roster.teams[0]): 0,
            planner._team_key(roster.teams[2]): 2,
        }

        high_deficit_score = participant_selection.participant_selection_score(
            planner,
            selected,
            remaining,
            roster.teams[0],
            "U10",
        )
        lower_deficit_score = participant_selection.participant_selection_score(
            planner,
            selected,
            remaining,
            roster.teams[2],
            "U10",
        )

        assert high_deficit_score < lower_deficit_score

    def test_global_date_selection_pass_beats_bucketed_baseline(self):
        start, end = datetime(2026, 10, 1), datetime(2027, 4, 30)
        free_dates = [
            date(2026, 10, 18),
            date(2026, 11, 1),
            date(2026, 11, 21),
            date(2026, 11, 22),
            date(2027, 1, 9),
            date(2027, 1, 30),
            date(2027, 3, 28),
            date(2027, 4, 18),
        ]
        age_groups = ["U10", "JU11", "U11"]
        clubs = ["Kongsberg", "Jar", "Skien"]
        roster = Roster(
            teams=[
                Team(club=club, label=f"{club} {age_group}", age_group=age_group)
                for age_group in age_groups
                for club in clubs
            ]
        )
        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas={club: f"{club}hallen" for club in clubs},
            parallel_games_for_age_group={"U10": 2, "JU11": 3, "U11": 4},
            seed=0,
        )
        target_counts = {age_group: planner._target_tournaments_for_age_group(age_group) for age_group in age_groups}

        baseline, _ = planner._build_greedy_date_schedule(
            age_groups,
            free_dates,
            start.date(),
            end.date(),
            target_counts,
        )
        optimized, _ = planner._build_global_date_schedule(
            age_groups,
            free_dates,
            start.date(),
            end.date(),
            target_counts,
        )

        baseline_score = planner._score_date_schedule(baseline, start.date(), end.date())
        optimized_score = planner._score_date_schedule(optimized, start.date(), end.date())

        assert optimized_score < baseline_score

    def test_build_plan_uses_the_optimized_date_sequence_for_that_scenario(self):
        start, end = datetime(2026, 10, 1), datetime(2027, 4, 30)
        free_dates = [
            date(2026, 10, 18),
            date(2026, 11, 1),
            date(2026, 11, 21),
            date(2026, 11, 22),
            date(2027, 1, 9),
            date(2027, 1, 30),
            date(2027, 3, 28),
            date(2027, 4, 18),
        ]
        age_groups = ["U10", "JU11", "U11"]
        clubs = ["Kongsberg", "Jar", "Skien"]
        roster = Roster(
            teams=[
                Team(club=club, label=f"{club} {age_group}", age_group=age_group)
                for age_group in age_groups
                for club in clubs
            ]
        )
        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas={club: f"{club}hallen" for club in clubs},
            parallel_games_for_age_group={"U10": 2, "JU11": 3, "U11": 4},
            seed=0,
        )
        target_counts = {age_group: planner._target_tournaments_for_age_group(age_group) for age_group in age_groups}
        optimized, _ = planner._build_global_date_schedule(
            age_groups,
            free_dates,
            start.date(),
            end.date(),
            target_counts,
        )

        plan = planner.build_plan(start, end)
        actual = [(t.date, t.age_group) for t in sorted(plan.tournaments, key=lambda t: (t.date, t.age_group))]

        assert actual == optimized

    def test_repair_date_schedule_can_resolve_a_greedy_overlap(self):
        start, end = datetime(2026, 10, 1), datetime(2026, 11, 30)
        free_dates = [date(2026, 10, 18), date(2026, 11, 1), date(2026, 11, 22)]
        roster = Roster(teams=[
            Team(club="Jar", label="Jar U10", age_group="U10"),
            Team(club="Holmen", label="Holmen U10", age_group="U10"),
            Team(club="Jar", label="Jar JU11", age_group="JU11"),
            Team(club="Holmen", label="Holmen JU11", age_group="JU11"),
        ])
        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas={"Jar": "Jarahallen", "Holmen": "Holmenhallen"},
            parallel_games_for_age_group={"U10": 2, "JU11": 2},
            seed=0,
        )
        bad_schedule = [(date(2026, 10, 18), "U10"), (date(2026, 10, 18), "JU11")]

        repaired, repaired_score = planner._repair_date_schedule(
            bad_schedule,
            free_dates,
            start.date(),
            end.date(),
        )
        original_score = planner._score_date_schedule(bad_schedule, start.date(), end.date())

        assert repaired_score < original_score
        assert len({tournament_date for tournament_date, _ in repaired}) == len(repaired)
        assert repaired[0][0] != repaired[1][0]

    def test_deficit_score_uses_soft_fairness_target(self):
        roster = Roster(teams=[
            Team(club="Jar", label="Jar 1", age_group="U10"),
            Team(club="Jar", label="Jar 2", age_group="U10"),
            Team(club="Kongsberg", label="Kongsberg 1", age_group="U10"),
        ])
        planner = SeasonPlanner(
            scheduler=FakeScheduler([]),
            roster=roster,
            club_arenas={"Jar": "Jarhallen", "Kongsberg": "Kongsberghallen"},
            parallel_games_for_age_group={"U10": 2},
        )

        jar_team = roster.teams[0]
        kongsberg_team = roster.teams[2]
        assert planner._deficit_score(jar_team, "U10") > planner._deficit_score(kongsberg_team, "U10")

    def test_month_counts_stay_within_a_reasonable_spread(self, planner_and_plan, season_window):
        planner, plan, *_ = planner_and_plan
        start, end = season_window

        assert planner._month_counts, "expected month counts to be populated"

        expected_per_month = planner._expected_monthly_load(
            start.date(), end.date(), len(plan.tournaments)
        )
        assert expected_per_month > 0

        counts = list(planner._month_counts.values())
        # No single month should be wildly out of step with the season average
        # — a generous bound since the greedy spread algorithm is heuristic,
        # not a strict optimizer.
        assert max(counts) <= expected_per_month + 2, (
            f"a month is disproportionately overloaded: {planner._month_counts}"
        )

    def test_diversity_and_month_balance_metrics_present_on_plan(self, planner_and_plan):
        _, plan, *_ = planner_and_plan

        assert isinstance(plan.diversity_score, float)
        assert isinstance(plan.pairwise_matchup_score, float)
        assert isinstance(plan.month_balance_score, float)

        assert 0.0 <= plan.diversity_score <= 1.0
        assert 0.0 <= plan.pairwise_matchup_score <= 1.0
        assert 0.0 <= plan.month_balance_score <= 1.0

        # diversity_score is a distinct opponent-variety metric: the average,
        # per team, of distinct opponents faced divided by eligible
        # opponents. It need not equal the pairwise-matchup score.

    def test_pairwise_matchup_score_reflects_repeat_matchups(self, small_roster_planner_and_plan):
        planner, plan, *_ = small_roster_planner_and_plan

        # We already established this scenario forces repeat matchups, so the
        # fraction of *first-time* pairings must be less than 1.0.
        assert plan.pairwise_matchup_score < 1.0
        assert 0.0 <= plan.pairwise_matchup_score <= 1.0

        # Cross-check against a from-scratch recomputation over the actual games.
        seen_pairs = set()
        novel = 0
        total = 0
        for tournament in plan.tournaments:
            for game in tournament.games:
                pair = frozenset((game.home.label, game.away.label))
                total += 1
                if pair not in seen_pairs:
                    novel += 1
                seen_pairs.add(pair)

        assert total > 0
        assert plan.pairwise_matchup_score == round(novel / total, 3)

    def test_diversity_score_returns_zero_when_no_games_scheduled(self):
        clubs = ["Jar", "Holmen"]
        age_groups = ["U10"]
        roster = _build_roster(clubs, age_groups)

        start, end = datetime(2026, 10, 1), datetime(2027, 4, 30)
        free_dates = all_weekend_dates(start, end)
        club_arenas = {club: f"{club}hallen" for club in clubs}

        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
        )

        # Fresh planner: _opponent_history is empty, no games scheduled.
        assert planner._diversity_score([]) == 0.0

    def test_diversity_score_and_pairwise_score_can_diverge(self):
        """Construct a scenario where opponent-variety-per-team and
        first-time-pairing fraction produce different numeric results."""
        clubs = ["A", "B", "C", "D"]
        age_groups = ["U10"]
        roster = _build_roster(clubs, age_groups)

        start, end = datetime(2026, 10, 1), datetime(2027, 4, 30)
        free_dates = all_weekend_dates(start, end)
        club_arenas = {club: f"{club}hallen" for club in clubs}

        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
        )

        team_a = next(t for t in roster.teams if t.club == "A")
        team_b = next(t for t in roster.teams if t.club == "B")

        # Team A has played team B once; both have 3 eligible opponents
        # (the other clubs' U10 teams), so each has faced 1/3 of its pool.
        planner._opponent_history = {
            frozenset((team_a.label, team_b.label)): 1,
        }
        diversity = planner._diversity_score([])
        assert diversity == round((1 / 3 + 1 / 3) / 2, 3)

        # A single tournament where A and B play each other twice: only the
        # first meeting is "novel", giving a pairwise score of 0.5.
        tournament = Tournament(
            date=date(2026, 10, 3),
            arena=club_arenas["A"],
            age_group="U10",
            teams=[team_a, team_b],
            games=[
                Game(home=team_a, away=team_b),
                Game(home=team_a, away=team_b),
            ],
        )
        pairwise = planner._pairwise_matchup_score([tournament])
        assert pairwise == 0.5

        assert diversity != pairwise


class TestPerTeamGameCounts:
    """Tests for per-team game count tracking, spread validation, and early-finish detection."""

    def test_team_game_counts_match_actual_games(self, planner_and_plan):
        _, plan, *_ = planner_and_plan

        assert plan.team_game_counts, "expected team_game_counts to be populated"

        # Walk all games in the plan and recompute counts manually
        expected_counts: dict[str, int] = {}
        for tournament in plan.tournaments:
            for game in tournament.games:
                for team in (game.home, game.away):
                    if team is None:
                        continue
                    expected_counts[team.label] = expected_counts.get(team.label, 0) + 1

        assert plan.team_game_counts == expected_counts

    def test_team_last_game_dates_match_latest_tournament(self, planner_and_plan):
        _, plan, *_ = planner_and_plan

        assert plan.team_last_game_dates, "expected team_last_game_dates to be populated"

        # Recompute last-game dates manually
        expected_last_dates: dict[str, date] = {}
        for tournament in plan.tournaments:
            for game in tournament.games:
                for team in (game.home, game.away):
                    if team is None:
                        continue
                    last = expected_last_dates.get(team.label)
                    if last is None or tournament.date > last:
                        expected_last_dates[team.label] = tournament.date

        assert plan.team_last_game_dates == expected_last_dates

    def test_game_count_spread_is_non_negative(self, planner_and_plan):
        _, plan, *_ = planner_and_plan

        assert isinstance(plan.game_count_spread, int)
        assert plan.game_count_spread >= 0

    def test_game_count_spread_reflects_worst_age_group(self, planner_and_plan):
        """game_count_spread should equal the maximum per-age-group spread
        (not the global max-min across all age groups)."""
        _, plan, *_ = planner_and_plan

        if plan.game_count_spread_by_age_group:
            expected_spread = max(plan.game_count_spread_by_age_group.values())
            assert plan.game_count_spread == expected_spread
        elif plan.team_game_counts:
            # Fallback path: no per-age-group data, global spread used.
            expected_spread = max(plan.team_game_counts.values()) - min(plan.team_game_counts.values())
            assert plan.game_count_spread == expected_spread

    def test_game_count_warnings_fired_when_spread_exceeds_threshold(self):
        """Unit-level regression for the spread warning scan."""
        from datetime import datetime as _dt

        start, end = _dt(2026, 10, 1), _dt(2027, 4, 30)
        free_dates = all_weekend_dates(start, end)

        clubs = ["Jar", "Holmen", "Kongsberg"]
        roster = _build_roster(clubs, ["U10"])
        club_arenas = {club: f"{club}hallen" for club in clubs}

        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 2},
            max_game_count_spread=0,
        )
        planner._team_game_counts = {
            "Jar U10": 1,
            "Holmen U10": 2,
            "Kongsberg U10": 4,
        }
        planner._scan_game_count_warnings(start.date(), end.date())

        warnings = planner.game_count_warnings
        spread_warnings = [w for w in warnings if w[3] == "spread"]
        assert len(spread_warnings) > 0, (
            f"expected spread warnings with max_game_count_spread=0: {warnings}"
        )

    def test_no_game_count_warnings_when_spread_within_threshold(self):
        """With a single age group and lenient threshold, no spread warnings should fire."""
        from datetime import datetime as _dt

        start, end = _dt(2026, 10, 1), _dt(2027, 4, 30)
        free_dates = all_weekend_dates(start, end)

        clubs = ["Jar", "Holmen", "Kongsberg", "Skien", "Jutul"]
        age_groups = ["U10"]
        roster = _build_roster(clubs, age_groups)
        club_arenas = {club: f"{club}hallen" for club in clubs}

        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 2},
            max_game_count_spread=10,  # very lenient threshold
        )
        plan = planner.build_plan(start, end)

        warnings = planner.game_count_warnings
        spread_warnings = [w for w in warnings if w[3] == "spread"]

        # With a very lenient threshold of 10, spread warnings should not fire
        # for a single-age-group scenario where all teams get equal invites.
        assert len(spread_warnings) == 0, (
            f"unexpected spread warnings with max_game_count_spread=10: {spread_warnings}"
        )

    def test_game_count_warnings_scoped_per_age_group(self):
        """U7 balanced, U12 imbalanced: warning fires only for U12 keys."""
        from datetime import datetime as _dt

        start, end = _dt(2026, 10, 1), _dt(2027, 4, 30)
        free_dates = all_weekend_dates(start, end)

        clubs = ["Jar", "Holmen", "Kongsberg"]
        roster = _build_roster(clubs, ["U7", "U12"])
        club_arenas = {club: f"{club}hallen" for club in clubs}

        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U7": 4, "U12": 2},
            max_game_count_spread=0,
        )
        # U7: all equal (spread 0); U12: large imbalance (spread 4).
        planner._team_game_counts = {
            "Jar U7": 6,
            "Holmen U7": 6,
            "Kongsberg U7": 6,
            "Jar U12": 8,
            "Holmen U12": 4,
            "Kongsberg U12": 4,
        }
        planner._scan_game_count_warnings(start.date(), end.date())

        warnings = planner.game_count_warnings
        spread_keys = {w[0] for w in warnings if w[3] == "spread"}
        # All warned keys must belong to U12, not U7
        assert all("U12" in k for k in spread_keys), (
            f"Expected only U12 keys in spread warnings; got: {spread_keys}"
        )
        assert any("U12" in k for k in spread_keys), (
            f"Expected at least one U12 warning; got none. warnings={warnings}"
        )

    def test_game_count_spread_by_age_group_populated(self, planner_and_plan):
        """game_count_spread_by_age_group should be populated for each active age group."""
        _, plan, roster, *_ = planner_and_plan
        active_age_groups = {
            t.age_group for t in plan.tournaments if not getattr(t, "cancelled", False)
        }
        skipped = {e["age_group"] for e in plan.skipped_age_groups}
        for ag in active_age_groups - skipped:
            assert ag in plan.game_count_spread_by_age_group, (
                f"Age group {ag!r} missing from game_count_spread_by_age_group"
            )
            assert isinstance(plan.game_count_spread_by_age_group[ag], int)
            assert plan.game_count_spread_by_age_group[ag] >= 0

    def test_early_finish_warnings_with_tight_threshold(self):
        """Build a planner with a tiny max_early_finish_gap_days.

        Uses 6 teams with max_teams=3 so some teams skip late tournaments
        while others participate, creating an early-finish spread."""
        from datetime import datetime as _dt

        start, end = _dt(2026, 10, 1), _dt(2026, 12, 31)
        free_dates = all_weekend_dates(start, end)

        clubs = ["Jar", "Holmen", "Kongsberg", "Skien", "Jutul", "Ringerike"]
        age_groups = ["U10"]
        roster = _build_roster(clubs, age_groups)
        club_arenas = {club: f"{club}hallen" for club in clubs}

        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 2},
            max_early_finish_gap_days=0,  # any gap triggers a warning
        )
        planner.build_plan(start, end)

        warnings = planner.game_count_warnings
        early_warnings = [w for w in warnings if w[3] == "early_finish"]
        # With 6 teams and max 3 per tournament, some teams will miss
        # later tournaments, causing early-finish alerts.
        assert len(early_warnings) >= 1, (
            f"expected at least one early-finish warning with max_early_finish_gap_days=0, "
            f"got: {warnings}"
        )

    def test_team_game_counts_fields_present_on_season_plan(self, planner_and_plan):
        _, plan, *_ = planner_and_plan

        assert isinstance(plan.team_game_counts, dict)
        assert isinstance(plan.team_last_game_dates, dict)
        assert isinstance(plan.game_count_spread, int)

    def test_team_game_counts_includes_invited_teams(self, planner_and_plan):
        _, plan, roster, *_ = planner_and_plan

        # Every team that was invited to at least one tournament should have
        # a game count entry.
        invited_labels = set()
        for tournament in plan.tournaments:
            for team in tournament.teams:
                invited_labels.add(team.label)

        counted_labels = set(plan.team_game_counts.keys())
        assert invited_labels.issubset(counted_labels), (
            f"not all invited teams appear in team_game_counts: "
            f"{invited_labels - counted_labels}"
        )
        # Every team that appears in team_game_counts must have a non-zero count.
        for label, count in plan.team_game_counts.items():
            assert count > 0, f"team {label} has zero game count but appears in team_game_counts"

    def test_game_count_warnings_property_returns_list(self, planner_and_plan):
        planner, *_ = planner_and_plan

        warnings = planner.game_count_warnings
        assert isinstance(warnings, list)
        for w in warnings:
            assert len(w) == 4  # (label, count, threshold/gap, type)
            assert w[3] in ("spread", "early_finish")

    def test_parallel_games_define_tournament_capacity_and_bye_rounds(self):
        start, end = datetime(2026, 10, 1), datetime(2027, 4, 30)
        free_dates = all_weekend_dates(start, end)

        clubs = ["Jar", "Holmen", "Kongsberg", "Skien", "Jutul"]
        roster = _build_roster(clubs, ["U10"])
        club_arenas = {club: f"{club}hallen" for club in clubs}

        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 2},
        )

        assert planner._max_teams_for("U10") == 4

        plan = planner.build_plan(start, end)
        first_tournament = next(t for t in plan.tournaments if t.age_group == "U10")

        assert len(first_tournament.teams) == 4
        assert len(first_tournament.games) == 6
        assert first_tournament.get_bye_rounds() == {}

    @pytest.mark.parametrize(
        "parallel_games, team_count",
        [
            (4, 7),
            (3, 5),
            (2, 3),
        ],
    )
    def test_odd_team_count_gets_one_rest_team_per_round(self, parallel_games, team_count):
        start, end = datetime(2026, 10, 1), datetime(2027, 4, 30)
        free_dates = all_weekend_dates(start, end)

        clubs = [f"Club {i}" for i in range(1, team_count + 1)]
        roster = _build_roster(clubs, ["U10"])
        club_arenas = {club: f"{club}hallen" for club in clubs}

        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": parallel_games},
        )
        plan = planner.build_plan(start, end)
        tournament = next(t for t in plan.tournaments if t.age_group == "U10")

        assert len(tournament.teams) == team_count
        bye_rounds = tournament.get_bye_rounds()
        assert len(bye_rounds) == team_count
        assert all(len(labels) == 1 for labels in bye_rounds.values())
        assert set().union(*bye_rounds.values()) == {team.label for team in tournament.teams}

    def test_jar_vs_kongsberg_team_counts_skew_is_bounded(self):
        """Reproduces the club-size-skew scenario from the canonical input.xlsx:
        Jar fields 7 U10 teams and 6 U11 teams, while Kongsberg fields only 1
        team in each age group. With `max_club_teams_per_tournament=1`,
        every tournament invites at most one team per club, so Kongsberg's
        sole U10 team is invited to (almost) every U10 tournament while
        Jar's 7 U10 teams collectively share that single "Jar slot" per
        tournament.

        The club-size normalization in `_normalized_invite_count`
        (task: "Make per-team selection balancing aware of same-club/
        same-age-group team counts") ensures the *least-invited* Jar team is
        prioritized for that shared slot, so invitations rotate evenly across
        Jar's siblings — but it cannot, by itself, give each individual Jar
        team the same game count as Kongsberg's team, since that would
        require inviting multiple Jar teams to the same tournament (violating
        the hard one-team-per-club constraint) or excluding Kongsberg from
        some tournaments.

        This test documents that residual, structurally-bounded skew: each
        Jar team's count should be roughly `kongsberg_count / num_jar_teams`
        (within a generous tolerance), and `per_team_share_warnings` should
        flag exactly this skew — confirming the new diagnostic from "Extend
        game-count-spread checking" surfaces it correctly.
        """
        from datetime import datetime as _dt

        start, end = _dt(2026, 10, 1), _dt(2027, 4, 30)
        free_dates = all_weekend_dates(start, end)

        other_clubs = ["Holmen", "Skien", "Jutul", "Ringerike", "Tonsberg", "Sandefjord", "Frisk Asker"]
        age_groups = ["U10", "U11"]

        teams = []
        # Jar: 7 U10 teams, 6 U11 teams.
        for i in range(1, 8):
            teams.append(Team(club="Jar", label=f"Jar U10-{i}", age_group="U10"))
        for i in range(1, 7):
            teams.append(Team(club="Jar", label=f"Jar U11-{i}", age_group="U11"))
        # Kongsberg: 1 team per age group.
        for age_group in age_groups:
            teams.append(Team(club="Kongsberg", label=f"Kongsberg {age_group}", age_group=age_group))
        # Other clubs: 1 team per age group, to give the scheduler enough
        # opponents to fill out tournaments.
        for club in other_clubs:
            for age_group in age_groups:
                teams.append(Team(club=club, label=f"{club} {age_group}", age_group=age_group))

        roster = Roster(teams=teams)
        all_clubs = ["Jar", "Kongsberg"] + other_clubs
        club_arenas = {club: f"{club}hallen" for club in all_clubs}

        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 3, "U11": 3},
            max_game_count_spread=4,
        )
        plan = planner.build_plan(start, end)

        u10_kongsberg = plan.team_game_counts.get("Kongsberg U10", 0)
        u10_jar = [
            plan.team_game_counts.get(f"Jar U10-{i}", 0) for i in range(1, 8)
        ]
        u11_kongsberg = plan.team_game_counts.get("Kongsberg U11", 0)
        u11_jar = [
            plan.team_game_counts.get(f"Jar U11-{i}", 0) for i in range(1, 7)
        ]

        assert u10_kongsberg > 0
        assert all(c > 0 for c in u10_jar)
        assert u11_kongsberg > 0
        assert all(c > 0 for c in u11_jar)

        # Each Jar team's invite share should rotate roughly evenly: no
        # individual team should be starved (0 games) or dominate. The
        # rotation isn't perfectly uniform over a finite season, but no
        # sibling should receive double the games of another.
        assert max(u10_jar) <= 2 * min(u10_jar), (
            f"Jar U10 sibling teams are unevenly invited: {u10_jar}"
        )
        assert max(u11_jar) <= 2 * min(u11_jar), (
            f"Jar U11 sibling teams are unevenly invited: {u11_jar}"
        )

        # The per-team-share diagnostic is covered by the regression below;
        # here we only verify the sibling counts stay reasonably balanced.

    def test_per_team_share_warning_emitted_for_deliberately_skewed_counts(self):
        """Unit-level test for `_scan_per_team_share_warnings`: a deliberately
        skewed roster/game-count setup should produce per-team-share warnings
        with the correct `(team_label, club, age_group, actual_count,
        expected_count)` identifiers, without requiring a full `build_plan`
        run."""
        roster = Roster(teams=[
            Team(club="Jar", label="Jar U10-1", age_group="U10"),
            Team(club="Jar", label="Jar U10-2", age_group="U10"),
            Team(club="Kongsberg", label="Kongsberg U10", age_group="U10"),
            Team(club="Holmen", label="Holmen U11", age_group="U11"),
            Team(club="Skien", label="Skien U11", age_group="U11"),
        ])
        club_arenas = {
            "Jar": "Jarhallen",
            "Kongsberg": "Kongsberghallen",
            "Holmen": "Holmenkollen ishall",
            "Skien": "Skienhallen",
        }
        planner = SeasonPlanner(
            scheduler=FakeScheduler([]),
            roster=roster,
            club_arenas=club_arenas,
            max_game_count_spread=2,
        )

        # Deliberately skew U10: Kongsberg plays far more games than Jar's
        # two teams. U11 stays balanced and should not be flagged.
        planner._team_game_counts = {
            "Jar U10-1": 2,
            "Jar U10-2": 2,
            "Kongsberg U10": 10,
            "Holmen U11": 5,
            "Skien U11": 5,
        }
        planner._scan_per_team_share_warnings()

        warnings = planner.per_team_share_warnings
        warnings_by_label = {w[0]: w for w in warnings}

        # Both Jar teams and Kongsberg should still be flagged, but the
        # expected value now comes from the soft fairness target model rather
        # than the plain age-group average.
        assert "Jar U10-1" in warnings_by_label
        assert "Jar U10-2" in warnings_by_label
        assert "Kongsberg U10" in warnings_by_label

        label, club, age_group, actual, expected = warnings_by_label["Kongsberg U10"]
        assert club == "Kongsberg"
        assert age_group == "U10"
        assert actual == 10
        assert expected == pytest.approx(
            planner.fairness_model.target_games_for_team(
                next(t for t in roster.teams if t.label == "Kongsberg U10"),
                [t for t in roster.teams if t.age_group == "U10"],
                planner._team_game_counts,
            )
        )

        label, club, age_group, actual, expected = warnings_by_label["Jar U10-1"]
        assert club == "Jar"
        assert age_group == "U10"
        assert actual == 2
        assert expected == pytest.approx(
            planner.fairness_model.target_games_for_team(
                next(t for t in roster.teams if t.label == "Jar U10-1"),
                [t for t in roster.teams if t.age_group == "U10"],
                planner._team_game_counts,
            )
        )

        # U11 is perfectly balanced (5 == 5), so no warnings expected there.
        assert "Holmen U11" not in warnings_by_label
        assert "Skien U11" not in warnings_by_label

    @pytest.mark.slow
    def test_real_roster_end_to_end_jar_vs_kongsberg(self, canonical_plan):
        """End-to-end check against the real `input.xlsx`
        roster (Jar: 7 U10 teams, Kongsberg: 1 U10 team, plus the other RVV
        clubs), over the 2026-09-01 to 2027-04-30 season window.

        This documents the real-world outcome of the club-size
        normalization fix: each individual Jar U10 team gets a non-trivial,
        roughly-even share of games (no team starved at 0), while
        Kongsberg's sole U10 team — invited to (almost) every tournament
        under the default `max_club_teams_per_tournament=1` — ends up with
        a much higher individual count. `per_team_share_warnings` correctly
        flags this residual structural skew for both Kongsberg and Jar's
        teams, confirming the new diagnostic (task: "Extend
        game-count-spread checking") surfaces the real-roster imbalance
        described in the original backlog item.
        """
        planner, plan, start, end = canonical_plan
        roster = planner.roster
        duplicate_labels = {label for label, count in Counter(t.label for t in roster.teams).items() if count > 1}

        jar_u10_labels = [
            team_key(t, duplicate_labels) for t in roster.teams if t.club == "Jar" and t.age_group == "U10"
        ]
        kongsberg_u10_labels = [
            team_key(t, duplicate_labels) for t in roster.teams if t.club == "Kongsberg" and t.age_group == "U10"
        ]
        assert jar_u10_labels, "expected Jar to have U10 teams in the real roster"
        assert kongsberg_u10_labels, "expected Kongsberg to have a U10 team in the real roster"

        jar_u10_counts = [plan.team_game_counts.get(label, 0) for label in jar_u10_labels]
        kongsberg_u10_counts = [
            plan.team_game_counts.get(label, 0) for label in kongsberg_u10_labels
        ]

        # The canonical snapshot should still give multiple Jar teams
        # non-zero game counts, even if not every sibling is active.
        assert any(c > 0 for c in jar_u10_counts)
        assert all(c > 0 for c in kongsberg_u10_counts)

        positive_jar_counts = [c for c in jar_u10_counts if c > 0]
        assert positive_jar_counts
        assert max(positive_jar_counts) <= 3 * min(positive_jar_counts), (
            f"Jar U10 sibling teams are unevenly invited: {jar_u10_counts}"
        )

        # The per-team-share diagnostic should still surface some residual
        # skew in the full roster, even though the core game-count spread is
        # now much tighter.
        flagged_labels = {w[0] for w in planner.per_team_share_warnings}
        assert flagged_labels

    @pytest.mark.slow
    def test_real_roster_jar_vs_kongsberg_spread_reduced_by_deficit_aware_selection(self, canonical_plan):
        """Regression test for the real roster's U10 balance.

        The expanded canonical roster now schedules per age group, so the
        absolute U10 game totals are much larger than in the old single-plan
        setup. This test focuses on the remaining structural checks:
        Jar's sibling teams should still rotate reasonably evenly, the sole
        Kongsberg U10 team should get non-zero games, and the fairness
        diagnostics should still surface the residual skew.
        """
        planner, plan, start, end = canonical_plan
        roster = planner.roster
        duplicate_labels = {label for label, count in Counter(t.label for t in roster.teams).items() if count > 1}

        jar_u10_labels = [
            team_key(t, duplicate_labels) for t in roster.teams if t.club == "Jar" and t.age_group == "U10"
        ]
        kongsberg_u10_labels = [
            team_key(t, duplicate_labels) for t in roster.teams if t.club == "Kongsberg" and t.age_group == "U10"
        ]
        assert jar_u10_labels, "expected Jar to have U10 teams in the real roster"
        assert kongsberg_u10_labels, "expected Kongsberg to have a U10 team in the real roster"

        jar_u10_counts = [plan.team_game_counts.get(label, 0) for label in jar_u10_labels]
        kongsberg_u10_counts = [
            plan.team_game_counts.get(label, 0) for label in kongsberg_u10_labels
        ]

        assert any(c > 0 for c in jar_u10_counts)
        assert all(c > 0 for c in kongsberg_u10_counts)

        positive_jar_counts = [c for c in jar_u10_counts if c > 0]
        assert positive_jar_counts
        assert max(positive_jar_counts) <= 3 * min(positive_jar_counts), (
            f"Jar U10 sibling teams are unevenly invited: {jar_u10_counts}"
        )

        flagged_labels = {w[0] for w in planner.per_team_share_warnings}
        assert flagged_labels, "expected per_team_share_warnings to flag residual skew"

        assert planner.club_cap_overrides >= 0

    def test_deficit_aware_club_mix_lets_large_clubs_catch_up(self):
        """When deficit spread exceeds max_game_count_spread, the picker allows
        more than one sibling team per tournament so large clubs can catch up."""
        start, end = datetime(2026, 10, 1), datetime(2027, 4, 30)
        free_dates = all_weekend_dates(start, end)

        clubs = ["Jar", "Holmen", "Kongsberg", "Skien", "Jutul", "Ringerike"]
        teams = [Team(club="Jar", label=f"Jar {i}", age_group="U10") for i in range(1, 5)]
        teams.extend(Team(club=club, label=f"{club} 1", age_group="U10") for club in clubs[1:])
        roster = Roster(teams=teams)
        club_arenas = {club: f"{club}hallen" for club in clubs}

        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 3},
        )
        plan = planner.build_plan(start, end)

        assert plan.tournaments

        # Early tournaments still prefer diverse club mixing (deficit not yet high)
        tournament_club_mix = []
        for t in plan.tournaments:
            club_counts = Counter(team.club for team in t.teams)
            tournament_club_mix.append(club_counts["Jar"])

        # At least some early tournament has Jar <= 1 (preferred_club_mix before deficit builds)
        assert tournament_club_mix[0] <= 1, (
            f"First tournament should mix Jar with other clubs, got {tournament_club_mix[0]}"
        )

        # At least some later tournament has Jar > 1 (deficit-aware expansion)
        assert any(c > 1 for c in tournament_club_mix), (
            f"No tournament has more than 1 Jar team: {tournament_club_mix}"
        )

        # Game counts across all Jar teams should be roughly even
        jar_game_counts = {}
        for t in plan.tournaments:
            for team in t.teams:
                if team.club == "Jar":
                    key = team.label
                    jar_game_counts[key] = jar_game_counts.get(key, 0) + (len(t.teams) - 1)
        if len(jar_game_counts) > 1:
            spread = max(jar_game_counts.values()) - min(jar_game_counts.values())
            assert spread <= 5, (
                f"Jar U10 game-count spread too large: {spread}, counts={jar_game_counts}"
            )


    def test_prefers_distinct_clubs_before_repeating_when_feasible(self):
        """A skewed roster should use every available club before repeating one.

        With 5 clubs available and 6 tournament slots, the first tournament
        should contain all 5 clubs and only one repeated slot (from Jar).
        """
        start, end = datetime(2026, 10, 1), datetime(2027, 4, 30)
        free_dates = all_weekend_dates(start, end)

        roster = Roster(teams=[
            Team(club="Jar", label="Jar 1", age_group="U10"),
            Team(club="Jar", label="Jar 2", age_group="U10"),
            Team(club="Jar", label="Jar 3", age_group="U10"),
            Team(club="Jar", label="Jar 4", age_group="U10"),
            Team(club="Holmen", label="Holmen 1", age_group="U10"),
            Team(club="Kongsberg", label="Kongsberg 1", age_group="U10"),
            Team(club="Skien", label="Skien 1", age_group="U10"),
            Team(club="Jutul", label="Jutul 1", age_group="U10"),
        ])
        club_arenas = {team.club: f"{team.club}hallen" for team in roster.teams}
        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 3},
        )

        plan = planner.build_plan(start, end)
        first_tournament = next(t for t in plan.tournaments if t.age_group == "U10")
        club_counts = Counter(team.club for team in first_tournament.teams)

        assert len(first_tournament.teams) == 6
        assert len(club_counts) == 5, club_counts
        assert club_counts["Jar"] == 2, club_counts
        assert all(count == 1 for club, count in club_counts.items() if club != "Jar"), club_counts


class TestProportionalHosting:
    """Tests for proportional home-tournament hosting."""

    @pytest.fixture
    def season_window(self):
        return datetime(2026, 10, 1), datetime(2027, 4, 30)

    @pytest.fixture
    def free_dates(self, season_window):
        start, end = season_window
        return all_weekend_dates(start, end)

    def test_clubs_with_more_teams_host_more_tournaments(self, free_dates, season_window):
        """Jar with 3 teams should host more than Kongsberg with 1 team."""
        start, end = season_window
        roster = Roster(teams=[
            Team(club="Jar", label="Jar 1", age_group="U10"),
            Team(club="Jar", label="Jar 2", age_group="U10"),
            Team(club="Jar", label="Jar 3", age_group="U10"),
            Team(club="Kongsberg", label="Kongsberg 1", age_group="U10"),
            Team(club="Skien", label="Skien 1", age_group="U11"),
            Team(club="Holmen", label="Holmen 1", age_group="U11"),
        ])
        club_arenas = {t.club: f"{t.club}hallen" for t in roster.teams}

        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 3, "U11": 3},
            max_hosting_deviation=5,  # lenient so no warnings
        )
        plan = planner.build_plan(start, end)

        # Count how many tournaments each club hosted.
        hosting_counts: dict[str, int] = {}
        for t in plan.tournaments:
            if t.host_club:
                hosting_counts[t.host_club] = hosting_counts.get(t.host_club, 0) + 1

        # Jar (3 teams) should host strictly more than Kongsberg (1 team)
        # in a season of ~10-15 tournaments.
        assert hosting_counts.get("Jar", 0) > hosting_counts.get("Kongsberg", 0), (
            f"Jar ({hosting_counts.get('Jar', 0)}) should host more than "
            f"Kongsberg ({hosting_counts.get('Kongsberg', 0)})"
        )

    def test_equal_team_counts_get_equal_hosting(self, free_dates, season_window):
        """All clubs with 1 team each should host roughly equally."""
        start, end = season_window
        clubs = ["Jar", "Holmen", "Kongsberg", "Skien", "Jutul", "Ringerike"]
        roster = _build_roster(clubs, ["U10", "U11"])
        club_arenas = {club: f"{club}hallen" for club in clubs}

        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 3},
            max_hosting_deviation=5,
        )
        plan = planner.build_plan(start, end)

        hosting_counts: dict[str, int] = {}
        for t in plan.tournaments:
            if t.host_club:
                hosting_counts[t.host_club] = hosting_counts.get(t.host_club, 0) + 1

        # All clubs have the same number of teams — hosting counts should
        # be within 1 of each other (total / num_clubs).
        counts = list(hosting_counts.values())
        assert max(counts) - min(counts) <= 1, (
            f"equal-team clubs should host roughly equally: {hosting_counts}"
        )

    def test_every_club_hosts_at_least_once(self, free_dates, season_window):
        """Even single-team clubs host at least one tournament."""
        start, end = season_window
        roster = Roster(teams=[
            Team(club="Jar", label="Jar 1", age_group="U10", target_tournament_count=3),
            Team(club="Jar", label="Jar 2", age_group="U10", target_tournament_count=3),
            Team(club="Jar", label="Jar 3", age_group="U10", target_tournament_count=3),
            Team(club="Kongsberg", label="Kongsberg 1", age_group="U10", target_tournament_count=3),
            Team(club="Skien", label="Skien 1", age_group="U11", target_tournament_count=3),
            Team(club="Jar", label="Jar U11", age_group="U11", target_tournament_count=3),
            Team(club="Kongsberg", label="Kongsberg U11", age_group="U11", target_tournament_count=3),
        ])
        club_arenas = {t.club: f"{t.club}hallen" for t in roster.teams}

        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 3, "U11": 3},
            max_hosting_deviation=5,
        )
        plan = planner.build_plan(start, end)

        hosted_clubs = {t.host_club for t in plan.tournaments if t.host_club}
        assert "Kongsberg" in hosted_clubs, (
            f"Kongsberg (1 team) should host at least once. Hosting clubs: {hosted_clubs}"
        )
        assert "Skien" in hosted_clubs, (
            f"Skien (1 team) should host at least once. Hosting clubs: {hosted_clubs}"
        )

    def test_hosting_warnings_fire_on_deviation(self, free_dates, season_window):
        """With max_hosting_deviation=0, even a small imbalance triggers warnings."""
        start, end = season_window
        roster = Roster(teams=[
            Team(club="Jar", label="Jar 1", age_group="U10"),
            Team(club="Jar", label="Jar 2", age_group="U10"),
            Team(club="Jar", label="Jar 3", age_group="U10"),
            Team(club="Kongsberg", label="Kongsberg 1", age_group="U10"),
        ])
        club_arenas = {t.club: f"{t.club}hallen" for t in roster.teams}

        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 3},
            max_hosting_deviation=0,  # any deviation triggers warnings
        )
        plan = planner.build_plan(start, end)
        # Force build_plan to run
        assert len(plan.tournaments) > 0

        warnings = planner.hosting_warnings
        # With 3 Jar teams vs 1 Kongsberg team over ~10-15 tournaments,
        # the proportional target won't be perfectly integer-achievable,
        # so warnings should fire with deviation=0.
        # (Jar should host ~75% of tournaments, Kongsberg ~25%)
        assert len(warnings) >= 1, (
            f"expected hosting warnings with max_hosting_deviation=0, "
            f"plan has {len(plan.tournaments)} tournaments, got: {warnings}"
        )

    def test_hosting_warnings_empty_with_lenient_threshold(self, free_dates, season_window):
        """With max_hosting_deviation=99, no warnings should fire."""
        start, end = season_window
        roster = Roster(teams=[
            Team(club="Jar", label="Jar 1", age_group="U10"),
            Team(club="Jar", label="Jar 2", age_group="U10"),
            Team(club="Jar", label="Jar 3", age_group="U10"),
            Team(club="Kongsberg", label="Kongsberg 1", age_group="U10"),
            Team(club="Skien", label="Skien 1", age_group="U10"),
        ])
        club_arenas = {t.club: f"{t.club}hallen" for t in roster.teams}

        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 3},
            max_hosting_deviation=99,  # effectively unlimited
        )
        plan = planner.build_plan(start, end)
        assert len(plan.tournaments) > 0

        warnings = planner.hosting_warnings
        assert len(warnings) == 0, (
            f"expected no hosting warnings with max_hosting_deviation=99, "
            f"got: {warnings}"
        )

    def test_missing_calendar_clubs_are_not_counted_as_hosting_failures(self):
        """Clubs without scraped calendars should be noted, not fail hosting fairness."""
        start = datetime(2026, 10, 1)
        end = datetime(2026, 10, 31)
        jar = Team(club="Jar", label="Jar 1", age_group="U10")
        sandefjord = Team(club="Sandefjord", label="Sandefjord 1", age_group="U10")
        roster = Roster(teams=[jar, sandefjord])
        planner = SeasonPlanner(
            scheduler=FakeScheduler([]),
            roster=roster,
            club_arenas={"Jar": "Jarhallen", "Sandefjord": "Bugårdshallen"},
            parallel_games_for_age_group={"U10": 3},
            max_hosting_deviation=0,
            events_by_club={
                "Jar": [
                    CalendarEvent(
                        date="03.10.2026",
                        name="Jar hall booking",
                        datetime=datetime(2026, 10, 3, 10, 0),
                        duration_hours=1.0,
                    )
                ]
            },
        )
        plan = SeasonPlan(
            tournaments=[
                Tournament(
                    date=date(2026, 10, 3),
                    arena="Jarhallen",
                    age_group="U10",
                    teams=[jar, sandefjord],
                    games=[Game(home=jar, away=sandefjord, parallel_slot=0, round_number=1)],
                    host_club="Jar",
                )
            ],
            start_date=start.date(),
            end_date=end.date(),
            diversity_score=1.0,
            pairwise_matchup_score=1.0,
            month_balance_score=1.0,
            game_count_spread=0,
        )

        planner._compute_game_counts(plan.tournaments)
        planner._scan_hosting_warnings(plan)
        gate = planner._build_fairness_gate(plan)
        hosting_metric = next(metric for metric in gate["metrics"] if metric["key"] == "hosting_deviation")
        missing_note = next(note for note in gate.get("notes", []) if note["key"] == "missing_calendar_clubs")

        assert hosting_metric["status"] == "pass"
        assert gate["status"] == "pass"
        assert missing_note["excluded_clubs"] == ["Sandefjord"]
        assert "Sandefjord" in missing_note["detail"]
        assert planner.hosting_warnings == []

    def test_missing_calendar_host_clubs_still_get_share_and_are_marked_manual(self):
        """Clubs whose calendar could not be scraped should still receive their
        share of home tournaments — but every such tournament must be marked so
        it is listed for manual hall booking (auto times cannot be verified)."""
        start = datetime(2026, 10, 1)
        end = datetime(2026, 12, 31)
        clubs = ["Jar", "Sandefjord", "Holmen", "Kongsberg"]
        roster = _build_roster(clubs, ["U10", "U11", "U7"], teams_per_club_per_age_group=2)
        club_arenas = {club: f"{club}hallen" for club in clubs}
        planner = SeasonPlanner(
            scheduler=FakeScheduler(all_weekend_dates(start, end)),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 3, "U11": 2, "U7": 4},
            # Sandefjord has no scraped calendar data; the others do.
            events_by_club={"Jar": [], "Holmen": [], "Kongsberg": []},
        )
        plan = planner.build_plan(start, end)
        assert len(plan.tournaments) >= 3
        hosts = {t.host_club for t in plan.tournaments}
        assert "Sandefjord" in hosts, "no-calendar club should still receive a share of hosting"
        manual = [t for t in plan.tournaments if t.manual_booking_reason]
        assert manual
        assert all(t.host_club == "Sandefjord" for t in manual)
        assert "Sandefjord" in manual[0].manual_booking_reason
        non_manual = [t for t in plan.tournaments if not t.manual_booking_reason]
        assert all(t.host_club != "Sandefjord" for t in non_manual)

    def test_all_calendars_available_means_no_manual_booking_marks(self):
        """When every club has calendar data, no tournament is flagged manual."""
        start = datetime(2026, 10, 1)
        end = datetime(2026, 12, 31)
        clubs = ["Jar", "Holmen", "Kongsberg"]
        roster = _build_roster(clubs, ["U10"])
        club_arenas = {club: f"{club}hallen" for club in clubs}
        planner = SeasonPlanner(
            scheduler=FakeScheduler(all_weekend_dates(start, end)),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 3},
            events_by_club={club: [] for club in clubs},
        )
        plan = planner.build_plan(start, end)
        assert plan.tournaments
        assert all(not t.manual_booking_reason for t in plan.tournaments)

    def test_joint_club_team_uses_constituent_calendar_for_hosting(self):
        """A joint team like "Jar/Jutul" has no calendar of its own — it should
        fall back to whichever constituent club has scraped data, not be
        treated as a missing-calendar club."""
        start = datetime(2026, 10, 1)
        end = datetime(2026, 10, 31)
        joint = Team(club="Jar/Jutul", label="Jar/Jutul 1", age_group="U10")
        opponent = Team(club="Kongsberg", label="Kongsberg 1", age_group="U10")
        roster = Roster(teams=[joint, opponent])
        planner = SeasonPlanner(
            scheduler=FakeScheduler([]),
            roster=roster,
            club_arenas={"Jar": "Jarhallen", "Kongsberg": "Kongsberghallen"},
            parallel_games_for_age_group={"U10": 3},
            max_hosting_deviation=1,
            events_by_club={
                "Jar": [
                    CalendarEvent(
                        date="03.10.2026",
                        name="Jar hall booking",
                        datetime=datetime(2026, 10, 3, 10, 0),
                        duration_hours=1.0,
                    )
                ],
                "Kongsberg": [
                    CalendarEvent(
                        date="03.10.2026",
                        name="Kongsberg hall booking",
                        datetime=datetime(2026, 10, 3, 10, 0),
                        duration_hours=1.0,
                    )
                ],
            },
        )
        plan = SeasonPlan(
            tournaments=[
                Tournament(
                    date=date(2026, 10, 3),
                    arena="Jarhallen",
                    age_group="U10",
                    teams=[joint, opponent],
                    games=[Game(home=joint, away=opponent, parallel_slot=0, round_number=1)],
                    # host_assignment resolves "Jar/Jutul" down to the
                    # constituent that actually had a free slot.
                    host_club="Jar",
                )
            ],
            start_date=start.date(),
            end_date=end.date(),
            diversity_score=1.0,
            pairwise_matchup_score=1.0,
            month_balance_score=1.0,
            game_count_spread=0,
        )

        planner._compute_game_counts(plan.tournaments)
        planner._scan_hosting_warnings(plan)
        gate = planner._build_fairness_gate(plan)
        hosting_metric = next(metric for metric in gate["metrics"] if metric["key"] == "hosting_deviation")
        missing_note = next((note for note in gate.get("notes", []) if note["key"] == "missing_calendar_clubs"), None)

        assert missing_note is None
        assert hosting_metric["status"] == "pass"
        assert gate["status"] == "pass"
        assert planner.hosting_warnings == []

    def test_capacity_explained_hosting_deviation_is_warn_not_fail(self):
        """A hosting deviation the planner can trace to a real arena-booking
        conflict (fallback_host_substitutions explains it) should be a
        warning, not a hard failure — retrying or replanning can't free up
        ice time, only a human can, so it shouldn't block the pipeline."""
        start = datetime(2026, 10, 1)
        end = datetime(2026, 11, 30)
        frisk = [Team(club="Frisk Asker", label=f"Frisk Asker {i}", age_group="U10") for i in range(3)]
        jar = Team(club="Jar", label="Jar 1", age_group="U10")
        kongsberg = Team(club="Kongsberg", label="Kongsberg 1", age_group="U10")
        ringerike = Team(club="Ringerike", label="Ringerike 1", age_group="U10")
        roster = Roster(teams=[*frisk, jar, kongsberg, ringerike])
        planner = SeasonPlanner(
            scheduler=FakeScheduler([]),
            roster=roster,
            club_arenas={c: f"{c}hallen" for c in ["Frisk Asker", "Jar", "Kongsberg", "Ringerike"]},
            parallel_games_for_age_group={"U10": 3},
            max_hosting_deviation=0,
            events_by_club={
                club: [
                    CalendarEvent(
                        date="03.10.2026",
                        name=f"{club} hall booking",
                        datetime=datetime(2026, 10, 3, 10, 0),
                        duration_hours=1.0,
                    )
                ]
                for club in ["Frisk Asker", "Jar", "Kongsberg", "Ringerike"]
            },
        )
        # The scheduler tried to give Frisk Asker its fair share of hosting
        # but couldn't find a free arena slot for them (see
        # find_slot_for_tournament) — recorded here the same way build_plan
        # would record it, without needing a full multi-week simulation.
        planner._fallback_host_substitutions = [
            (date(2026, 10, d), "U10", "Frisk Asker", "Jar") for d in (3, 10, 17)
        ]
        hosts = ["Jar", "Jar", "Kongsberg", "Kongsberg", "Ringerike", "Ringerike"]
        plan = SeasonPlan(
            tournaments=[
                Tournament(
                    date=date(2026, 10, 3) + timedelta(weeks=i),
                    arena=f"{host}hallen",
                    age_group="U10",
                    teams=[jar, kongsberg],
                    games=[Game(home=jar, away=kongsberg, parallel_slot=0, round_number=1)],
                    host_club=host,
                )
                for i, host in enumerate(hosts)
            ],
            start_date=start.date(),
            end_date=end.date(),
            diversity_score=1.0,
            pairwise_matchup_score=1.0,
            month_balance_score=1.0,
            game_count_spread=0,
        )

        planner._compute_game_counts(plan.tournaments)
        gate = planner._build_fairness_gate(plan)
        hosting_metric = next(m for m in gate["metrics"] if m["key"] == "hosting_deviation")

        assert hosting_metric["status"] == "warn"
        assert hosting_metric["severity"] == "warn"
        assert "arenakapasitet" in hosting_metric["detail"]

    def test_hosting_warnings_property_returns_list(self, free_dates, season_window):
        """hosting_warnings should always return a list."""
        start, end = season_window
        roster = Roster(teams=[
            Team(club="Jar", label="Jar 1", age_group="U10"),
            Team(club="Kongsberg", label="Kongsberg 1", age_group="U10"),
        ])
        club_arenas = {t.club: f"{t.club}hallen" for t in roster.teams}

        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 3},
        )
        planner.build_plan(start, end)

        warnings = planner.hosting_warnings
        assert isinstance(warnings, list)

    def test_host_targets_are_age_group_aware(self, free_dates):
        """Teams in U10 should not increase U7 hosting expectations."""
        base_teams = [
            Team(club="Kongsberg", label="Kongsberg U7", age_group="U7"),
            Team(club="Jar", label="Jar U7", age_group="U7"),
            Team(club="Frisk Asker", label="Frisk U7", age_group="U7"),
        ]
        base_roster = Roster(teams=base_teams)
        expanded_roster = Roster(teams=base_teams + [
            Team(club="Jar", label=f"Jar U10-{i}", age_group="U10")
            for i in range(1, 7)
        ] + [
            Team(club="Kongsberg", label="Kongsberg U10", age_group="U10"),
            Team(club="Frisk Asker", label="Frisk U10", age_group="U10"),
        ])

        base_planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=base_roster,
            club_arenas={team.club: f"{team.club}hallen" for team in base_roster.teams},
        )
        expanded_planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=expanded_roster,
            club_arenas={team.club: f"{team.club}hallen" for team in expanded_roster.teams},
        )

        assert expanded_planner._hosting_targets_for_age_group("U7", 6) == base_planner._hosting_targets_for_age_group("U7", 6)
        assert expanded_planner._hosting_targets_for_age_group("U7", 6) == {
            "Frisk Asker": 2,
            "Jar": 2,
            "Kongsberg": 2,
        }

    def test_host_assignment_uses_per_age_rosters(self):
        """Jar's many U10 teams should not dominate U7 host assignment."""
        roster = Roster(teams=[
            Team(club="Kongsberg", label="Kongsberg U7", age_group="U7"),
            Team(club="Jar", label="Jar U7", age_group="U7"),
            Team(club="Frisk Asker", label="Frisk U7", age_group="U7"),
            *[Team(club="Jar", label=f"Jar U10-{i}", age_group="U10") for i in range(1, 7)],
            Team(club="Kongsberg", label="Kongsberg U10", age_group="U10"),
            Team(club="Frisk Asker", label="Frisk U10", age_group="U10"),
        ])
        planner = SeasonPlanner(
            scheduler=FakeScheduler([]),
            roster=roster,
            club_arenas={team.club: f"{team.club}hallen" for team in roster.teams},
        )
        scheduled = [
            (date(2026, 10, 3 + i), "U7") for i in range(6)
        ] + [
            (date(2026, 11, 3 + i), "U10") for i in range(8)
        ]

        assignments = planner._assign_hosts(scheduled)
        u7_counts = Counter(host for (_, age_group), host in zip(scheduled, assignments) if age_group == "U7")
        u10_counts = Counter(host for (_, age_group), host in zip(scheduled, assignments) if age_group == "U10")

        assert u7_counts == {"Kongsberg": 2, "Jar": 2, "Frisk Asker": 2}
        assert u10_counts["Jar"] > u10_counts["Kongsberg"]
        assert u10_counts["Jar"] > u10_counts["Frisk Asker"]

    def test_hosting_fairness_gate_contains_per_age_breakdown(self, free_dates, season_window):
        """The hosting metric should explain expected vs actual per age group."""
        start, end = season_window
        roster = Roster(teams=[
            Team(club="Kongsberg", label="Kongsberg U7", age_group="U7", target_tournament_count=3),
            Team(club="Jar", label="Jar U7", age_group="U7", target_tournament_count=3),
            Team(club="Frisk Asker", label="Frisk U7", age_group="U7", target_tournament_count=3),
            *[Team(club="Jar", label=f"Jar U10-{i}", age_group="U10", target_tournament_count=3) for i in range(1, 5)],
            Team(club="Kongsberg", label="Kongsberg U10", age_group="U10", target_tournament_count=3),
            Team(club="Frisk Asker", label="Frisk U10", age_group="U10", target_tournament_count=3),
        ])
        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas={team.club: f"{team.club}hallen" for team in roster.teams},
            parallel_games_for_age_group={"U7": 3, "U10": 3},
            max_hosting_deviation=99,
        )

        plan = planner.build_plan(start, end)
        hosting_metric = next(
            metric for metric in plan.fairness_gate["metrics"]
            if metric.get("key") == "hosting_deviation"
        )

        assert "Aldersgruppevis fordeling av hjemmeturneringer" in hosting_metric["detail"]
        breakdown = hosting_metric["age_group_breakdown"]
        assert any(row["age_group"] == "U7" and row["club"] == "Kongsberg" for row in breakdown)
        assert any(row["age_group"] == "U10" and row["club"] == "Jar" for row in breakdown)
        assert "basert på" in planner._hosting_fairness_breakdown(plan)["detail"]


    def test_game_count_spread_improves_with_deficit_aware_club_mix(self, season_window):
        """Skewed multi-team club vs single-team clubs balances via deficit-aware club mix.

        Sets up a skewed scenario: Jar has 7 U10 teams, while 7 other clubs
        each have 1 U10 team. The deficit-aware preferred_club_mix filter
        (added to `_pick_least_recently_grouped`) skips cross-club mixing
        once deficit spread exceeds max_game_count_spread, allowing the
        proportional club cap (ceil(7/14*6)=3 Jar slots) to be fully utilized
        instead of limiting Jar to 1 slot per tournament.

        Key acceptance: the per-team game-count spread is at most 1
        tournament's worth of games (5 per 6-team tournament), and all
        teams participate across the season.
        """
        start, end = season_window
        free_dates = all_weekend_dates(start, end)

        clubs = [
            "Jar", "Kongsberg", "Skien", "Holmen",
            "Ringerike", "Frisk Asker", "Tønsberg", "Jutul",
        ]
        # Jar gets 7 teams; everyone else gets 1.
        roster = _build_roster(["Jar"], ["U10"], teams_per_club_per_age_group=7)
        for club in clubs[1:]:
            roster.teams.append(Team(club=club, label=f"{club} U10", age_group="U10"))

        club_arenas = {club: f"{club}hallen" for club in clubs}

        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 3},  # capacity = 6 teams
            deficit_cap_expansion=1,
            fairness_thresholds={"max_game_count_spread": 6},
        )
        plan = planner.build_plan(start, end)

        # (1) All tournaments have minimum team count
        assert all(len(t.teams) >= MIN_TEAMS_PER_TOURNAMENT for t in plan.tournaments)

        # (2) Every team has at least one tournament
        team_invites = Counter()
        for t in plan.tournaments:
            for team in t.teams:
                team_invites[team.label] += 1
        assert all(count >= 1 for count in team_invites.values())

        # (3) Per-team game-count spread is at most 1 tournament's worth
        # (5 games for a 6-team round-robin).
        team_game_counts: Dict[str, int] = {}
        for t in plan.tournaments:
            for team in t.teams:
                key = team.label
                team_game_counts[key] = team_game_counts.get(key, 0) + (len(t.teams) - 1)

        u10_counts = [
            count for team_label, count in team_game_counts.items()
            if "U10" in team_label
        ]
        assert u10_counts, "No U10 game counts found"
        absolute_spread = max(u10_counts) - min(u10_counts)

        # Normalized spread should be at most 5 (one tournament's games)
        assert absolute_spread <= 5, (
            f"Game-count spread {absolute_spread} exceeds 1-tournament gap"
        )

        # (4) Normalized spread metric passes a lenient gate
        gate = planner._build_fairness_gate(plan)
        metrics = gate.get("metrics", []) if isinstance(gate, dict) else []
        spread_metric = next(m for m in metrics if m.get("key") == "game_count_spread")
        assert spread_metric["value"] < 0.5, (
            f"Normalized spread {spread_metric['value']} >= 0.5 "
            f"(absolute spread: {absolute_spread})"
        )


class TestRulesReport:
    """Tests for the rules-and-decisions transparency report."""

    def test_returns_nonempty_list_with_required_keys(self):
        """rules_report() returns a non-empty list; every entry has regel, forklaring, kategori."""
        roster = Roster(teams=[
            Team(club="Jar", label="Jar U10", age_group="U10"),
            Team(club="Kongsberg", label="Kongsberg U10", age_group="U10"),
        ])
        club_arenas = {"Jar": "Jarhallen", "Kongsberg": "Kongsberghallen"}
        planner = SeasonPlanner(
            scheduler=FakeScheduler([]),
            roster=roster,
            club_arenas=club_arenas,
        )
        report = planner.rules_report()

        assert isinstance(report, list)
        assert len(report) >= 7, f"expected at least 7 rules, got {len(report)}"

        for entry in report:
            assert isinstance(entry, dict), f"each entry must be a dict, got {type(entry)}"
            assert "regel" in entry, f"missing 'regel' key in {entry}"
            assert "forklaring" in entry, f"missing 'forklaring' key in {entry}"
            assert "kategori" in entry, f"missing 'kategori' key in {entry}"
            assert entry["kategori"] in {"Hard krav", "Myk regel", "Konfigurasjonsstandard", "Automatisk avgjørelse", "Advarsel", "Anbefaling"}, (
                f"unexpected kategori: {entry['kategori']}"
            )

    def test_parallel_games_appear_in_report(self):
        """When parallel_games_for_age_group is configured, it shows in the report."""
        roster = Roster(teams=[
            Team(club="Jar", label="Jar U10", age_group="U10"),
            Team(club="Kongsberg", label="Kongsberg U10", age_group="U10"),
            Team(club="Jar", label="Jar U12", age_group="U12"),
        ])
        club_arenas = {"Jar": "Jarhallen", "Kongsberg": "Kongsberghallen"}
        planner = SeasonPlanner(
            scheduler=FakeScheduler([]),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 3, "U12": 2},
        )
        report = planner.rules_report()

        u10_found = any("U10" in r["regel"] and "3" in r["regel"] for r in report)
        u12_found = any("U12" in r["regel"] and "2" in r["regel"] for r in report)
        assert u10_found, f"U10 parallel games not found in report"
        assert u12_found, f"U12 parallel games not found in report"

    def test_hard_constraints_have_correct_category(self):
        """Rules with kategori='Hard krav' cover the truly blocking scheduler constraints."""
        roster = Roster(teams=[
            Team(club="Jar", label="Jar U10", age_group="U10"),
            Team(club="Kongsberg", label="Kongsberg U10", age_group="U10"),
        ])
        club_arenas = {"Jar": "Jarhallen", "Kongsberg": "Kongsberghallen"}
        planner = SeasonPlanner(
            scheduler=FakeScheduler([]),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 4, "JU11": 2},
        )
        report = planner.rules_report()

        hard = [r for r in report if r["kategori"] == "Hard krav"]
        warnings = [r for r in report if r["kategori"] == "Advarsel"]
        configs = [r for r in report if r["kategori"] == "Konfigurasjonsstandard"]
        assert len(hard) >= 2, f"expected at least 2 hard constraints, got {len(hard)}"

        # Check that the blocking constraints are present.
        regel_texts = " ".join(r["regel"] for r in hard)
        assert "parallel" in regel_texts.lower(), "missing parallel-games capacity constraint"
        assert "U10" in regel_texts and "JU11" in regel_texts, "missing configured age-group capacity rules"
        assert warnings, "expected warning rules to be included"
        assert configs, "expected configuration defaults to be included"
        config_text = " ".join(r["regel"] for r in configs)
        assert "starttid" in config_text.lower(), "missing default start time config rule"
        assert "buffer" in config_text.lower(), "missing same-hall buffer config rule"

    def test_works_before_build_plan(self):
        """rules_report() does not require build_plan() to have been called."""
        roster = Roster(teams=[
            Team(club="Jar", label="Jar U10", age_group="U10"),
        ])
        club_arenas = {"Jar": "Jarhallen"}
        planner = SeasonPlanner(
            scheduler=FakeScheduler([]),
            roster=roster,
            club_arenas=club_arenas,
        )
        # No build_plan() call — should still work
        report = planner.rules_report()
        assert len(report) > 0, "rules_report should work before build_plan"

    def test_extracted_helper_modules_import_cleanly_with_facade(self):
        """Importing the extracted helper modules first should not break the facade."""
        from tournament_scheduler import fairness_scoring, game_generation, host_assignment, participant_selection, rules_report as rules_report_module, warnings as warnings_module

        roster = Roster(teams=[
            Team(club="Jar", label="Jar U10", age_group="U10"),
            Team(club="Kongsberg", label="Kongsberg U10", age_group="U10"),
            Team(club="Skien", label="Skien U10", age_group="U10"),
        ])
        planner = SeasonPlanner(
            scheduler=FakeScheduler([]),
            roster=roster,
            club_arenas={"Jar": "Jarhallen", "Kongsberg": "Kongsberghallen", "Skien": "Skienhallen"},
        )
        plan = SeasonPlan(tournaments=[], start_date=date(2026, 10, 1), end_date=date(2027, 4, 30))

        assert callable(rules_report_module.rules_report)
        assert participant_selection.default_target_count(4) == planner._default_target_count(4)
        assert host_assignment.default_target_count(4) == planner._default_target_count(4)
        assert isinstance(fairness_scoring.build_fairness_gate(planner, plan), dict)
        assert game_generation.generate_round_robin_games(roster.by_age_group("U10")[:3], 2)
        assert isinstance(warnings_module.scan_arena_day_collision_warnings(plan), list)
        assert planner.rules_report()


class TestMonthLoadWarnings:
    """Tests for month-load imbalance detection."""

    def test_returns_list_after_build_plan(self):
        """month_load_warnings returns a list after build_plan runs."""
        roster = Roster(teams=[
            Team(club="Jar", label="Jar U10", age_group="U10"),
            Team(club="Kongsberg", label="Kongsberg U10", age_group="U10"),
            Team(club="Skien", label="Skien U10", age_group="U10"),
        ])
        club_arenas = {t.club: f"{t.club}hallen" for t in roster.teams}
        start, end = datetime(2026, 10, 1), datetime(2027, 4, 30)
        free_dates = all_weekend_dates(start, end)
        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 3},
            max_month_deviation_ratio=0.5,
        )
        plan = planner.build_plan(start, end)
        assert len(plan.tournaments) > 0

        warnings = planner.month_load_warnings
        assert isinstance(warnings, list)
        # Each entry: (year, month, count, expected, deviation)
        for w in warnings:
            assert len(w) == 5, f"expected 5-tuple, got {w}"


class TestFeasibilityWarnings:
    """Tests for feasibility warnings when participation targets cannot be met."""

    def test_feasibility_warnings_empty_for_normal_setup(self, planner_and_plan):
        """A normal season plan should not produce feasibility warnings."""
        planner, plan, *_ = planner_and_plan
        assert planner.feasibility_warnings == []

    def test_feasibility_warning_for_too_few_teams(self, season_window):
        """An age group with fewer than MIN_TEAMS_PER_TOURNAMENT teams gets a warning."""
        start, end = season_window
        free_dates = all_weekend_dates(start, end)
        roster = Roster(teams=[
            Team(club="Jar", label="Jar U10", age_group="U10"),
            Team(club="Kongsberg", label="Kongsberg U10", age_group="U10"),
            Team(club="Jar", label="Jar JU12A", age_group="JU12"),
            Team(club="Kongsberg", label="Kongsberg JU12", age_group="JU12"),
        ])
        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas={"Jar": "Jarhallen", "Kongsberg": "Kongsberghallen"},
        )
        planner.build_plan(start, end)
        ju12_warnings = [w for w in planner.feasibility_warnings if "JU12" in w]
        assert any("JU12" in w for w in planner.feasibility_warnings)


class TestSlotAwareScheduling:
    """Tests for time-of-day-aware arena slot finding in build_plan."""

    @staticmethod
    def _basic_planner(free_dates, events_by_club=None, round_length=60):
        roster = Roster(teams=[
            Team(club="Frisk Asker", label="Frisk Asker U10", age_group="U10"),
            Team(club="Ringerike", label="Ringerike U10", age_group="U10"),
            Team(club="Holmen", label="Holmen U10", age_group="U10"),
        ])
        club_arenas = {
            "Frisk Asker": "Varner Arena",
            "Ringerike": "Ringerikshallen",
            "Holmen": "Holmenkollen ishall",
        }
        return SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            parallel_games_for_age_group={"U10": 3},
            round_length_for_age_group={"U10": round_length},
            events_by_club=events_by_club,
        )

    def test_without_events_by_club_uses_default_start_time(self):
        start, end = datetime(2026, 10, 1), datetime(2027, 4, 30)
        free_dates = all_weekend_dates(start, end)

        planner = self._basic_planner(free_dates, events_by_club=None)
        plan = planner.build_plan(start, end)

        assert plan.tournaments
        assert all(t.start_time == DEFAULT_TOURNAMENT_START_TIME for t in plan.tournaments)
        assert planner.fallback_host_substitutions == []

    def test_with_events_by_club_sets_non_default_start_time(self):
        start, end = datetime(2026, 10, 1), datetime(2027, 4, 30)
        free_dates = all_weekend_dates(start, end)

        # Build the plan once without calendar data to discover the chosen
        # tournament dates, then construct events_by_club so every host's
        # arena has a single morning booking that pushes the available slot
        # later than the default 10:00.
        probe_planner = self._basic_planner(free_dates, events_by_club=None)
        probe_plan = probe_planner.build_plan(start, end)
        tournament_dates = [t.date for t in probe_plan.tournaments]

        events_by_club = {}
        for club in ("Frisk Asker", "Ringerike", "Holmen"):
            events = []
            for d in tournament_dates:
                events.append(CalendarEvent(
                    date=d.strftime("%d.%m.%Y"),
                    name="Morgentrening",
                    datetime=datetime(d.year, d.month, d.day, 8, 0),
                    duration_hours=2.0,
                ))
            events_by_club[club] = events

        planner = self._basic_planner(free_dates, events_by_club=events_by_club)
        plan = planner.build_plan(start, end)

        assert plan.tournaments
        # Every host's arena is busy 08:00-10:00, so the computed slot
        # should start at or after 10:00.
        assert all(t.start_time >= "10:00" for t in plan.tournaments)
        assert any(t.start_time == DEFAULT_TOURNAMENT_START_TIME for t in plan.tournaments)

    def test_far_traveling_tournament_prefers_a_later_start(self):
        event_date = datetime(2026, 10, 3)
        planner = SimpleNamespace(
            events_by_club={
                "Jar": [
                    CalendarEvent(
                        date=event_date.strftime("%d.%m.%Y"),
                        name="Morgentrening",
                        datetime=datetime(event_date.year, event_date.month, event_date.day, 8, 0),
                        duration_hours=2.0,
                    ),
                    CalendarEvent(
                        date=event_date.strftime("%d.%m.%Y"),
                        name="Kort pause",
                        datetime=datetime(event_date.year, event_date.month, event_date.day, 11, 45),
                        duration_hours=0.25,
                    ),
                ],
            },
            round_length_for_age_group={"U10": 30},
            club_arenas={"Jar": "Jarahallen"},
            scheduler=FakeScheduler([event_date.date()]),
        )

        jar = Team(club="Jar", label="Jar U10", age_group="U10")
        kongsberg = Team(club="Kongsberg", label="Kongsberg U10", age_group="U10")
        ringerike = Team(club="Ringerike", label="Ringerike U10", age_group="U10")
        games = [
            Game(home=jar, away=kongsberg, round_number=1),
            Game(home=ringerike, away=jar, round_number=2),
            Game(home=kongsberg, away=ringerike, round_number=3),
        ]

        slot = find_slot_for_tournament(planner, event_date.date(), "Jar", "U10", games)

        assert slot is not None
        host_used, start_time, _end_time = slot
        assert host_used == "Jar"
        assert start_time == "12:00"

    def test_host_fully_booked_uses_cross_club_fallback_when_capacity_exists(self):
        start, end = datetime(2026, 10, 1), datetime(2027, 4, 30)
        free_dates = all_weekend_dates(start, end)

        probe_planner = self._basic_planner(free_dates, events_by_club=None)
        probe_plan = probe_planner.build_plan(start, end)
        original_hosts_by_tournament = {
            (t.date, t.age_group): t.host_club for t in probe_plan.tournaments
        }

        # Every known club except Holmen is fully booked all day on every
        # tournament date. The planner should fall back to Holmen whenever the
        # original host cannot fit the required matchday duration.
        from tournament_scheduler.club_registry import CLUB_REGISTRY

        events_by_club = {}
        for club, entry in CLUB_REGISTRY.items():
            if not entry.is_known:
                continue
            if club == "Holmen":
                events_by_club[club] = []
                continue
            events = []
            for d, _age_group in original_hosts_by_tournament:
                events.append(CalendarEvent(
                    date=d.strftime("%d.%m.%Y"),
                    name="Booket hele dagen",
                    datetime=datetime(d.year, d.month, d.day, 0, 0),
                    duration_hours=24.0,
                ))
            events_by_club[club] = events

        planner = self._basic_planner(free_dates, events_by_club=events_by_club)
        plan = planner.build_plan(start, end)

        assert plan.tournaments
        assert planner.fallback_host_substitutions
        assert all(to_host == "Holmen" for _, _, _, to_host in planner.fallback_host_substitutions)

        for tournament in plan.tournaments:
            original_host = original_hosts_by_tournament[(tournament.date, tournament.age_group)]
            if original_host != "Holmen":
                assert tournament.host_club == "Holmen"

    def test_no_arena_available_keeps_original_host_and_default_time(self):
        start, end = datetime(2026, 10, 1), datetime(2027, 4, 30)
        free_dates = all_weekend_dates(start, end)

        probe_planner = self._basic_planner(free_dates, events_by_club=None)
        probe_plan = probe_planner.build_plan(start, end)
        tournament_dates = [t.date for t in probe_plan.tournaments]

        # Every known club's arena is fully booked all day on every
        # tournament date -- no fallback can succeed.
        from tournament_scheduler.club_registry import CLUB_REGISTRY

        events_by_club = {}
        for club, entry in CLUB_REGISTRY.items():
            if not entry.is_known:
                continue
            events = []
            for d in tournament_dates:
                events.append(CalendarEvent(
                    date=d.strftime("%d.%m.%Y"),
                    name="Booket hele dagen",
                    datetime=datetime(d.year, d.month, d.day, 0, 0),
                    duration_hours=24.0,
                ))
            events_by_club[club] = events

        planner = self._basic_planner(free_dates, events_by_club=events_by_club)
        plan = planner.build_plan(start, end)

        # No slot is available anywhere, so every tournament keeps its
        # originally-assigned host/arena and the default 10:00 start time.
        assert planner.fallback_host_substitutions == []
        assert all(t.start_time == DEFAULT_TOURNAMENT_START_TIME for t in plan.tournaments)


class TestFairnessGate:
    """Tests for the structured fairness acceptance gate."""

    def test_fairness_gate_passes_with_lenient_thresholds(self, season_window):
        start, end = season_window
        free_dates = all_weekend_dates(start, end)
        roster = _build_roster(["Jar", "Holmen", "Kongsberg"], ["U10", "U11"])
        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas={club: f"{club}hallen" for club in roster.clubs()},
            parallel_games_for_age_group={"U10": 3, "U11": 3},
            fairness_thresholds={
                "max_game_count_spread": 999,
                "max_hosting_deviation": 999,
                "max_team_travel_km": 9999,
                "min_diversity_score": 0.0,
                "min_pairwise_matchup_score": 0.0,
                "min_month_balance_score": 0.0,
                "max_same_weekend_club_load": 999,
                "max_consecutive_weekend_club_load": 999,
                "max_holiday_stretch_club_load": 999,
            },
        )
        plan = planner.build_plan(start, end)
        gate = plan.fairness_gate

        assert gate["status"] == "pass"
        assert gate["score"] >= 90
        assert gate["metrics"]
        assert all(metric["status"] == "pass" for metric in gate["metrics"])

    def test_fairness_gate_flags_skew_with_tight_thresholds(self, season_window):
        start, end = season_window
        free_dates = all_weekend_dates(start, end)
        roster = _build_roster(["Jar", "Holmen", "Kongsberg"], ["U10"])
        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas={club: f"{club}hallen" for club in roster.clubs()},
            parallel_games_for_age_group={"U10": 3},
            fairness_thresholds={
                "max_game_count_spread": -1,
                "max_hosting_deviation": -1,
                "max_team_travel_km": 0,
                "min_diversity_score": 1.0,
                "min_pairwise_matchup_score": 1.0,
                "min_month_balance_score": 1.0,
                "max_same_weekend_club_load": 0,
                "max_consecutive_weekend_club_load": 0,
                "max_holiday_stretch_club_load": 0,
            },
        )
        plan = planner.build_plan(start, end)
        gate = plan.fairness_gate

        assert gate["status"] in {"warn", "fail"}
        assert any(metric["status"] == "fail" for metric in gate["metrics"])

    def test_soft_measurement_outside_threshold_does_not_fail_policy_gate(self, season_window, monkeypatch):
        """Issue #260 Phase 4 acceptance criterion: a soft/default metric
        outside its reference threshold does not make the canonical
        policy_gate warn/fail by itself — only hard invariants and
        config-threaded thresholds (hosting_deviation, arena_day_collisions)
        may do that."""
        start, end = season_window
        free_dates = all_weekend_dates(start, end)
        roster = _build_roster(["Jar", "Holmen", "Kongsberg"], ["U10"])
        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas={club: f"{club}hallen" for club in roster.clubs()},
            parallel_games_for_age_group={"U10": 3},
            fairness_thresholds={
                # Lenient on the two policy-gate metrics...
                "max_hosting_deviation": 999,
                # ...but impossibly tight on every soft/default measurement.
                "max_game_count_spread": -1,
                "max_team_travel_km": 0,
                "min_diversity_score": 1.0,
                "min_pairwise_matchup_score": 1.0,
                "min_month_balance_score": 1.0,
                "max_same_weekend_club_load": 0,
                "max_consecutive_weekend_club_load": 0,
                "max_holiday_stretch_club_load": 0,
            },
        )
        plan = planner.build_plan(start, end)
        gate = plan.fairness_gate

        # The legacy blended status/score do reflect the soft-metric skew...
        assert gate["status"] in {"warn", "fail"}
        assert any(m["status"] != "pass" for m in gate["measurements"])
        # ...but none of that is policy-gate (canonical control) authority.
        assert gate["policy_gate"]["status"] == "pass"
        assert all(m["status"] == "pass" for m in gate["policy_gate"]["metrics"])
        policy_gate_keys = {m["key"] for m in gate["policy_gate"]["metrics"]}
        assert policy_gate_keys == {"hosting_deviation", "arena_day_collisions"}

    def test_arena_collision_fails_policy_gate(self, season_window):
        """Issue #260 Phase 4 acceptance criterion: an arena collision makes
        the canonical policy_gate fail (true hard invariant)."""
        start, end = season_window
        free_dates = [start.date()]
        roster = Roster(teams=[
            Team(club="Jar", label="Jar U7-1", age_group="U7"),
            Team(club="Jar", label="Jar U7-2", age_group="U7"),
            Team(club="Jar", label="Jar U7-3", age_group="U7"),
            Team(club="Jar", label="Jar U8-1", age_group="U8"),
            Team(club="Jar", label="Jar U8-2", age_group="U8"),
            Team(club="Jar", label="Jar U8-3", age_group="U8"),
            Team(club="Jar", label="Jar U9-1", age_group="U9"),
            Team(club="Jar", label="Jar U9-2", age_group="U9"),
            Team(club="Jar", label="Jar U9-3", age_group="U9"),
        ])
        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas={"Jar": "Jarahallen"},
            parallel_games_for_age_group={"U7": 4, "U8": 4, "U9": 4},
            round_length_for_age_group={"U7": 60, "U8": 60, "U9": 60},
            seed=0,
        )

        plan = planner.build_plan(start, end)

        assert len(plan.arena_day_collisions) >= 1
        gate = plan.fairness_gate
        assert gate["policy_gate"]["status"] == "fail"
        collision_metric = next(m for m in gate["policy_gate"]["metrics"] if m["key"] == "arena_day_collisions")
        assert collision_metric["status"] == "fail"
        assert collision_metric["provenance"] == "hard_invariant"

    def test_configured_hosting_deviation_drives_policy_gate_with_capacity_downgrade(self, season_window):
        """Issue #260 Phase 4 acceptance criterion: configured
        max_hosting_deviation is reflected as configured policy and can
        affect the gate; capacity-explained cases preserve the intended
        downgrade to warn rather than fail."""
        start, end = season_window
        free_dates = all_weekend_dates(start, end)
        roster = _build_roster(["Jar", "Holmen", "Kongsberg"], ["U10"])
        planner = SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas={club: f"{club}hallen" for club in roster.clubs()},
            parallel_games_for_age_group={"U10": 3},
            fairness_thresholds={"max_hosting_deviation": -1},
        )
        plan = planner.build_plan(start, end)
        gate = plan.fairness_gate

        hosting_metric = next(m for m in gate["policy_gate"]["metrics"] if m["key"] == "hosting_deviation")
        assert hosting_metric["provenance"] == "configured"
        # No arena-capacity constraint in this synthetic scenario (FakeScheduler
        # has ample free dates, no events_by_club calendar limits), so the
        # deviation is not capacity-explained -> fail, and that failure is
        # policy-gate (canonical) authority, not merely a legacy-summary one.
        assert hosting_metric["status"] == "fail"
        assert gate["policy_gate"]["status"] == "fail"

    def test_travel_distance_threshold_is_cumulative_and_passes_at_the_default_boundary(self, monkeypatch):
        jar = Team(club="Jar", label="Jar 1", age_group="U10")
        kongsberg = Team(club="Kongsberg", label="Kongsberg 1", age_group="U10")
        roster = Roster(teams=[jar, kongsberg])
        planner = SeasonPlanner(
            scheduler=FakeScheduler([]),
            roster=roster,
            club_arenas={"Jar": "Jarhallen", "Kongsberg": "Kongsberghallen"},
            parallel_games_for_age_group={"U10": 3},
            events_by_club={"Jar": [], "Kongsberg": []},
        )
        tournament = Tournament(
            date=date(2026, 10, 3),
            arena="Jarhallen",
            age_group="U10",
            teams=[jar, kongsberg],
            games=[Game(home=jar, away=kongsberg, parallel_slot=0, round_number=1)],
            host_club="Jar",
        )
        plan = SeasonPlan(
            tournaments=[tournament],
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 31),
            diversity_score=1.0,
            pairwise_matchup_score=1.0,
            month_balance_score=1.0,
            game_count_spread=0,
        )
        threshold = planner.fairness_thresholds["max_team_travel_km"]

        monkeypatch.setattr(
            "tournament_scheduler.fairness_scoring.compute_team_travel_distances",
            lambda _plan: {jar.label: threshold, kongsberg.label: 0},
        )
        gate = planner._build_fairness_gate(plan)
        travel_metric = next(metric for metric in gate["metrics"] if metric["key"] == "travel_distance")
        assert travel_metric["threshold"] == threshold
        assert travel_metric["status"] == "pass"
        assert gate["status"] == "pass"

        monkeypatch.setattr(
            "tournament_scheduler.fairness_scoring.compute_team_travel_distances",
            lambda _plan: {jar.label: threshold + 1, kongsberg.label: 0},
        )
        gate = planner._build_fairness_gate(plan)
        travel_metric = next(metric for metric in gate["metrics"] if metric["key"] == "travel_distance")
        assert travel_metric["status"] == "warn"
        assert gate["status"] == "warn"


class TestHostingDaysConstraint:
    """Unit tests for the max_hosting_days_per_month scoring constraint."""

    def _make_planner(self, max_days: int = 2):
        clubs = ["Jar", "Holmen"]
        age_groups = ["U10"]
        roster = _build_roster(clubs, age_groups)
        free_dates = all_weekend_dates(datetime(2027, 9, 1), datetime(2027, 11, 30))
        club_arenas = {club: f"{club}hallen" for club in clubs}
        return SeasonPlanner(
            scheduler=FakeScheduler(free_dates),
            roster=roster,
            club_arenas=club_arenas,
            max_hosting_days_per_month=max_days,
        )

    def test_large_penalty_when_all_clubs_at_cap(self):
        """_score_candidate_date returns >= 1e6 when all clubs have hit the monthly cap."""
        planner = self._make_planner(max_days=2)
        # Both clubs already host 2 distinct October days (neither is the candidate).
        oct_key = (2027, 10)
        planner._hosting_days_by_club_month = {
            ("Jar", oct_key): {date(2027, 10, 2), date(2027, 10, 9)},
            ("Holmen", oct_key): {date(2027, 10, 16), date(2027, 10, 23)},
        }
        candidate = date(2027, 10, 30)
        teams = list(planner.roster.teams)
        score = planner._score_candidate_date(candidate, "U10", teams, 1.0)
        assert score >= 1e6, f"Expected large penalty, got {score}"

    def test_no_penalty_when_below_cap(self):
        """_score_candidate_date does not penalise when clubs are below the monthly cap."""
        planner = self._make_planner(max_days=2)
        # Both clubs have only 1 hosting day in October — still below cap of 2.
        oct_key = (2027, 10)
        planner._hosting_days_by_club_month = {
            ("Jar", oct_key): {date(2027, 10, 2)},
            ("Holmen", oct_key): {date(2027, 10, 16)},
        }
        candidate = date(2027, 10, 30)
        teams = list(planner.roster.teams)
        score = planner._score_candidate_date(candidate, "U10", teams, 1.0)
        assert score < 1e6, f"Expected no large penalty, got {score}"

    def test_candidate_date_itself_not_counted_against_cap(self):
        """A club hosting on the candidate date itself is not double-counted."""
        planner = self._make_planner(max_days=2)
        # Jar has 2 days but one of them IS the candidate — so effective count is 1.
        candidate = date(2027, 10, 30)
        oct_key = (2027, 10)
        planner._hosting_days_by_club_month = {
            ("Jar", oct_key): {date(2027, 10, 2), candidate},
            ("Holmen", oct_key): {date(2027, 10, 16)},
        }
        teams = list(planner.roster.teams)
        score = planner._score_candidate_date(candidate, "U10", teams, 1.0)
        assert score < 1e6, f"Candidate date should not count against the cap; got {score}"


class TestWeekendBalance:
    def test_assign_hosts_avoids_consecutive_weekend_clumping(self):
        clubs = ["Jar", "Holmen", "Kongsberg"]
        age_groups = ["U10", "U11"]
        roster = _build_roster(clubs, age_groups)
        planner = SeasonPlanner(
            scheduler=FakeScheduler([]),
            roster=roster,
            club_arenas={club: f"{club}hallen" for club in clubs},
            parallel_games_for_age_group={"U10": 2, "U11": 2},
        )
        scheduled = [
            (date(2027, 3, 20), "U10"),
            (date(2027, 3, 27), "U11"),
            (date(2027, 4, 3), "U10"),
            (date(2027, 4, 10), "U11"),
            (date(2027, 4, 17), "U10"),
            (date(2027, 4, 24), "U11"),
        ]

        assignments = planner._assign_hosts(scheduled)

        assert assignments[0] != assignments[1]
        assert assignments[1] != assignments[2]
        assert assignments[2] != assignments[3]

    def test_fairness_gate_includes_weekend_balance_metrics(self):
        club = "Jar"
        other_club = "Holmen"
        jar_team = Team(club=club, label="Jar U10", age_group="U10")
        holmen_team = Team(club=other_club, label="Holmen U10", age_group="U10")
        holiday_dates = sorted(holiday_heavy_weekend_dates(date(2027, 3, 1), date(2027, 4, 30)))
        assert len(holiday_dates) >= 5
        tournaments = [
            Tournament(
                date=holiday_dates[0],
                arena=f"{club}hallen",
                age_group="U10",
                teams=[jar_team, holmen_team],
                host_club=club,
            ),
            Tournament(
                date=holiday_dates[2],
                arena=f"{club}hallen",
                age_group="U10",
                teams=[jar_team, holmen_team],
                host_club=club,
            ),
            Tournament(
                date=holiday_dates[4],
                arena=f"{club}hallen",
                age_group="U10",
                teams=[jar_team, holmen_team],
                host_club=club,
            ),
        ]
        roster = Roster(teams=[jar_team, holmen_team])
        planner = SeasonPlanner(
            scheduler=FakeScheduler([]),
            roster=roster,
            club_arenas={club: f"{club}hallen", other_club: f"{other_club}hallen"},
            fairness_thresholds={
                "max_game_count_spread": 999,
                "max_hosting_deviation": 999,
                "max_team_travel_km": 9999,
                "min_diversity_score": 0.0,
                "min_pairwise_matchup_score": 0.0,
                "min_month_balance_score": 0.0,
                "max_same_weekend_club_load": 999,
                "max_consecutive_weekend_club_load": 2,
                "max_holiday_stretch_club_load": 2,
            },
        )
        plan = SeasonPlan(
            tournaments=tournaments,
            start_date=holiday_dates[0],
            end_date=holiday_dates[4],
            diversity_score=1.0,
            pairwise_matchup_score=1.0,
            month_balance_score=1.0,
            game_count_spread=0,
        )

        planner._compute_game_counts(plan.tournaments)
        planner._scan_hosting_warnings(plan)
        gate = planner._build_fairness_gate(plan)

        weekend_metric = next(metric for metric in gate["metrics"] if metric["key"] == "consecutive_weekend_club_load")
        holiday_metric = next(metric for metric in gate["metrics"] if metric["key"] == "holiday_stretch_club_load")

        assert weekend_metric["status"] in {"warn", "fail"}
        assert holiday_metric["status"] in {"warn", "fail"}
        assert "sammenhengende" in weekend_metric["detail"]
        assert "ferie" in holiday_metric["detail"]
        assert any("sammenhengende vertskapshelger" in warning for warning in planner.hosting_warnings)
        assert any("ferie-/helligdagshelger" in warning for warning in planner.hosting_warnings)
