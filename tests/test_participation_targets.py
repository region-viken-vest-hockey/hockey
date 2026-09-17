"""Participation-target policy tests (issue #376).

A participation target is a *strong operational goal* with bounded, evidenced
relaxation -- not a hard legality boundary and not a freely-tradeable soft
preference. These tests pin the canonical target/hard-max semantics, the
avoidability classification and the "season total is stronger than exact half
split" rule.
"""

from __future__ import annotations

from typing import Any

from tournament_scheduler.operator_waivers import scope_fingerprint
from tournament_scheduler.participation_targets import (
    AVOIDABLE,
    BOUNDED_SEARCH_EXHAUSTED,
    PROVEN_INFEASIBLE,
    evaluate_participation,
    resolve_half_target,
    resolve_hard_max,
    resolve_season_target,
)
from tournament_scheduler.planning_contract import verify_candidate

U11 = "U11"


def _team(club: str, label: str, age_group: str = U11) -> dict[str, Any]:
    return {"club": club, "label": label, "age_group": age_group}


def _tournament(t_id: str, date: str, teams: list[dict[str, Any]], age_group: str = U11) -> dict[str, Any]:
    return {
        "id": t_id,
        "date": date,
        "arena": "Arena",
        "age_group": age_group,
        "host_club": teams[0]["club"],
        "start_time": "10:00",
        "teams": teams,
        "games": [],
    }


def _problem(*, teams: list[dict[str, Any]], targets: dict[str, int], **extra: Any) -> dict[str, Any]:
    problem: dict[str, Any] = {
        "start_date": "2025-09-01",
        "end_date": "2026-06-30",
        "teams": [dict(t, target_tournament_count=None) for t in teams],
        "parallel_games": {},
        "participation_targets_by_age_group": {U11: targets},
    }
    problem.update(extra)
    return problem


def test_resolution_precedence_explicit_then_age_group():
    teams = [_team("Jar", "Jar 1")]
    problem = _problem(teams=teams, targets={"before_christmas": 3, "after_christmas": 3})
    identity = ("Jar", "Jar 1", U11)
    assert resolve_season_target(identity, problem) == 6
    assert resolve_half_target(identity, problem, "before_christmas") == 3
    assert resolve_hard_max(identity, problem) is None

    problem["teams"][0]["target_tournament_count"] = 4
    assert resolve_season_target(identity, problem) == 4
    # An explicit season target splits deterministically using the half ratio.
    assert resolve_half_target(identity, problem, "before_christmas") == 2
    assert resolve_half_target(identity, problem, "after_christmas") == 2

    problem["teams"][0]["participation_hard_max"] = 5
    assert resolve_hard_max(identity, problem) == 5


def test_two_plus_four_is_season_complete_with_half_deviation():
    """``2 + 4`` for a ``3 + 3`` target is legal: season total met, half
    distribution deviates by one each way."""
    a, b = _team("Jar", "Jar 1"), _team("Kongsberg", "Kongsberg 1")
    candidate = {
        "tournaments": [
            _tournament("t1", "2025-09-06", [a, b]),
            _tournament("t2", "2025-10-11", [a, b]),
            _tournament("t3", "2026-01-24", [a, b]),
            _tournament("t4", "2026-02-21", [a, b]),
            _tournament("t5", "2026-03-21", [a, b]),
            _tournament("t6", "2026-04-18", [a, b]),
        ]
    }
    problem = _problem(teams=[a, b], targets={"before_christmas": 3, "after_christmas": 3})
    result = verify_candidate(candidate, problem)
    assert result["ok"] is True
    metrics = result["participation_metrics"]
    assert metrics["teams_exact_season_target"] == 2
    assert metrics["season_total_absolute_deviation"] == 0
    assert metrics["half_total_absolute_deviation"] == 4  # +1/-1 for each team
    assert metrics["teams_exact_half_targets"] == 0


def test_under_target_with_fewer_tournaments_than_target_is_proven_infeasible():
    a, b = _team("Jar", "Jar 1"), _team("Kongsberg", "Kongsberg 1")
    candidate = {"tournaments": [_tournament("t1", "2025-09-06", [a, b])]}
    problem = _problem(teams=[a, b], targets={"before_christmas": 3, "after_christmas": 3})
    evaluation = evaluate_participation(candidate, problem)
    under = [d for d in evaluation.deviations if d["direction"] == "under_target" and d["scope"] == "season"]
    assert under
    assert under[0]["avoidability"] == PROVEN_INFEASIBLE
    assert under[0]["evidence"]["reason"] == "fewer_tournaments_than_target"


def test_over_target_with_available_capacity_is_only_search_exhausted():
    a, b = _team("Jar", "Jar 1"), _team("Kongsberg", "Kongsberg 1")
    candidate = {
        "tournaments": [
            _tournament(f"t{i}", f"2026-0{i}-10", [a, b]) for i in range(1, 5)
        ]
    }
    problem = _problem(teams=[a, b], targets={"before_christmas": 1, "after_christmas": 1})
    evaluation = evaluate_participation(candidate, problem)
    over = [d for d in evaluation.deviations if d["direction"] == "over_target"]
    # The verifier itself never claims a target deviation is unavoidable.
    assert over and all(d["avoidability"] == BOUNDED_SEARCH_EXHAUSTED for d in over)


def test_explicit_hard_max_is_a_separate_hard_rule():
    a, b = _team("Jar", "Jar 1"), _team("Kongsberg", "Kongsberg 1")
    candidate = {
        "tournaments": [
            _tournament(f"t{i}", f"2026-0{i}-10", [a, b]) for i in range(1, 5)
        ]
    }
    problem = _problem(
        teams=[a, b],
        targets={"before_christmas": 3, "after_christmas": 3},
        participation_hard_max=3,
    )
    result = verify_candidate(candidate, problem)
    assert result["ok"] is False
    assert "participation_hard_max_exceeded" in {v["code"] for v in result["violations"]}
    # Hitting the hard max is not inferred from the target.
    problem["participation_hard_max"] = 4
    assert verify_candidate(candidate, problem)["ok"] is True


def test_matching_hard_max_waiver_downgrades_only_that_violation():
    a, b, c = _team("Jar", "Jar 1"), _team("Kongsberg", "Kongsberg 1"), _team("Skien", "Skien 1")
    candidate = {
        "tournaments": [
            _tournament("t1", "2026-01-10", [a, b]),
            _tournament("t2", "2026-02-10", [a, b]),
            _tournament("t3", "2026-03-10", [a, b]),
            _tournament("t4", "2026-04-10", [a, c]),
        ]
    }
    problem = _problem(
        teams=[a, b, c],
        targets={"before_christmas": 3, "after_christmas": 3},
        participation_hard_max=3,
    )
    # Only Jar 1 is over the hard maximum (4).
    baseline = verify_candidate(candidate, problem)
    assert [v["code"] for v in baseline["violations"]] == ["participation_hard_max_exceeded"]
    waiver = {
        "id": "w1",
        "rule": "participation_hard_max_exceeded",
        "scope": {"team": {"club": "Jar", "label": "Jar 1", "age_group": U11}, "tournament_id": "t4", "half": None},
        "configured_value": 3,
        "allowed_value": 4,
        "active": True,
    }
    waiver["scope_fingerprint"] = scope_fingerprint(
        rule="participation_hard_max_exceeded",
        team=waiver["scope"]["team"],
        tournament_id="t4",
        half=None,
        configured_value=3,
        allowed_value=4,
    )
    problem["operator_waivers"] = [waiver]
    result = verify_candidate(candidate, problem)
    assert [v["code"] for v in result["waived_violations"]] == ["participation_hard_max_exceeded"]
    assert all(v["code"] != "participation_hard_max_exceeded" for v in result["violations"])
    assert result["ok"] is True


def test_search_evidence_can_override_the_default_classification():
    a, b = _team("Jar", "Jar 1"), _team("Kongsberg", "Kongsberg 1")
    candidate = {"tournaments": [_tournament("t1", "2025-09-06", [a, b])]}
    problem = _problem(teams=[a, b], targets={"before_christmas": 3, "after_christmas": 3})
    evaluation = evaluate_participation(
        candidate,
        problem,
        search_evidence={
            ("Jar", "Jar 1", U11): {"status": AVOIDABLE, "coverage": "found a 3+3 alternative"}
        },
    )
    jar = [d for d in evaluation.deviations if d["team"] == "Jar 1"]
    assert jar and all(d["avoidability"] == AVOIDABLE for d in jar)
    assert evaluation.metrics["avoidable_deviation_count"] >= 1


def test_replan_four_teams_one_over_half_target_still_hard_valid():
    """Regression for the 2026-09-17 canonical replan: a few teams one
    tournament above their half target must not make an otherwise usable
    candidate a hard verifier failure (issue #376)."""
    teams = [_team(club, f"{club} 1") for club in ("Jar", "Kongsberg", "Skien", "Ringerike")]
    tournaments = [
        _tournament(f"before{i}", f"2025-{month:02d}-10", teams)
        for i, month in enumerate((9, 10, 11, 12), start=1)
    ] + [
        _tournament(f"after{i}", f"2026-{month:02d}-10", teams)
        for i, month in enumerate((1, 2, 3), start=1)
    ]
    candidate = {"tournaments": tournaments}
    problem = _problem(teams=teams, targets={"before_christmas": 3, "after_christmas": 3})

    result = verify_candidate(candidate, problem)
    assert result["ok"] is True
    assert result["violations"] == []
    over = [
        d
        for d in result["participation_deviations"]
        if d["direction"] == "over_target" and d["scope"] == "before_christmas"
    ]
    assert len(over) == len(teams)
    assert all(d["deviation"] == 1 for d in over)
    assert all(d["avoidability"] == BOUNDED_SEARCH_EXHAUSTED for d in over)


def test_cross_half_compensation_capacity_is_surfaced_as_evidence():
    a, b = _team("Jar", "Jar 1"), _team("Kongsberg", "Kongsberg 1")
    candidate = {
        "tournaments": [
            _tournament("t1", "2025-09-06", [a, b]),
            _tournament("t2", "2026-01-24", [a, b]),
            _tournament("t3", "2026-02-21", [a, b]),
        ]
    }
    problem = _problem(teams=[a, b], targets={"before_christmas": 3, "after_christmas": 3})
    evaluation = evaluate_participation(candidate, problem)
    before_under = [
        d
        for d in evaluation.deviations
        if d["scope"] == "before_christmas" and d["direction"] == "under_target"
    ]
    assert before_under
    assert before_under[0]["evidence"]["cross_half_capacity_available"] is True
    assert before_under[0]["evidence"]["cross_half"] == "after_christmas"
    assert evaluation.metrics["cross_half_compensation_opportunity_count"] >= 1


def test_publication_readiness_flags_over_target_deviation_as_review():
    from tournament_scheduler.final_verification import publication_readiness

    readiness = publication_readiness(
        {
            "violations": [],
            "participation_deviations": [
                {"direction": "over_target", "team": "Jar 1", "scope": "season"},
            ],
        }
    )
    assert readiness["status"] == "REVIEW_REQUIRED"
    assert {"code": "participation_target_deviation", "count": 1} in readiness["reasons"]


def test_worse_participation_blocks_dominance_over_a_quality_gain():
    """A candidate with materially worse bounded participation deviation must
    not be promoted merely because it improves a lower-priority quality metric
    (issue #376)."""
    from tournament_scheduler.stage3_ab import build_ab_report

    a, b = _team("Jar", "Jar 1"), _team("Kongsberg", "Kongsberg 1")
    problem = _problem(teams=[a, b], targets={"before_christmas": 3, "after_christmas": 3})
    # Baseline meets the season target exactly (2 before + 4 after).
    baseline = {
        "tournaments": [_tournament(f"b{i}", date, [a, b]) for i, date in enumerate(
            ["2025-09-06", "2025-10-11", "2026-01-24", "2026-02-21", "2026-03-21", "2026-04-18"], start=1
        )]
    }
    # Candidate drops two participations for Kongsberg 1 (worse deviation) while
    # introducing a second, near-perfect matchup set.
    candidate = {
        "tournaments": [
            _tournament("c1", "2025-09-06", [a, b]),
            _tournament("c2", "2025-10-11", [a, b]),
            _tournament("c3", "2026-01-24", [a, b]),
            _tournament("c4", "2026-02-21", [a, b]),
        ]
    }
    report = build_ab_report(baseline, candidate, problem)
    participation_regressions = [
        metric["metric"]
        for metric in report["overall_comparison"]["metrics"]
        if metric["regressed"] and metric["metric"].startswith("participation.")
    ]
    assert "participation.season_total_absolute_deviation" in participation_regressions
    assert report["dominates_baseline"] is False
    assert report["production_ready"] is False
