from collections import Counter, defaultdict

from tournament_scheduler.final_verification import verify_final_candidate
from tournament_scheduler.limited_rounds import count_same_club_games, generate_limited_round_games
from tournament_scheduler.models import Team


def _u8_teams():
    return [Team("Jar", "Jar 1", "U8"), Team("Jar", "Jar 2", "U8")] + [
        Team(club, club, "U8") for club in ["A", "B", "C", "D", "E", "F"]
    ]


def test_limited_rounds_8_teams_5_rounds_avoids_jar_derby():
    games = generate_limited_round_games(_u8_teams(), parallel_games=4, rounds=5)

    assert len(games) == 20
    assert max(g.round_number for g in games) == 5
    assert count_same_club_games(games) == 0

    counts = Counter()
    pairs = Counter()
    by_round = defaultdict(list)
    for game in games:
        counts[game.home.label] += 1
        counts[game.away.label] += 1
        pairs[tuple(sorted((game.home.label, game.away.label)))] += 1
        by_round[game.round_number].append(game)

    assert set(counts.values()) == {5}
    assert all(count == 1 for count in pairs.values())
    assert all(len(round_games) == 4 for round_games in by_round.values())
    assert all(
        len({team for game in round_games for team in (game.home.label, game.away.label)}) == 8
        for round_games in by_round.values()
    )


def test_limited_rounds_are_deterministic():
    first = [
        (g.round_number, g.parallel_slot, g.home.label, g.away.label)
        for g in generate_limited_round_games(_u8_teams(), 4, 5)
    ]
    second = [
        (g.round_number, g.parallel_slot, g.home.label, g.away.label)
        for g in generate_limited_round_games(_u8_teams(), 4, 5)
    ]
    assert first == second


def test_limited_rounds_6_teams_3_parallel_5_rounds_is_15_games():
    teams = [Team(f"Club {i}", f"Team {i}", "U10") for i in range(1, 7)]
    games = generate_limited_round_games(teams, parallel_games=3, rounds=5)

    assert len(games) == 15
    counts = Counter()
    pairs = Counter()
    by_round = defaultdict(list)
    for game in games:
        counts[game.home.label] += 1
        counts[game.away.label] += 1
        pairs[tuple(sorted((game.home.label, game.away.label)))] += 1
        by_round[game.round_number].append(game)

    assert set(counts.values()) == {5}
    assert all(count == 1 for count in pairs.values())
    assert sorted(len(round_games) for round_games in by_round.values()) == [3, 3, 3, 3, 3]


def test_limited_rounds_do_not_require_all_pairs():
    # 8 teams have 28 possible pairings; a 5-round / 4-parallel schedule
    # deliberately plays only 20 of them.
    games = generate_limited_round_games(_u8_teams(), parallel_games=4, rounds=5)
    unique_pairs = {tuple(sorted((g.home.label, g.away.label))) for g in games}
    assert len(unique_pairs) == 20
    assert len(unique_pairs) < 28


def test_final_verifier_rejects_avoidable_same_club_limited_round_game():
    teams = [{"club": t.club, "label": t.label, "age_group": t.age_group} for t in _u8_teams()]
    generated = generate_limited_round_games(_u8_teams(), 4, 5)
    games = [
        {"home": g.home.label, "away": g.away.label, "parallel_slot": g.parallel_slot, "round_number": g.round_number}
        for g in generated
    ]
    # Replace one avoidable inter-club game with the Jar derby while preserving
    # the other structural properties well enough to exercise the semantic check.
    for game in games:
        if game["round_number"] == 1 and "Jar 1" in (game["home"], game["away"]):
            game["home"] = "Jar 1"
            game["away"] = "Jar 2"
            break

    result = verify_final_candidate(
        {"tournaments": [{"id": "t1", "date": "2026-01-10", "age_group": "U8", "teams": teams, "games": games}]},
        {"teams": teams, "parallel_games": {"U8": 4}, "rounds_per_tournament": {"U8": 5}},
    )

    assert "avoidable_same_club_matchup" in {v["code"] for v in result["violations"]}
