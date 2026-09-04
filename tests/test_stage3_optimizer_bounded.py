"""Unit tests for the baseline-bounded participant optimizer (issue #257 Task 2)."""

from __future__ import annotations

from tournament_scheduler.planning_contract import score_candidate, verify_candidate
from tournament_scheduler.stage3_optimizer import (
    optimize_candidate_participants_bounded,
    optimize_candidate_participants_bounded_multi_seed,
)


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


def _single_tournament_candidate() -> dict:
    teams = [_team(f"ClubS{i}", f"S{i}", "U8") for i in range(1, 5)]
    return {
        "schema_version": 1,
        "tournaments": [_tournament("s1", "2026-01-05", "ArenaS", "U8", teams)],
    }


class TestOptimizeCandidateParticipantsBounded:
    def test_participation_counts_are_preserved_exactly(self):
        candidate = _clustered_candidate()
        optimized = optimize_candidate_participants_bounded(candidate, iterations=3000, seed=1)

        old_counts = score_candidate(candidate)["participation"]["counts_by_team"]
        new_counts = score_candidate(optimized)["participation"]["counts_by_team"]
        assert old_counts == new_counts

    def test_no_new_hard_violations(self):
        candidate = _clustered_candidate()
        optimized = optimize_candidate_participants_bounded(candidate, iterations=3000, seed=1)

        assert verify_candidate(candidate)["ok"]
        assert verify_candidate(optimized)["ok"]

    def test_protected_metrics_never_regress(self):
        candidate = _clustered_candidate()
        old_score = score_candidate(candidate)["opponent_diversity"]

        for seed in range(5):
            optimized = optimize_candidate_participants_bounded(candidate, iterations=2000, seed=seed)
            new_score = score_candidate(optimized)["opponent_diversity"]
            assert new_score["same_club_pairing_count"] <= old_score["same_club_pairing_count"]
            assert (
                new_score["max_same_club_teams_per_tournament"]
                <= old_score["max_same_club_teams_per_tournament"]
            )
            new_turnaround = score_candidate(optimized)["turnaround"]["gaps_under_days"]
            old_turnaround = score_candidate(candidate)["turnaround"]["gaps_under_days"]
            assert new_turnaround[7] <= old_turnaround[7]
            assert new_turnaround[14] <= old_turnaround[14]

    def test_opponent_diversity_improves_within_bounds(self):
        candidate = _clustered_candidate()
        optimized = optimize_candidate_participants_bounded(candidate, iterations=4000, seed=1)

        old_diversity = score_candidate(candidate)["opponent_diversity"]
        new_diversity = score_candidate(optimized)["opponent_diversity"]
        assert new_diversity["pairs_meeting_3_plus"] <= old_diversity["pairs_meeting_3_plus"]
        assert new_diversity["unique_pairs"] >= old_diversity["unique_pairs"]

        status = optimized["source"]["per_age_group_status"]["U10"]
        assert status["status"] == "improved"
        assert status["seed_used"] == 1

    def test_single_tournament_group_reported_unchanged(self):
        candidate = _single_tournament_candidate()
        optimized = optimize_candidate_participants_bounded(candidate, iterations=1000, seed=0)

        assert optimized["tournaments"] == candidate["tournaments"]
        status = optimized["source"]["per_age_group_status"]["U8"]
        assert status["status"] == "unchanged"
        assert "nothing to swap" in status["reason"]

    def test_baseline_retained_when_no_improvement_possible(self):
        """A candidate that is already optimal on the lexicographic metrics
        (every pair meets exactly once, no repeats) has nothing to improve —
        the group must be returned byte-for-byte unchanged rather than
        wandering to a same-quality-but-different assignment."""
        teams = [_team(f"ClubU{i}", f"U{i}", "U9") for i in range(1, 5)]
        # A single 4-team round robin: every pair already meets exactly once.
        candidate = {
            "schema_version": 1,
            "tournaments": [_tournament("u1", "2026-01-05", "ArenaU", "U9", teams)],
        }
        optimized = optimize_candidate_participants_bounded(candidate, iterations=2000, seed=0)
        assert optimized["tournaments"] == candidate["tournaments"]

    def test_multi_seed_picks_best_across_seeds_per_age_group(self):
        candidate = _clustered_candidate()
        multi = optimize_candidate_participants_bounded_multi_seed(
            candidate, seeds=(1, 2, 3, 4, 5), iterations=1500
        )
        single_results = [
            optimize_candidate_participants_bounded(candidate, iterations=1500, seed=s)
            for s in (1, 2, 3, 4, 5)
        ]

        multi_diversity = score_candidate(multi)["opponent_diversity"]
        for single in single_results:
            single_diversity = score_candidate(single)["opponent_diversity"]
            # Multi-seed must be at least as good as any individual seed's
            # result on the primary lexicographic metric.
            assert multi_diversity["pairs_meeting_3_plus"] <= single_diversity["pairs_meeting_3_plus"]

        assert verify_candidate(multi)["ok"]
        status = multi["source"]["per_age_group_status"]["U10"]
        assert status["status"] in {"improved", "unchanged"}
        if status["status"] == "improved":
            assert status["seed_used"] in (1, 2, 3, 4, 5)
