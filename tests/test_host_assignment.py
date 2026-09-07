"""Tests verifying that the home team in generated games is the arena-owning club.

These tests cover:
- club_for_arena reverse-lookup in the club registry
- The host-first participant ordering in game generation
- The end-to-end invariant: game.home.club == arena owner for all rounds
"""

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from tournament_scheduler.club_registry import club_for_arena
from tournament_scheduler.host_assignment import (
    assign_hosts,
    find_slot_for_tournament,
    hosting_targets_for_age_group,
)
from tournament_scheduler.models import Roster, Team
from tournament_scheduler.models import CalendarEvent, Team
from tournament_scheduler.scheduler import TournamentScheduler
from tournament_scheduler.season_planner import SeasonPlanner
from tournament_scheduler.utils.date_parser import DateParser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_teams(clubs, age_group="U10"):
    """Create one Team per club name."""
    return [
        Team(club=club, label=f"{club}-A", age_group=age_group)
        for club in clubs
    ]


def _host_first(participants, arena):
    """Reorder participants so the arena-owning club's team is first."""
    home_club = club_for_arena(arena)
    if home_club is None:
        return participants
    host_teams = [t for t in participants if t.club == home_club]
    other_teams = [t for t in participants if t.club != home_club]
    return (host_teams + other_teams) if host_teams else participants


# ---------------------------------------------------------------------------
# club_for_arena unit tests
# ---------------------------------------------------------------------------


class TestClubForArena:
    def test_varner_arena_maps_to_frisk_asker(self):
        assert club_for_arena("Varner Arena") == "Frisk Asker"

    def test_kongsberghallen_maps_to_kongsberg(self):
        assert club_for_arena("Kongsberghallen") == "Kongsberg"

    def test_case_insensitive_match(self):
        assert club_for_arena("varner arena") == "Frisk Asker"
        assert club_for_arena("KONGSBERGHALLEN") == "Kongsberg"

    def test_unknown_arena_returns_none(self):
        assert club_for_arena("Ukjent hall") is None

    def test_whitespace_stripped(self):
        assert club_for_arena("  Varner Arena  ") == "Frisk Asker"

    def test_all_registered_arenas_resolve(self):
        """Every arena in CLUB_REGISTRY should resolve back to its club."""
        from tournament_scheduler.club_registry import CLUB_REGISTRY
        for club_name, entry in CLUB_REGISTRY.items():
            result = club_for_arena(entry.arena)
            assert result == club_name, (
                f"club_for_arena('{entry.arena}') returned {result!r}, expected {club_name!r}"
            )


class TestFallbackHostSlotSearch:
    def test_uses_fallback_host_when_primary_arena_is_full(self):
        event_date = datetime(2026, 10, 3).date()
        planner = SimpleNamespace(
            events_by_club={
                "Jar": [
                    CalendarEvent(
                        date=event_date.strftime("%d.%m.%Y"),
                        name="Booket hele dagen",
                        datetime=datetime(event_date.year, event_date.month, event_date.day, 0, 0),
                        duration_hours=24.0,
                    )
                ],
                "Holmen": [],
            },
            round_length_for_age_group={"U10": 30},
            club_arenas={"Jar": "Jarahallen", "Holmen": "Holmenkollen ishall"},
            scheduler=TournamentScheduler([], [], DateParser()),
        )

        jar = Team(club="Jar", label="Jar U10", age_group="U10")
        holmen = Team(club="Holmen", label="Holmen U10", age_group="U10")
        kongsberg = Team(club="Kongsberg", label="Kongsberg U10", age_group="U10")
        games = SeasonPlanner.generate_round_robin_games([jar, holmen, kongsberg], parallel_games=2)

        slot = find_slot_for_tournament(
            planner,
            event_date,
            "Jar",
            "U10",
            games,
            candidate_hosts=["Holmen"],
        )

        assert slot is not None
        assert slot[0] == "Holmen"


class TestJointClubHostNames:
    """A joint-club team (e.g. 'Jar/Jutul') has no single physical arena —
    find_slot_for_tournament must try its constituent clubs' arenas instead
    of crashing on the unregistered combined name (regression for a real
    KeyError('Unknown club Jar/Jutul') crash in Stage 3 planning)."""

    def _planner(self, *, jar_events, jutul_events):
        return SimpleNamespace(
            events_by_club={"Jar": jar_events, "Jutul": jutul_events},
            round_length_for_age_group={"U10": 30},
            club_arenas={"Jar": "Jarhallen", "Jutul": "Bærum ishall"},
            scheduler=TournamentScheduler([], [], DateParser()),
        )

    def _games(self):
        jar = Team(club="Jar", label="Jar U10", age_group="U10")
        jutul = Team(club="Jutul", label="Jutul U10", age_group="U10")
        kongsberg = Team(club="Kongsberg", label="Kongsberg U10", age_group="U10")
        return SeasonPlanner.generate_round_robin_games([jar, jutul, kongsberg], parallel_games=2)

    def test_falls_back_to_jutul_when_jar_arena_is_full(self):
        event_date = datetime(2026, 10, 3).date()
        planner = self._planner(
            jar_events=[
                CalendarEvent(
                    date=event_date.strftime("%d.%m.%Y"),
                    name="Booket hele dagen",
                    datetime=datetime(event_date.year, event_date.month, event_date.day, 0, 0),
                    duration_hours=24.0,
                )
            ],
            jutul_events=[],
        )

        slot = find_slot_for_tournament(planner, event_date, "Jar/Jutul", "U10", self._games())

        assert slot is not None
        assert slot[0] == "Jutul"

    def test_does_not_crash_when_neither_constituent_has_a_slot(self):
        event_date = datetime(2026, 10, 3).date()
        full_day = CalendarEvent(
            date=event_date.strftime("%d.%m.%Y"),
            name="Booket hele dagen",
            datetime=datetime(event_date.year, event_date.month, event_date.day, 0, 0),
            duration_hours=24.0,
        )
        planner = self._planner(jar_events=[full_day], jutul_events=[full_day])

        # Must return None (no slot found), not raise.
        slot = find_slot_for_tournament(planner, event_date, "Jar/Jutul", "U10", self._games())
        assert slot is None


# ---------------------------------------------------------------------------
# Host-first participant ordering -> game.home invariant
# ---------------------------------------------------------------------------


class TestHomeTeamIsAlwaysHostClub:
    """When the arena-owning club's team is placed first in participants,
    it must appear as game.home in every game IT PLAYS (never as away)."""

    def test_frisk_asker_is_home_in_their_games_at_varner_arena(self):
        """Frisk Asker must appear as game.home in every game they play."""
        arena = "Varner Arena"
        clubs = ["Frisk Asker", "Kongsberg", "Skien", "Ringerike"]
        participants = _make_teams(clubs)
        ordered = _host_first(participants, arena)

        games = SeasonPlanner.generate_round_robin_games(ordered, parallel_games=2)

        assert len(games) > 0, "expected at least one game"
        # Every game involving Frisk Asker: they must be home, never away
        fa_games = [
            g for g in games
            if g.home.club == "Frisk Asker" or g.away.club == "Frisk Asker"
        ]
        assert len(fa_games) > 0, "Frisk Asker should play at least one game"
        for game in fa_games:
            assert game.home.club == "Frisk Asker", (
                f"Expected Frisk Asker as home in their game, got {game.home.club!r}"
            )

    def test_kongsberg_is_home_in_their_games_at_kongsberghallen(self):
        arena = "Kongsberghallen"
        clubs = ["Kongsberg", "Frisk Asker", "Jar", "Holmen"]
        participants = _make_teams(clubs)
        ordered = _host_first(participants, arena)

        games = SeasonPlanner.generate_round_robin_games(ordered, parallel_games=2)

        assert len(games) > 0
        kg_games = [
            g for g in games
            if g.home.club == "Kongsberg" or g.away.club == "Kongsberg"
        ]
        assert len(kg_games) > 0
        for game in kg_games:
            assert game.home.club == "Kongsberg", (
                f"Expected Kongsberg as home in their games, got {game.home.club!r}"
            )

    def test_host_first_ordering_is_applied_regardless_of_input_order(self):
        """Even if the host club is last in the original list, reordering places them first."""
        arena = "Varner Arena"
        # Deliberately put Frisk Asker last
        clubs = ["Kongsberg", "Skien", "Ringerike", "Frisk Asker"]
        participants = _make_teams(clubs)
        ordered = _host_first(participants, arena)

        assert ordered[0].club == "Frisk Asker", (
            "After reordering, Frisk Asker must be first in the list"
        )
        games = SeasonPlanner.generate_round_robin_games(ordered, parallel_games=2)
        fa_games = [g for g in games if g.home.club == "Frisk Asker" or g.away.club == "Frisk Asker"]
        for game in fa_games:
            assert game.home.club == "Frisk Asker"

    def test_host_games_home_across_all_rounds(self):
        """The host-as-home invariant holds across every round, not just round 1."""
        arena = "Varner Arena"
        clubs = ["Frisk Asker", "Kongsberg", "Skien", "Ringerike", "Jar"]
        participants = _make_teams(clubs)
        ordered = _host_first(participants, arena)

        games = SeasonPlanner.generate_round_robin_games(ordered, parallel_games=2)

        round_numbers = sorted({g.round_number for g in games})
        assert len(round_numbers) > 1, "expected multiple rounds"
        for rn in round_numbers:
            round_games = [g for g in games if g.round_number == rn]
            fa_round_games = [
                g for g in round_games
                if g.home.club == "Frisk Asker" or g.away.club == "Frisk Asker"
            ]
            for game in fa_round_games:
                assert game.home.club == "Frisk Asker", (
                    f"Round {rn}: Frisk Asker must be home in their game, got {game.home.club!r}"
                )

    def test_no_host_team_in_participants_does_not_crash(self):
        """When the host club has no team in the participant list, order is unchanged."""
        arena = "Varner Arena"
        clubs = ["Kongsberg", "Skien", "Ringerike"]
        participants = _make_teams(clubs)
        ordered = _host_first(participants, arena)

        # Order unchanged — no Frisk Asker team present
        assert [t.club for t in ordered] == [t.club for t in participants]
        games = SeasonPlanner.generate_round_robin_games(ordered, parallel_games=2)
        assert len(games) > 0


# ---------------------------------------------------------------------------
# Age-group-aware host assignment streak tests
# ---------------------------------------------------------------------------


class TestAgeGroupAwareHostStreak:
    """Verify consecutive-streak tracking is scoped per (club, age_group)."""

    def _make_planner(self, teams):
        roster = Roster(teams=teams)
        return SimpleNamespace(
            roster=roster,
            clubs=lambda: roster.clubs(),
            club_arenas={},
            _arena_day_collisions=[],
            round_length_for_age_group={},
        )

    def test_different_age_group_no_streak_penalty(self):
        """A club hosting U7 one weekend and U10 the next gets no streak penalty."""
        teams = [
            Team(club="Jar", label="Jar U10", age_group="U10"),
            Team(club="Jar", label="Jar U7", age_group="U7"),
            Team(club="Kongsberg", label="Kongsberg U10", age_group="U10"),
            Team(club="Kongsberg", label="Kongsberg U7", age_group="U7"),
        ]
        planner = self._make_planner(teams)

        # Jar hosts U7 on day 1, then U10 on day 8 (consecutive weekend, different age group)
        # If streak were global, Jar would be penalized for U10.
        # With age-group scoping, (Jar, U10) has no streak -> 3 candidates, Jar still feasible.
        scheduled = [
            (date(2026, 10, 3), "U7"),
            (date(2026, 10, 3), "U10"),  # same day, different age group
            (date(2026, 10, 10), "U10"),  # next weekend, same club Jar would host
        ]
        assignments = assign_hosts(planner, scheduled)

        # All 3 slots assigned to some club
        assert all(a != "" for a in assignments), "All slots should be assigned"
        # Assignments don't crash — streak is age-scoped, so Jar can be assigned for U10
        # even though Jar hosted U7 the previous week
        assert len(assignments) == 3

    def test_same_age_group_streak_penalty_still_applies(self):
        """A club hosting U7 two weekends in a row still gets a streak penalty."""
        teams = [
            Team(club="Jar", label="Jar U7", age_group="U7"),
            Team(club="Kongsberg", label="Kongsberg U7", age_group="U7"),
        ]
        planner = self._make_planner(teams)

        scheduled = [
            (date(2026, 10, 3), "U7"),
            (date(2026, 10, 10), "U7"),   # consecutive same age group
        ]
        assignments = assign_hosts(planner, scheduled)

        # Both slots assigned
        assert all(a != "" for a in assignments)

    def test_same_arena_same_day_different_age_groups_allowed(self):
        """Two tournaments in the same arena on the same day for different age groups is allowed."""
        teams = [
            Team(club="Jar", label="Jar U7", age_group="U7"),
            Team(club="Jar", label="Jar U10", age_group="U10"),
            Team(club="Kongsberg", label="Kongsberg U7", age_group="U7"),
            Team(club="Kongsberg", label="Kongsberg U10", age_group="U10"),
        ]
        planner = self._make_planner(teams)

        # Two tournaments on the same day, different age groups, both at Jar/Jarhallen
        scheduled = [
            (date(2026, 10, 3), "U7"),
            (date(2026, 10, 3), "U10"),
        ]
        assignments = assign_hosts(planner, scheduled)

        assert len(assignments) == 2
        assert all(a != "" for a in assignments), "Both slots should be assigned"

    def test_unknown_calendar_status_club_not_excluded_from_candidate_pool(self):
        """A club with unknown calendar status this run still competes for
        hosting duty -- it must not be silently dropped from the candidate
        pool (that would just shift its share onto other clubs)."""
        teams = [
            Team(club="Jar", label="Jar U10", age_group="U10"),
            Team(club="Kongsberg", label="Kongsberg U10", age_group="U10"),
        ]
        planner = self._make_planner(teams)
        planner.club_calendar_status = {"Jar": "known", "Kongsberg": "unknown"}

        scheduled = [
            (date(2026, 10, 3), "U10"),
            (date(2026, 10, 10), "U10"),
            (date(2026, 10, 17), "U10"),
            (date(2026, 10, 24), "U10"),
        ]
        assignments = assign_hosts(planner, scheduled)

        assert "Kongsberg" in assignments, (
            "unknown-status club should still be selectable as host, not "
            "excluded up front"
        )
