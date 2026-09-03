"""Unit tests for tournament_scheduler.stage3_optimizer (issue #257, scope item 5)."""

from __future__ import annotations

from tournament_scheduler.planning_contract import score_candidate, verify_candidate
from tournament_scheduler.stage3_optimizer import optimize_candidate


def _team(club: str, label: str, age_group: str) -> dict:
    return {"club": club, "label": label, "age_group": age_group}


def _tournament(t_id: str, date_str: str, arena: str, age_group: str, teams: list[dict]) -> dict:
    game_pairs = [(a["label"], b["label"]) for i, a in enumerate(teams) for b in teams[i + 1 :]]
    return {
        "id": t_id,
        "date": date_str,
        "arena": arena,
        "age_group": age_group,
        "host_club": teams[0]["club"] if teams else None,
        "teams": teams,
        "games": [
            {"home": home, "away": away, "parallel_slot": 0, "round_number": 1}
            for home, away in game_pairs
        ],
    }


def _clustered_candidate() -> dict:
    """A deliberately bad candidate: the same two quartets meet twice each."""
    teams = {f"T{i}": _team(f"Club{i}", f"T{i}", "U10") for i in range(1, 9)}
    group_a = [teams["T1"], teams["T2"], teams["T3"], teams["T4"]]
    group_b = [teams["T5"], teams["T6"], teams["T7"], teams["T8"]]
    return {
        "schema_version": 1,
        "tournaments": [
            _tournament("t1", "2026-01-05", "Arena1", "U10", group_a),
            _tournament("t2", "2026-02-04", "Arena1", "U10", group_a),
            _tournament("t3", "2026-03-06", "Arena5", "U10", group_b),
            _tournament("t4", "2026-04-05", "Arena5", "U10", group_b),
        ],
    }


class TestOptimizeCandidate:
    def test_preserves_participation_counts(self):
        candidate = _clustered_candidate()
        before = score_candidate(candidate)["participation"]["counts_by_team"]

        optimized = optimize_candidate(candidate, iterations=2000, seed=1)

        after = score_candidate(optimized)["participation"]["counts_by_team"]
        assert after == before

    def test_preserves_tournament_roster_sizes_and_skeleton(self):
        candidate = _clustered_candidate()
        optimized = optimize_candidate(candidate, iterations=2000, seed=1)

        by_id = {t["id"]: t for t in candidate["tournaments"]}
        for t in optimized["tournaments"]:
            original = by_id[t["id"]]
            assert len(t["teams"]) == len(original["teams"])
            assert t["date"] == original["date"]
            assert t["arena"] == original["arena"]
            assert t["age_group"] == original["age_group"]

    def test_reduces_repeated_pairings(self):
        candidate = _clustered_candidate()
        before = score_candidate(candidate)["opponent_diversity"]

        optimized = optimize_candidate(candidate, iterations=3000, seed=1)
        after = score_candidate(optimized)["opponent_diversity"]

        assert after["max_pair_repeat"] <= before["max_pair_repeat"]
        assert after["pairs_meeting_3_plus"] <= before["pairs_meeting_3_plus"]
        assert after["unique_pairs"] >= before["unique_pairs"]

    def test_optimized_candidate_still_passes_hard_verification(self):
        candidate = _clustered_candidate()
        optimized = optimize_candidate(candidate, iterations=2000, seed=1)

        result = verify_candidate(optimized)
        assert result["ok"], result["violations"]

    def test_deterministic_for_fixed_seed(self):
        candidate = _clustered_candidate()
        a = optimize_candidate(candidate, iterations=1500, seed=42)
        b = optimize_candidate(candidate, iterations=1500, seed=42)
        assert a["tournaments"] == b["tournaments"]

    def test_zero_iterations_is_a_no_op_copy(self):
        candidate = _clustered_candidate()
        optimized = optimize_candidate(candidate, iterations=0, seed=1)
        assert optimized["tournaments"] == candidate["tournaments"]
        assert optimized is not candidate

    def test_untouched_when_only_one_tournament_per_age_group(self):
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        candidate = {"tournaments": [_tournament("t1", "2026-01-10", "Jarhallen", "U10", teams)]}
        optimized = optimize_candidate(candidate, iterations=500, seed=1)
        assert optimized["tournaments"] == candidate["tournaments"]

    def test_cancelled_tournaments_pass_through_unchanged(self):
        teams = [_team("Jar", "Jar 1", "U10"), _team("Kongsberg", "Kongsberg 1", "U10")]
        cancelled = _tournament("tc", "2026-01-10", "Jarhallen", "U10", teams)
        cancelled["cancelled"] = True
        candidate = _clustered_candidate()
        candidate["tournaments"].append(cancelled)

        optimized = optimize_candidate(candidate, iterations=500, seed=1)

        optimized_by_id = {t["id"]: t for t in optimized["tournaments"]}
        assert optimized_by_id["tc"] == cancelled
