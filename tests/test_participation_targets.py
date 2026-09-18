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
    CLUB_POOL_COMPLETE,
    INTRA_CLUB_DISTRIBUTION,
    MATERIAL_CLUB_POOL_SHORTFALL,
    MINOR_CLUB_POOL_SHORTFALL,
    OPERATOR_ACCEPTED,
    PROVEN_INFEASIBLE,
    SINGLE_TEAM_DEVIATION,
    counts_as_unresolved_shortfall,
    evaluate_participation,
    evidence_covers_deviation,
    resolve_half_target,
    resolve_hard_max,
    resolve_season_target,
    search_evidence_from_acceptances,
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
    assert "participation.club_pool_unresolved_season_total_absolute_deviation" in participation_regressions
    assert report["dominates_baseline"] is False
    assert report["production_ready"] is False


# ---------------------------------------------------------------------------
# Operator acceptance / search-evidence coverage (issue #378)
# ---------------------------------------------------------------------------


def test_evidence_covers_deviation_is_scope_and_magnitude_bound() -> None:
    acceptance = {
        "status": OPERATOR_ACCEPTED,
        "scope": "before_christmas",
        "direction": "under_target",
        "target": 3,
        "accepted_deviation": -1,
    }

    assert evidence_covers_deviation(
        acceptance, scope="before_christmas", direction="under_target", actual=2, target=3
    )
    # Same deviation, but a different scope for the same team does not apply.
    assert not evidence_covers_deviation(
        acceptance, scope="after_christmas", direction="under_target", actual=2, target=3
    )
    # An improvement is still covered; a worse deviation is not.
    assert evidence_covers_deviation(
        acceptance, scope="before_christmas", direction="under_target", actual=3, target=3
    )
    assert not evidence_covers_deviation(
        acceptance, scope="before_christmas", direction="under_target", actual=1, target=3
    )
    # A target change or direction flip invalidates the old acceptance.
    assert not evidence_covers_deviation(
        acceptance, scope="before_christmas", direction="under_target", actual=2, target=4
    )
    assert not evidence_covers_deviation(
        acceptance, scope="before_christmas", direction="over_target", actual=4, target=3
    )
    # An entry that declares no coverage fields keeps the original unconditional
    # behaviour for callers that attach a plain status.
    assert evidence_covers_deviation(
        {"status": OPERATOR_ACCEPTED},
        scope="season",
        direction="over_target",
        actual=9,
        target=1,
    )


def test_operator_acceptance_reclassifies_a_deviation_through_the_verifier() -> None:
    a = _team("Nordby", "Nordby 1")
    b = _team("Sorby", "Sorby 1")
    candidate = {"tournaments": [_tournament("t1", "2025-09-06", [a, b])]}
    problem: dict[str, Any] = {
        "teams": [a, b],
        "age_groups": [U11],
        "target_tournament_count": 3,
    }
    identity = ("Nordby", "Nordby 1", U11)

    baseline = evaluate_participation(candidate, problem).deviations
    assert baseline
    assert all(deviation["avoidability"] != OPERATOR_ACCEPTED for deviation in baseline)

    evidence = search_evidence_from_acceptances(
        [
            {
                "status": OPERATOR_ACCEPTED,
                "club": "Nordby",
                "label": "Nordby 1",
                "age_group": U11,
                "scope": "season",
                "direction": "under_target",
                "target": 3,
                "accepted_deviation": -3,
            }
        ]
    )
    assert set(evidence) == {identity}

    accepted = evaluate_participation(candidate, problem, search_evidence=evidence).deviations
    season = next(deviation for deviation in accepted if deviation["scope"] == "season")
    assert season["avoidability"] == OPERATOR_ACCEPTED
    assert season["evidence"]["scope"] == "season"
    assert season["evidence"]["accepted_deviation"] == -3
    # A persisted acceptance therefore also reclassifies through verify_candidate
    # when it is injected into the planning problem.
    verification = verify_candidate(
        candidate, {**problem, "participation_search_evidence": evidence}
    )
    injected = next(
        deviation
        for deviation in verification["participation_deviations"]
        if deviation["scope"] == "season"
    )
    assert injected["avoidability"] == OPERATOR_ACCEPTED


# ---------------------------------------------------------------------------
# Club x age-group player-pool classification
# ---------------------------------------------------------------------------

_JU10 = "JU10"


def _after_christmas_problem(*teams: dict[str, Any]) -> dict[str, Any]:
    return {
        "start_date": "2025-01-01",
        "end_date": "2026-12-31",
        "teams": [dict(t, target_tournament_count=None) for t in teams],
        "parallel_games": {},
        "participation_targets_by_age_group": {_JU10: {"before_christmas": 0, "after_christmas": 4}},
    }


def _after_christmas_candidate(
    a: dict[str, Any], b: dict[str, Any], c: dict[str, Any], *, a_count: int, b_count: int
) -> dict[str, Any]:
    """Build a valid after-Christmas skeleton with exact Jar A/B counts.

    Each Jar team shares a tournament with an unrelated club ``c`` (so every
    tournament has the effective minimum size) and never with each other; the
    Jar pool total is therefore exactly ``a_count + b_count``.
    """
    dates = [
        "2026-01-10",
        "2026-01-17",
        "2026-01-24",
        "2026-02-07",
        "2026-02-14",
        "2026-02-21",
        "2026-03-07",
        "2026-03-14",
    ]
    tournaments: list[dict[str, Any]] = []
    for index, date in enumerate(dates):
        if index < a_count:
            teams = [a, c]
        elif index < a_count + b_count:
            teams = [b, c]
        else:
            break
        tournaments.append(_tournament(f"t{index}", date, teams, age_group=_JU10))
    return {"tournaments": tournaments}


def _pool(result: Any, scope: str, club: str) -> dict[str, Any]:
    pools = result["participation_club_pools"] if isinstance(result, dict) else result.club_pools
    return next(
        pool for pool in pools if pool["scope"] == scope and pool["club"] == club
    )


def test_club_pool_balanced_aggregate_with_uneven_labels_is_intra_club_distribution():
    """5 + 3 = 8/8: the club player pool got its capacity; only labels differ."""
    a, b, c = _team("Jar", "Jar Hvit", _JU10), _team("Jar", "Jar Blå", _JU10), _team(
        "Kongsberg", "Kongsberg 1", _JU10
    )
    problem = _after_christmas_problem(a, b, c)
    candidate = _after_christmas_candidate(a, b, c, a_count=5, b_count=3)
    result = evaluate_participation(candidate, problem)

    pool = _pool(result, "after_christmas", "Jar")
    assert pool["club_pool_actual"] == 8
    assert pool["club_pool_target"] == 8
    assert pool["classification"] == INTRA_CLUB_DISTRIBUTION
    assert pool["planning_significance"] == "informational"
    assert pool["counts_as_unresolved_shortfall"] is False
    assert pool["registered_team_count"] == 2
    # Exact per-team counts stay visible and auditable.
    distribution = {item["team"]: item for item in pool["team_distribution"]}
    assert distribution["Jar Hvit"]["actual"] == 5
    assert distribution["Jar Blå"]["actual"] == 3
    # The intra-club imbalance is not counted as an unresolved club-pool deficit.
    assert result.metrics["club_pool_intra_distribution_count"] >= 1
    assert result.metrics["club_pool_unresolved_shortfall_count"] == 0
    assert all(
        deviation["counts_as_unresolved_shortfall"] is False
        for deviation in result.deviations
        if deviation["scope"] == "after_christmas"
    )


def test_club_pool_small_shortfall_is_minor_and_distinct_from_single_team():
    """4 + 3 = 7/8 is a minor multi-team residual, not a single-team 3/4."""
    a, b, c = _team("Jar", "Jar Hvit", _JU10), _team("Jar", "Jar Blå", _JU10), _team(
        "Kongsberg", "Kongsberg 1", _JU10
    )
    problem = _after_christmas_problem(a, b, c)
    candidate = _after_christmas_candidate(a, b, c, a_count=4, b_count=3)
    result = evaluate_participation(candidate, problem)

    pool = _pool(result, "after_christmas", "Jar")
    assert (pool["club_pool_actual"], pool["club_pool_target"]) == (7, 8)
    assert pool["classification"] == MINOR_CLUB_POOL_SHORTFALL
    assert pool["planning_significance"] == "minor"
    assert pool["counts_as_unresolved_shortfall"] is True
    assert result.metrics["club_pool_minor_shortfall_count"] >= 1


def test_club_pool_material_shortfall_stays_material():
    """3 + 2 = 5/8 is a material club-pool shortfall, not hidden by flexibility."""
    a, b, c = _team("Jar", "Jar Hvit", _JU10), _team("Jar", "Jar Blå", _JU10), _team(
        "Kongsberg", "Kongsberg 1", _JU10
    )
    problem = _after_christmas_problem(a, b, c)
    candidate = _after_christmas_candidate(a, b, c, a_count=3, b_count=2)
    result = evaluate_participation(candidate, problem)

    pool = _pool(result, "after_christmas", "Jar")
    assert (pool["club_pool_actual"], pool["club_pool_target"]) == (5, 8)
    assert pool["classification"] == MATERIAL_CLUB_POOL_SHORTFALL
    assert pool["planning_significance"] == "material"
    assert pool["counts_as_unresolved_shortfall"] is True


def test_single_team_club_keeps_per_team_semantics():
    """A single-team club's per-team deviation *is* its club-pool deviation."""
    solo = _team("Kongsberg", "Kongsberg 1", _JU10)
    other = _team("Jar", "Jar 1", _JU10)
    problem = _after_christmas_problem(solo, other)
    candidate = {
        "tournaments": [
            _tournament(f"t{i}", f"2026-01-{10 + i:02d}", [solo, other], age_group=_JU10)
            for i in range(3)
        ]
    }
    result = evaluate_participation(candidate, problem)
    pool = _pool(result, "after_christmas", "Kongsberg")
    assert pool["registered_team_count"] == 1
    assert pool["classification"] == SINGLE_TEAM_DEVIATION
    assert pool["counts_as_unresolved_shortfall"] is True


def test_counts_as_unresolved_shortfall_only_for_genuine_deficits():
    assert counts_as_unresolved_shortfall(INTRA_CLUB_DISTRIBUTION) is False
    assert counts_as_unresolved_shortfall(CLUB_POOL_COMPLETE) is False
    assert counts_as_unresolved_shortfall(MINOR_CLUB_POOL_SHORTFALL) is True
    assert counts_as_unresolved_shortfall(MATERIAL_CLUB_POOL_SHORTFALL) is True
    assert counts_as_unresolved_shortfall(SINGLE_TEAM_DEVIATION, direction="under_target") is True
    assert counts_as_unresolved_shortfall(SINGLE_TEAM_DEVIATION, direction="over_target") is False


def test_verify_candidate_carries_club_pool_classification_and_readiness_ignores_redistribution():
    """The export/readiness layer receives the classification and does not
    count pure intra-club redistribution as an unresolved shortfall."""
    from tournament_scheduler.final_verification import publication_readiness

    a, b, c = _team("Jar", "Jar Hvit", _JU10), _team("Jar", "Jar Blå", _JU10), _team(
        "Kongsberg", "Kongsberg 1", _JU10
    )
    problem = _after_christmas_problem(a, b, c)
    candidate = _after_christmas_candidate(a, b, c, a_count=5, b_count=3)
    result = verify_candidate(candidate, problem)
    assert result["ok"] is True

    jar_entries = [
        entry for entry in result["manual_participation_placements"] if entry["club"] == "Jar"
    ]
    assert jar_entries
    assert all(
        entry["club_pool_classification"] == INTRA_CLUB_DISTRIBUTION for entry in jar_entries
    )
    assert all(entry["counts_as_unresolved_shortfall"] is False for entry in jar_entries)

    readiness = publication_readiness(result)
    reason_codes = {reason["code"] for reason in readiness["reasons"]}
    # No genuine under-target club/player-pool deficit remains (Jar is
    # aggregate-complete; Kongsberg is over target), so intra-club
    # redistribution must not create a `participation_shortfalls` reason.
    assert "participation_shortfalls" not in reason_codes
    assert {item["code"] for item in readiness["informational_reasons"]} == {
        "intra_club_participation_distribution"
    }


def test_publication_readiness_is_publishable_when_only_intra_club_distribution_remains():
    """Aggregate-complete 5 + 3 must not block publication by itself."""
    from tournament_scheduler.final_verification import publication_readiness

    result = {
        "violations": [],
        "manual_participation_placements": [
            {
                "club": "Jar",
                "label": "Jar Blå",
                "age_group": _JU10,
                "actual": "3",
                "target": "4",
                "club_pool_classification": INTRA_CLUB_DISTRIBUTION,
                "counts_as_unresolved_shortfall": False,
            }
        ],
        "participation_deviations": [
            {
                "club": "Jar",
                "team": "Jar Hvit",
                "direction": "over_target",
                "club_pool_classification": INTRA_CLUB_DISTRIBUTION,
            }
        ],
    }
    readiness = publication_readiness(result)
    assert readiness["status"] == "PUBLISHABLE"
    assert readiness["publishable"] is True
    assert readiness["reasons"] == []
    assert readiness["informational_reasons"] == [
        {"code": "intra_club_participation_distribution", "count": 1}
    ]

