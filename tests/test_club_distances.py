"""Tests for club_distances — distance lookups and furthest-traveling-team logic."""

from tournament_scheduler.models import SeasonPlan, Team, Tournament
from tournament_scheduler.club_distances import (
    _CLUB_COORDINATES,
    _ROAD_DISTANCE_FACTOR,
    _haversine_km,
    arena_to_club,
    compute_team_travel_distances,
    distance,
    furthest_traveling_team,
)


class TestHaversineDistance:
    """Tests for the underlying haversine + road-correction calculation."""

    def test_identical_coordinates_returns_zero(self):
        coord = _CLUB_COORDINATES["Kongsberg"]
        assert _haversine_km(coord, coord) == 0

    def test_known_pair_within_realistic_range(self):
        """Kongsberg <-> Jar should be a realistic Oslo-area driving distance."""
        kongsberg = _CLUB_COORDINATES["Kongsberg"]
        jar = _CLUB_COORDINATES["Jar"]
        great_circle = _haversine_km(kongsberg, jar)
        assert 40 <= great_circle <= 80

    def test_road_correction_scales_up_great_circle_distance(self):
        """The corrected distance() result should be larger than the raw
        haversine great-circle distance, by exactly _ROAD_DISTANCE_FACTOR."""
        kongsberg = _CLUB_COORDINATES["Kongsberg"]
        jar = _CLUB_COORDINATES["Jar"]
        great_circle = _haversine_km(kongsberg, jar)
        road_distance = distance("Kongsberg", "Jar")

        assert road_distance > great_circle
        assert road_distance == round(great_circle * _ROAD_DISTANCE_FACTOR)


class TestDistanceLookup:
    """Tests for the haversine-based club-to-club distance calculation."""

    def test_known_pair_returns_positive_distance(self):
        assert distance("Kongsberg", "Jar") > 50

    def test_lookup_is_symmetric(self):
        assert distance("Kongsberg", "Jar") == distance("Jar", "Kongsberg")

    def test_same_club_returns_zero(self):
        assert distance("Kongsberg", "Kongsberg") == 0

    def test_unknown_club_returns_zero(self):
        assert distance("Kongsberg", "UnknownClub") == 0
        assert distance("UnknownClub", "Jar") == 0

    def test_all_rvv_clubs_have_known_distances(self):
        """Every pair of distinct RVV clubs should have a recorded distance."""
        clubs = [
            "Kongsberg", "Jar", "Holmen", "Ringerike",
            "Skien", "Jutul", "Frisk Asker", "Tønsberg",
            "Sandefjord Penguins",
        ]
        for i, a in enumerate(clubs):
            for b in clubs[i + 1:]:
                assert distance(a, b) > 0, f"missing distance: {a} <-> {b}"

    def test_all_pairs_are_symmetric(self):
        """distance(a, b) == distance(b, a) for every pair of RVV clubs."""
        clubs = [
            "Kongsberg", "Jar", "Holmen", "Ringerike",
            "Skien", "Jutul", "Frisk Asker", "Tønsberg",
            "Sandefjord Penguins",
        ]
        for i, a in enumerate(clubs):
            for b in clubs[i + 1:]:
                assert distance(a, b) == distance(b, a), f"asymmetric: {a} <-> {b}"


class TestArenaToClub:
    """Tests for arena name → club name resolution."""

    def test_known_arena_returns_club(self):
        assert arena_to_club("Kongsberghallen") == "Kongsberg"
        assert arena_to_club("Jarhallen") == "Jar"

    def test_unknown_arena_returns_none(self):
        assert arena_to_club("Unknown Arena") is None
        assert arena_to_club("") is None


class TestFurthestTravelingTeam:
    """Tests for furthest_traveling_team logic."""

    def test_empty_tournament_returns_none(self):
        t = Tournament(date=None, arena="Kongsberghallen", age_group="U10")
        assert furthest_traveling_team(t) is None

    def test_single_local_team_returns_none(self):
        """A tournament with only the host's own team should return None (no travel)."""
        t = Tournament(
            date=None,
            arena="Kongsberghallen",
            age_group="U10",
            teams=[Team(club="Kongsberg", label="Kongsberg U10", age_group="U10")],
        )
        assert furthest_traveling_team(t) is None

    def test_prefers_farthest_team(self):
        """Jar should be identified as farthest from Kongsberg when
        Jar and Kongsberg are both participants hosted at Kongsberghallen."""
        t = Tournament(
            date=None,
            arena="Kongsberghallen",
            age_group="U10",
            host_club="Kongsberg",
            teams=[
                Team(club="Kongsberg", label="Kongsberg U10", age_group="U10"),
                Team(club="Jar", label="Jar 1", age_group="U10"),
                Team(club="Holmen", label="Holmen U10", age_group="U10"),
            ],
        )
        result = furthest_traveling_team(t)
        assert result is not None
        team, km = result
        # Based on real arena coordinates, Jar (~75 km) is farther than
        # Holmen (~64 km) from Kongsberg.
        assert team.label == "Jar 1"
        assert km > 50

    def test_unknown_arena_returns_none(self):
        """When the arena is not in the registry, fall back to host_club."""
        t = Tournament(
            date=None,
            arena="Unknown Arena",
            age_group="U10",
            host_club="Kongsberg",
            teams=[
                Team(club="Jar", label="Jar 1", age_group="U10"),
            ],
        )
        result = furthest_traveling_team(t)
        assert result is not None
        team, km = result
        assert team.label == "Jar 1"
        assert km > 50

    def test_no_host_club_and_unknown_arena_returns_none(self):
        """When neither arena nor host_club is known, return None."""
        t = Tournament(
            date=None,
            arena="Unknown Arena",
            age_group="U10",
            host_club=None,
            teams=[
                Team(club="Jar", label="Jar 1", age_group="U10"),
            ],
        )
        assert furthest_traveling_team(t) is None


class TestComputeTeamTravelDistances:
    """Tests for compute_team_travel_distances — per-team accumulated travel across the season."""

    def _make_plan(self, tournaments: list[Tournament]) -> SeasonPlan:
        """Create a minimal SeasonPlan from a list of tournaments."""
        plan = SeasonPlan()
        plan.tournaments = tournaments
        if tournaments:
            plan.start_date = tournaments[0].date
            plan.end_date = tournaments[-1].date
        return plan

    def test_empty_plan_returns_empty_dict(self):
        plan = SeasonPlan()
        assert compute_team_travel_distances(plan) == {}

    def test_local_tournament_adds_zero(self):
        """A tournament where the team's club is the host adds 0 to its total."""
        from datetime import date
        t = Tournament(
            date=date(2025, 9, 6),
            arena="Jarhallen",
            age_group="U10",
            host_club="Jar",
            teams=[
                Team(club="Jar", label="Jar 1", age_group="U10"),
                Team(club="Kongsberg", label="Kongsberg U10", age_group="U10"),
            ],
        )
        plan = self._make_plan([t])
        result = compute_team_travel_distances(plan)
        assert result["Jar 1"] == 0
        assert result["Kongsberg U10"] == distance("Kongsberg", "Jar")

    def test_multiple_away_tournaments_accumulate(self):
        """Team travelling to multiple away tournaments accumulates distances."""
        from datetime import date
        t1 = Tournament(
            date=date(2025, 9, 6),
            arena="Jarhallen",
            age_group="U10",
            host_club="Jar",
            teams=[
                Team(club="Kongsberg", label="Kongsberg U10", age_group="U10"),
            ],
        )
        t2 = Tournament(
            date=date(2025, 10, 11),
            arena="Skien ishall",
            age_group="U10",
            host_club="Skien",
            teams=[
                Team(club="Kongsberg", label="Kongsberg U10", age_group="U10"),
            ],
        )
        plan = self._make_plan([t1, t2])
        result = compute_team_travel_distances(plan)
        expected = distance("Kongsberg", "Jar") + distance("Kongsberg", "Skien")
        assert result["Kongsberg U10"] == expected

    def test_cancelled_tournament_skipped(self):
        """Cancelled tournaments do not add travel distance."""
        from datetime import date
        t1 = Tournament(
            date=date(2025, 9, 6),
            arena="Jarhallen",
            age_group="U10",
            host_club="Jar",
            teams=[
                Team(club="Kongsberg", label="Kongsberg U10", age_group="U10"),
            ],
        )
        t2 = Tournament(
            date=date(2025, 10, 11),
            arena="Skien ishall",
            age_group="U10",
            host_club="Skien",
            cancelled=True,
            cancellation_reason="Ice rink maintenance",
            teams=[
                Team(club="Kongsberg", label="Kongsberg U10", age_group="U10"),
            ],
        )
        plan = self._make_plan([t1, t2])
        result = compute_team_travel_distances(plan)
        # Only Jar-hosted distance should count; Skien is cancelled
        assert result["Kongsberg U10"] == distance("Kongsberg", "Jar")

    def test_unknown_host_arena_skipped(self):
        """When neither arena nor host_club resolves to a known club, the tournament is skipped."""
        from datetime import date
        t = Tournament(
            date=date(2025, 9, 6),
            arena="Unknown Arena",
            age_group="U10",
            host_club=None,
            teams=[
                Team(club="Kongsberg", label="Kongsberg U10", age_group="U10"),
            ],
        )
        plan = self._make_plan([t])
        result = compute_team_travel_distances(plan)
        # Team is registered with 0 but no distance added
        assert result["Kongsberg U10"] == 0

    def test_dict_keys_match_team_labels(self):
        """Every participating team should appear as a key in the result dict,
        even those with zero travel."""
        from datetime import date
        t = Tournament(
            date=date(2025, 9, 6),
            arena="Jarhallen",
            age_group="U10",
            host_club="Jar",
            teams=[
                Team(club="Jar", label="Jar 1", age_group="U10"),
                Team(club="Kongsberg", label="Kongsberg U10", age_group="U10"),
                Team(club="Holmen", label="Holmen U10", age_group="U10"),
            ],
        )
        plan = self._make_plan([t])
        result = compute_team_travel_distances(plan)
        assert set(result.keys()) == {"Jar 1", "Kongsberg U10", "Holmen U10"}
        assert result["Jar 1"] == 0
        assert result["Kongsberg U10"] > 0
        assert result["Holmen U10"] > 0
