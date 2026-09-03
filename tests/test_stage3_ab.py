"""Unit tests for tournament_scheduler.stage3_ab (issue #257, scope items 6-7)."""

from __future__ import annotations

from tournament_scheduler.stage3_ab import build_ab_report
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


class TestBuildAbReport:
    def test_optimizer_output_is_promotable_over_bad_baseline(self):
        old_candidate = _clustered_candidate()
        new_candidate = optimize_candidate(old_candidate, iterations=3000, seed=1)

        report = build_ab_report(old_candidate, new_candidate)

        assert report["old"]["verification"]["ok"]
        assert report["new"]["verification"]["ok"]
        assert not report["hard_constraint_regressed"]
        assert report["promotable"], report["overall_comparison"]["regressions"]

    def test_identical_candidates_have_no_regressions(self):
        candidate = _clustered_candidate()
        report = build_ab_report(candidate, dict(candidate))
        assert report["overall_comparison"]["regressions"] == []
        assert report["promotable"]

    def test_worse_new_candidate_is_flagged_as_regression(self):
        old_candidate = _clustered_candidate()
        # Swap group_b's teams so the two quartets play each other 4x instead
        # of 2x each — strictly worse opponent diversity than the baseline.
        worse = {
            "schema_version": 1,
            "tournaments": [
                _tournament("t1", "2026-01-05", "Arena1", "U10", old_candidate["tournaments"][0]["teams"]),
                _tournament("t2", "2026-01-08", "Arena1", "U10", old_candidate["tournaments"][0]["teams"]),
                _tournament("t3", "2026-01-11", "Arena5", "U10", old_candidate["tournaments"][2]["teams"]),
                _tournament("t4", "2026-01-14", "Arena5", "U10", old_candidate["tournaments"][2]["teams"]),
            ],
        }

        report = build_ab_report(old_candidate, worse)

        assert not report["promotable"]
        assert report["overall_comparison"]["regressions"]

    def test_per_age_group_breakdown_matches_overall_for_single_age_group(self):
        old_candidate = _clustered_candidate()
        new_candidate = optimize_candidate(old_candidate, iterations=3000, seed=1)

        report = build_ab_report(old_candidate, new_candidate)

        assert set(report["by_age_group"].keys()) == {"U10"}
        u10 = report["by_age_group"]["U10"]
        assert u10["old"]["opponent_diversity"] == report["old"]["score"]["opponent_diversity"]
        assert u10["new"]["opponent_diversity"] == report["new"]["score"]["opponent_diversity"]

    def test_hard_constraint_regression_blocks_promotion(self):
        old_candidate = _clustered_candidate()
        broken = dict(old_candidate)
        # Duplicate a team within the same tournament on the same date — a
        # hard verification failure that wasn't present in the baseline.
        broken_tournaments = [dict(t) for t in old_candidate["tournaments"]]
        broken_tournaments[0] = dict(broken_tournaments[0])
        broken_tournaments[0]["teams"] = broken_tournaments[0]["teams"] + [
            broken_tournaments[0]["teams"][0]
        ]
        broken["tournaments"] = broken_tournaments

        report = build_ab_report(old_candidate, broken)

        assert report["hard_constraint_regressed"]
        assert not report["promotable"]
