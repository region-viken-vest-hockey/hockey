"""Tests for data models."""

from datetime import datetime, date
from tournament_scheduler.models import (
    CalendarEvent,
    TournamentInfo,
    ConflictContext,
    ConflictResult,
    SchedulingResult,
    Game,
    Roster,
    Team,
    Tournament,
)


class TestModels:
    """Test suite for data models."""

    def test_calendar_event_creation(self):
        """Test CalendarEvent creation."""
        event = CalendarEvent(
            date='17.01.2026',
            name='Test Tournament',
            datetime=datetime(2026, 1, 17, 14, 0),
            duration_hours=3.5
        )
        assert event.date == '17.01.2026'
        assert event.name == 'Test Tournament'
        assert event.duration_hours == 3.5

    def test_tournament_info_creation(self):
        """Test TournamentInfo creation."""
        info = TournamentInfo(
            date=date(2026, 1, 17),
            name='Winter Cup',
            teams=['Team A', 'Team B'],
            location='Kongsberg'
        )
        assert len(info.teams) == 2
        assert info.location == 'Kongsberg'

    def test_conflict_context_defaults(self):
        """Test ConflictContext with default values."""
        context = ConflictContext(
            start_date=datetime(2026, 1, 1),
            end_date=datetime(2026, 6, 30)
        )
        assert context.team_names == []
        assert context.calendar_events == []
        assert context.excel_dates == set()
        assert context.tournament_to_reschedule is None

    def test_conflict_result_creation(self):
        """Test ConflictResult creation."""
        result = ConflictResult(
            excluded_dates={date(2026, 1, 17)},
            reasons={date(2026, 1, 17): 'Ice hall conflict'},
            checker_name='IceHallChecker'
        )
        assert len(result.excluded_dates) == 1
        assert result.checker_name == 'IceHallChecker'

    def test_scheduling_result_creation(self):
        """Test SchedulingResult creation."""
        result = SchedulingResult(
            available_dates=[date(2026, 2, 14), date(2026, 2, 15)],
            excluded_dates=[date(2026, 1, 17)],
            exclusion_breakdown={'ice_hall': 1, 'team_conflict': 0},
            detailed_exclusions=[(date(2026, 1, 17), 'Ice hall: Tournament X')],
            total_weekends_checked=10
        )
        assert len(result.available_dates) == 2
        assert result.total_weekends_checked == 10


class TestTournamentDurationAndEndTime:
    """Tests for Tournament.duration_minutes and Tournament.end_time."""

    def _make_teams(self, n):
        return [Team(club=f"Club{i}", label=f"Team{i}", age_group="U10") for i in range(n)]

    def test_duration_minutes_with_no_games_is_zero(self):
        tournament = Tournament(date=date(2026, 1, 17), arena="Arena", age_group="U10")
        assert tournament.duration_minutes(round_length=10) == 0

    def test_duration_minutes_uses_max_round_number(self):
        teams = self._make_teams(4)
        games = [
            Game(home=teams[0], away=teams[1], round_number=1),
            Game(home=teams[2], away=teams[3], round_number=1),
            Game(home=teams[0], away=teams[2], round_number=2),
            Game(home=teams[1], away=teams[3], round_number=2),
            Game(home=teams[0], away=teams[3], round_number=3),
            Game(home=teams[1], away=teams[2], round_number=3),
        ]
        tournament = Tournament(date=date(2026, 1, 17), arena="Arena", age_group="U10", teams=teams, games=games)

        # 3 rounds * 10 minutes/round = 30 minutes total.
        assert tournament.duration_minutes(round_length=10) == 30

    def test_duration_minutes_scales_with_round_length(self):
        teams = self._make_teams(2)
        games = [Game(home=teams[0], away=teams[1], round_number=1)]
        tournament = Tournament(date=date(2026, 1, 17), arena="Arena", age_group="U12", teams=teams, games=games)

        assert tournament.duration_minutes(round_length=12) == 12
        assert tournament.duration_minutes(round_length=8) == 8

    def test_matchday_duration_minutes_includes_setup_buffer(self):
        teams = self._make_teams(4)
        games = [
            Game(home=teams[0], away=teams[1], round_number=1),
            Game(home=teams[2], away=teams[3], round_number=1),
            Game(home=teams[0], away=teams[2], round_number=2),
            Game(home=teams[1], away=teams[3], round_number=2),
            Game(home=teams[0], away=teams[3], round_number=3),
            Game(home=teams[1], away=teams[2], round_number=3),
        ]
        tournament = Tournament(date=date(2026, 1, 17), arena="Arena", age_group="U10", teams=teams, games=games)

        assert tournament.matchday_duration_minutes(round_length=10) == 45

    def test_end_time_computed_from_start_time_and_duration(self):
        teams = self._make_teams(4)
        games = [
            Game(home=teams[0], away=teams[1], round_number=1),
            Game(home=teams[2], away=teams[3], round_number=1),
            Game(home=teams[0], away=teams[2], round_number=2),
            Game(home=teams[1], away=teams[3], round_number=2),
        ]
        tournament = Tournament(
            date=date(2026, 1, 17),
            arena="Arena",
            age_group="U10",
            teams=teams,
            games=games,
            start_time="09:00",
        )

        # 2 rounds * 10 minutes/round = 20 minutes -> 09:00 + 20min = 09:20
        assert tournament.end_time(round_length=10) == "09:20"

    def test_end_time_rolls_over_to_next_hour(self):
        teams = self._make_teams(2)
        games = [Game(home=teams[0], away=teams[1], round_number=1)]
        tournament = Tournament(
            date=date(2026, 1, 17),
            arena="Arena",
            age_group="U12",
            teams=teams,
            games=games,
            start_time="09:50",
        )

        assert tournament.end_time(round_length=12) == "10:02"

    def test_end_time_is_none_when_start_time_unset(self):
        teams = self._make_teams(2)
        games = [Game(home=teams[0], away=teams[1], round_number=1)]
        tournament = Tournament(date=date(2026, 1, 17), arena="Arena", age_group="U10", teams=teams, games=games)

        # Backward-compatible: no start_time -> end_time is None regardless of round_length.
        assert tournament.start_time is None
        assert tournament.end_time(round_length=10) is None


class TestRosterAgeGroupSeparation:
    """U and JU are distinct exact-match categories, never mixed."""

    def test_by_age_group_never_mixes_u_and_ju(self):
        roster = Roster(teams=[
            Team(club="Jar", label="Jar 1", age_group="U10"),
            Team(club="Jar", label="Jar 1", age_group="JU10"),
            Team(club="Kongsberg", label="Kongsberg 1", age_group="U10"),
        ])

        u10 = roster.by_age_group("U10")
        ju10 = roster.by_age_group("JU10")

        assert {team.label for team in u10} == {"Jar 1", "Kongsberg 1"}
        assert all(team.age_group == "U10" for team in u10)
        assert [team.age_group for team in ju10] == ["JU10"]
