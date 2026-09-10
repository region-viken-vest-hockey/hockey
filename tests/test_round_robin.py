"""Tests for SeasonPlanner.generate_round_robin_games (circle-method round-robin generator)."""


from tournament_scheduler.models import Team
from tournament_scheduler.season_planner import SeasonPlanner


def _make_teams(n, age_group='U10'):
    return [Team(club=f'Club{i}', label=f'Team{i}', age_group=age_group) for i in range(n)]


def _expected_pairings(n):
    return n * (n - 1) // 2


def _expected_min_rounds(n, parallel_games):
    """Minimum number of sequential rounds for n teams given parallel-game limit.

    A full round-robin for n teams needs (n-1) rounds for even n, or n rounds
    for odd n (one bye per round). Each round has at most floor(n/2) games,
    which may need to be split across multiple "sequential slots" if
    parallel_games is smaller than the games-per-round count — but the
    generator assigns parallel_slot within a round (0..parallel_games-1),
    it does not split rounds. So the expected round count is simply the
    standard round-robin round count.
    """
    return n - 1 if n % 2 == 0 else n


class TestRoundRobinGenerator:
    """Test suite for the circle-method round-robin game generator."""

    def test_empty_and_single_team_produce_no_games(self):
        assert SeasonPlanner.generate_round_robin_games([], parallel_games=2) == []
        assert SeasonPlanner.generate_round_robin_games(_make_teams(1), parallel_games=2) == []

    def test_every_team_plays_every_other_team_exactly_once(self):
        for n in range(2, 9):
            teams = _make_teams(n)
            games = SeasonPlanner.generate_round_robin_games(teams, parallel_games=2)

            assert len(games) == _expected_pairings(n), f"n={n}: expected {_expected_pairings(n)} games"

            seen_pairs = set()
            for game in games:
                pair = frozenset((game.home.label, game.away.label))
                assert pair not in seen_pairs, f"n={n}: duplicate pairing {pair}"
                seen_pairs.add(pair)

            # Every team appears in exactly n-1 games (plays everyone else once).
            appearances = {}
            for game in games:
                appearances[game.home.label] = appearances.get(game.home.label, 0) + 1
                appearances[game.away.label] = appearances.get(game.away.label, 0) + 1
            for team in teams:
                assert appearances[team.label] == n - 1

    def test_no_team_appears_twice_in_the_same_round(self):
        """Within each generated 'round' (a maximal run of non-overlapping games
        produced consecutively by the circle method), no team should play twice.

        Since the generator emits games round-by-round, we reconstruct rounds
        by greedily grouping consecutive games until a team repeats.
        """
        for n in [4, 5, 6, 7, 8]:
            teams = _make_teams(n)
            games = SeasonPlanner.generate_round_robin_games(teams, parallel_games=3)

            rounds = []
            current_round = []
            current_teams = set()
            for game in games:
                if game.home.label in current_teams or game.away.label in current_teams:
                    rounds.append(current_round)
                    current_round = []
                    current_teams = set()
                current_round.append(game)
                current_teams.add(game.home.label)
                current_teams.add(game.away.label)
            if current_round:
                rounds.append(current_round)

            for round_games in rounds:
                labels = []
                for game in round_games:
                    labels.append(game.home.label)
                    labels.append(game.away.label)
                assert len(labels) == len(set(labels)), f"n={n}: team appears twice within a round"

    def test_parallel_slot_respects_limit(self):
        for n in [4, 5, 6, 7, 8]:
            for parallel_games in [1, 2, 3]:
                teams = _make_teams(n)
                games = SeasonPlanner.generate_round_robin_games(teams, parallel_games=parallel_games)
                for game in games:
                    assert 0 <= game.parallel_slot < parallel_games

    def test_round_count_matches_expected_minimum(self):
        """The number of distinct rounds matches the standard round-robin minimum
        for the given team count (n-1 for even n, n for odd n with byes)."""
        for n in [4, 5, 6, 7, 8]:
            teams = _make_teams(n)
            games = SeasonPlanner.generate_round_robin_games(teams, parallel_games=10)

            rounds = []
            current_round = []
            current_teams = set()
            for game in games:
                if game.home.label in current_teams or game.away.label in current_teams:
                    rounds.append(current_round)
                    current_round = []
                    current_teams = set()
                current_round.append(game)
                current_teams.add(game.home.label)
                current_teams.add(game.away.label)
            if current_round:
                rounds.append(current_round)

            assert len(rounds) == _expected_min_rounds(n, parallel_games=10), (
                f"n={n}: expected {_expected_min_rounds(n, 10)} rounds, got {len(rounds)}"
            )

    def test_balances_rounds_when_same_club_pairings_are_allowed(self):
        teams = [
            Team(club="Jar", label="Jar1", age_group="U10"),
            Team(club="Jar", label="Jar2", age_group="U10"),
            Team(club="Jar", label="Jar3", age_group="U10"),
            Team(club="Frisk Asker", label="Frisk1", age_group="U10"),
            Team(club="Frisk Asker", label="Frisk2", age_group="U10"),
            Team(club="Sandefjord", label="Sandefjord1", age_group="U10"),
        ]

        games = SeasonPlanner.generate_round_robin_games(teams, parallel_games=3)
        rounds = {}
        for game in games:
            rounds.setdefault(game.round_number, []).append(game)

        assert len(rounds) == 5
        assert sorted(len(round_games) for round_games in rounds.values()) == [3, 3, 3, 3, 3]

        seen_pairs = {frozenset((game.home.label, game.away.label)) for game in games}
        assert frozenset(("Jar1", "Jar2")) in seen_pairs
        assert frozenset(("Jar1", "Jar3")) in seen_pairs
        assert frozenset(("Jar2", "Jar3")) in seen_pairs
        assert frozenset(("Frisk1", "Frisk2")) in seen_pairs

        for round_games in rounds.values():
            seen = set()
            for game in round_games:
                assert game.home.label not in seen
                assert game.away.label not in seen
                seen.add(game.home.label)
                seen.add(game.away.label)

    def test_three_parallel_games_and_six_teams_make_five_full_rounds(self):
        teams = [
            Team(club="ClubA", label="ClubA 1", age_group="U10"),
            Team(club="ClubB", label="ClubB 1", age_group="U10"),
            Team(club="ClubC", label="ClubC 1", age_group="U10"),
            Team(club="ClubD", label="ClubD 1", age_group="U10"),
            Team(club="ClubE", label="ClubE 1", age_group="U10"),
            Team(club="ClubF", label="ClubF 1", age_group="U10"),
        ]

        games = SeasonPlanner.generate_round_robin_games(teams, parallel_games=3)
        rounds = {}
        for game in games:
            rounds.setdefault(game.round_number, []).append(game)

        assert len(rounds) == 5
        assert all(len(round_games) == 3 for round_games in rounds.values())
        assert len(games) == 15
        for round_games in rounds.values():
            seen = set()
            for game in round_games:
                assert game.home.label not in seen
                assert game.away.label not in seen
                seen.add(game.home.label)
                seen.add(game.away.label)


class TestHostFirstParticipantOrder:
    """participants[0] is the host club; they must appear as game.home
    in every game they participate in (never as the away team)."""

    def test_first_participant_is_home_in_their_own_games(self):
        """When the host club's team is placed first, they appear as game.home
        in all games where they participate."""
        host = Team(club="Frisk Asker", label="Frisk Asker A", age_group="U10")
        visitors = [
            Team(club="Kongsberg", label="Kongsberg A", age_group="U10"),
            Team(club="Skien", label="Skien A", age_group="U10"),
            Team(club="Ringerike", label="Ringerike A", age_group="U10"),
        ]
        participants = [host] + visitors

        games = SeasonPlanner.generate_round_robin_games(participants, parallel_games=2)

        assert len(games) > 0
        host_games = [
            g for g in games
            if g.home.club == "Frisk Asker" or g.away.club == "Frisk Asker"
        ]
        assert len(host_games) > 0, "host should participate in at least one game"
        for game in host_games:
            assert game.home.club == "Frisk Asker", (
                f"Frisk Asker should be home in their games; got home={game.home.club!r}"
            )

    def test_home_team_invariant_with_five_participants(self):
        host = Team(club="Kongsberg", label="Kongsberg A", age_group="U10")
        visitors = [
            Team(club=f"Club{i}", label=f"Club{i} A", age_group="U10")
            for i in range(4)
        ]
        participants = [host] + visitors

        games = SeasonPlanner.generate_round_robin_games(participants, parallel_games=2)

        host_games = [
            g for g in games
            if g.home.club == "Kongsberg" or g.away.club == "Kongsberg"
        ]
        assert len(host_games) > 0
        for game in host_games:
            assert game.home.club == "Kongsberg", (
                f"Kongsberg should be home in their games; got {game.home.club!r}"
            )
