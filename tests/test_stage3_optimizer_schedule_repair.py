"""Unit tests for the baseline-bounded schedule-conflict repair pass
(issue #257 skeleton follow-up to Task 2)."""

from __future__ import annotations

from tournament_scheduler.planning_contract import score_candidate, verify_candidate
from tournament_scheduler.stage3_optimizer import (
    repair_schedule_conflicts_bounded,
    repair_schedule_conflicts_bounded_multi_seed,
)

_PROBLEM = {"round_length_minutes": {"U7": 20, "U9": 20}}


def _team(club: str, label: str, age_group: str) -> dict:
    return {"club": club, "label": label, "age_group": age_group}


def _tournament(
    t_id: str, date_str: str, arena: str, age_group: str, teams: list[dict], start_time: str = "10:00"
) -> dict:
    game_pairs = [(a["label"], b["label"]) for i, a in enumerate(teams) for b in teams[i + 1 :]]
    return {
        "id": t_id,
        "date": date_str,
        "arena": arena,
        "age_group": age_group,
        "host_club": teams[0]["club"] if teams else None,
        "start_time": start_time,
        "teams": teams,
        "games": [
            {"home": home, "away": away, "parallel_slot": 0, "round_number": 1}
            for home, away in game_pairs
        ],
    }


def _cross_age_group_arena_conflict_candidate() -> dict:
    """Two different age groups' tournaments sharing an arena at the same
    time on the same date — a real arena_interval_conflict, cross-age-group
    (so a per-age-group optimizer cannot fix it), fixable by moving one of
    them to a free date via a date swap."""
    u7_teams = [_team(f"ClubU7{i}", f"U7-{i}", "U7") for i in range(1, 5)]
    u9_teams = [_team(f"ClubU9{i}", f"U9-{i}", "U9") for i in range(1, 5)]
    return {
        "schema_version": 1,
        "tournaments": [
            _tournament("t1", "2026-01-05", "ArenaX", "U7", u7_teams),
            _tournament("t2", "2026-01-05", "ArenaX", "U9", u9_teams),
            # A free U7 slot elsewhere to swap t1's date into.
            _tournament("t3", "2026-03-05", "ArenaY", "U7", u7_teams),
        ],
    }


class TestRepairScheduleConflictsBounded:
    def test_resolves_cross_age_group_arena_conflict(self):
        candidate = _cross_age_group_arena_conflict_candidate()
        baseline_verification = verify_candidate(candidate, _PROBLEM)
        assert not baseline_verification["ok"]
        assert any(v["code"] == "arena_interval_conflict" for v in baseline_verification["violations"])

        repaired = repair_schedule_conflicts_bounded_multi_seed(
            candidate, _PROBLEM, seeds=(1, 2, 3, 4, 5), iterations=500
        )

        verification = verify_candidate(repaired, _PROBLEM)
        assert verification["ok"], verification["violations"]
        assert repaired["source"]["status"] == "improved"
        assert repaired["source"]["best_violations"] == 0

    def test_never_increases_hard_violations(self):
        candidate = _cross_age_group_arena_conflict_candidate()
        repaired = repair_schedule_conflicts_bounded(candidate, _PROBLEM, iterations=500, seed=1)
        old_count = len(verify_candidate(candidate, _PROBLEM)["violations"])
        new_count = len(verify_candidate(repaired, _PROBLEM)["violations"])
        assert new_count <= old_count

    def test_never_regresses_turnaround_gaps(self):
        candidate = _cross_age_group_arena_conflict_candidate()
        old_gaps = score_candidate(candidate)["turnaround"]["gaps_under_days"]
        for seed in range(5):
            repaired = repair_schedule_conflicts_bounded(candidate, _PROBLEM, iterations=500, seed=seed)
            new_gaps = score_candidate(repaired)["turnaround"]["gaps_under_days"]
            assert new_gaps[7] <= old_gaps[7]
            assert new_gaps[14] <= old_gaps[14]

    def test_never_changes_teams_or_arenas(self):
        """Only dates move — arenas/hosts/rosters are invariant by construction."""
        candidate = _cross_age_group_arena_conflict_candidate()
        repaired = repair_schedule_conflicts_bounded_multi_seed(
            candidate, _PROBLEM, seeds=(1, 2, 3), iterations=500
        )
        old_by_id = {t["id"]: t for t in candidate["tournaments"]}
        for t in repaired["tournaments"]:
            old = old_by_id[t["id"]]
            assert t["arena"] == old["arena"]
            assert t["host_club"] == old["host_club"]
            assert t["teams"] == old["teams"]

    def test_zero_baseline_violations_returns_input_unchanged(self):
        u7_teams = [_team(f"ClubU7{i}", f"U7-{i}", "U7") for i in range(1, 5)]
        candidate = {
            "schema_version": 1,
            "tournaments": [
                _tournament("t1", "2026-01-05", "ArenaX", "U7", u7_teams),
                _tournament("t2", "2026-02-05", "ArenaY", "U7", u7_teams),
            ],
        }
        assert verify_candidate(candidate, _PROBLEM)["ok"]

        repaired = repair_schedule_conflicts_bounded_multi_seed(candidate, _PROBLEM, seeds=(1,), iterations=500)

        assert repaired["tournaments"] == candidate["tournaments"]
        assert repaired["source"]["status"] == "unchanged"
        assert repaired["source"]["baseline_violations"] == 0

    def test_multi_seed_picks_best_across_seeds(self):
        candidate = _cross_age_group_arena_conflict_candidate()
        multi = repair_schedule_conflicts_bounded_multi_seed(
            candidate, _PROBLEM, seeds=(1, 2, 3, 4, 5), iterations=500
        )
        multi_violations = len(verify_candidate(multi, _PROBLEM)["violations"])
        for seed in (1, 2, 3, 4, 5):
            single = repair_schedule_conflicts_bounded(candidate, _PROBLEM, iterations=500, seed=seed)
            single_violations = len(verify_candidate(single, _PROBLEM)["violations"])
            assert multi_violations <= single_violations
