"""Unit tests for tournament_scheduler.cli.plan_critic.generate_critic_summary."""
from __future__ import annotations

from datetime import date


from tournament_scheduler.cli.plan_critic import count_issues_from_plan, generate_critic_summary
from tournament_scheduler.models import SeasonPlan, Tournament, Team


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tournament(
    host_club: str,
    year: int,
    month: int,
    day: int,
    age_group: str = "U10",
) -> Tournament:
    """Construct a minimal Tournament for testing."""
    return Tournament(
        date=date(year, month, day),
        arena=f"{host_club} arena",
        age_group=age_group,
        host_club=host_club,
    )


def _make_plan(**kwargs) -> SeasonPlan:
    """Build a SeasonPlan with sensible defaults, overrideable via kwargs."""
    defaults: dict = {
        "tournaments": [],
        "team_game_counts": {},
        "game_count_spread": 0,
        "month_balance_score": 1.0,
        "fairness_gate": {},
        "arena_day_collisions": [],
    }
    defaults.update(kwargs)
    return SeasonPlan(**defaults)


# ---------------------------------------------------------------------------
# Happy path: no issues
# ---------------------------------------------------------------------------


def test_no_issues_returns_empty_list():
    plan = _make_plan()
    result = generate_critic_summary(plan)
    assert result == []


def test_no_issues_balanced_plan():
    tournaments = [
        _make_tournament("Kongsberg", 2027, 1, 15),
        _make_tournament("Ringerike", 2027, 2, 12),
        _make_tournament("Skien", 2027, 3, 19),
    ]
    plan = _make_plan(
        tournaments=tournaments,
        team_game_counts={"Jar 1": 6, "Holmen 1": 6},
        game_count_spread=0,
        month_balance_score=0.95,
        fairness_gate={"status": "pass", "metrics": []},
        arena_day_collisions=[],
    )
    result = generate_critic_summary(plan)
    assert result == []


# ---------------------------------------------------------------------------
# Hosting clump detection
# ---------------------------------------------------------------------------


def test_hosting_clump_detected():
    """3 tournaments by same club in same month should trigger an issue."""
    tournaments = [
        _make_tournament("Kongsberg", 2027, 3, 5),
        _make_tournament("Kongsberg", 2027, 3, 12),
        _make_tournament("Kongsberg", 2027, 3, 19),
    ]
    plan = _make_plan(tournaments=tournaments)
    result = generate_critic_summary(plan)
    assert len(result) >= 1
    assert any("Kongsberg" in issue and "3" in issue for issue in result)


def test_hosting_clump_exactly_two_is_ok():
    """Exactly 2 tournaments by same club in same month is fine."""
    tournaments = [
        _make_tournament("Kongsberg", 2027, 3, 5),
        _make_tournament("Kongsberg", 2027, 3, 12),
    ]
    plan = _make_plan(tournaments=tournaments)
    result = generate_critic_summary(plan)
    clump_issues = [i for i in result if "Kongsberg" in i and "hosts" in i]
    assert clump_issues == []


def test_cancelled_tournaments_excluded_from_clump():
    """Cancelled tournaments must not count toward hosting clumps."""
    t1 = _make_tournament("Kongsberg", 2027, 3, 5)
    t2 = _make_tournament("Kongsberg", 2027, 3, 12)
    t3 = _make_tournament("Kongsberg", 2027, 3, 19)
    t3.cancelled = True
    plan = _make_plan(tournaments=[t1, t2, t3])
    result = generate_critic_summary(plan)
    clump_issues = [i for i in result if "Kongsberg" in i and "hosts" in i]
    assert clump_issues == []


def test_same_day_multi_age_group_counts_as_one_hosting_day():
    """4 tournaments across 2 days (2 per day) = 2 distinct days → no clump."""
    tournaments = [
        _make_tournament("Kongsberg", 2027, 3, 5, age_group="U10"),
        _make_tournament("Kongsberg", 2027, 3, 5, age_group="U12"),
        _make_tournament("Kongsberg", 2027, 3, 12, age_group="U10"),
        _make_tournament("Kongsberg", 2027, 3, 12, age_group="U12"),
    ]
    plan = _make_plan(tournaments=tournaments)
    result = generate_critic_summary(plan)
    clump_issues = [i for i in result if "Kongsberg" in i and "hosts" in i]
    assert clump_issues == [], (
        "Two hosting days should not trigger a clump even with 4 raw tournaments"
    )


def test_three_distinct_hosting_days_triggers_clump():
    """3 tournaments across 3 separate days = 3 distinct days → clump flagged."""
    tournaments = [
        _make_tournament("Ringerike", 2027, 4, 3),
        _make_tournament("Ringerike", 2027, 4, 10),
        _make_tournament("Ringerike", 2027, 4, 17),
    ]
    plan = _make_plan(tournaments=tournaments)
    result = generate_critic_summary(plan)
    clump_issues = [i for i in result if "Ringerike" in i and "hosts" in i]
    assert len(clump_issues) >= 1, "Three distinct hosting days should trigger a clump"
    assert "3" in clump_issues[0], "Issue should mention the count of 3"


def test_same_day_plus_one_extra_day_is_two_hosting_days():
    """3 tournaments: 2 on same day + 1 on different day = 2 distinct days → no clump."""
    tournaments = [
        _make_tournament("Skien", 2027, 5, 1, age_group="U10"),
        _make_tournament("Skien", 2027, 5, 1, age_group="U8"),
        _make_tournament("Skien", 2027, 5, 8, age_group="U12"),
    ]
    plan = _make_plan(tournaments=tournaments)
    result = generate_critic_summary(plan)
    clump_issues = [i for i in result if "Skien" in i and "hosts" in i]
    assert clump_issues == [], (
        "Two distinct hosting days should not trigger a clump"
    )


def test_three_age_groups_on_three_days_no_clump():
    """A club hosting one tournament per age group per month should not trigger a clump.

    3 different age groups on 3 separate days = 1 day per age group → no over-hosting
    per age group. The old cross-age-group counting would have flagged this as 3 days.
    """
    tournaments = [
        _make_tournament("Holmen", 2027, 9, 4, age_group="U7"),
        _make_tournament("Holmen", 2027, 9, 11, age_group="U9"),
        _make_tournament("Holmen", 2027, 9, 18, age_group="U11"),
    ]
    plan = _make_plan(tournaments=tournaments)
    result = generate_critic_summary(plan)
    clump_issues = [i for i in result if "Holmen" in i and "hosts" in i]
    assert clump_issues == [], (
        "Hosting one tournament per age group per month should not be flagged as a clump"
    )


def test_u10_clump_triggers_while_u12_on_same_days_does_not():
    """U10 clump is flagged while U12 on the same days stays under the threshold.

    3 U10 tournaments on 3 separate days triggers a U10 clump.
    1 U12 tournament (on one of those same days) does not trigger a U12 clump.
    The issue message must reference U10, not U12.
    """
    tournaments = [
        # U10 on 3 separate days → clump
        _make_tournament("Ringerike", 2027, 10, 7, age_group="U10"),
        _make_tournament("Ringerike", 2027, 10, 14, age_group="U10"),
        _make_tournament("Ringerike", 2027, 10, 21, age_group="U10"),
        # U12 on one day only → no clump
        _make_tournament("Ringerike", 2027, 10, 7, age_group="U12"),
    ]
    plan = _make_plan(tournaments=tournaments)
    result = generate_critic_summary(plan)
    clump_issues = [i for i in result if "Ringerike" in i and "hosts" in i]
    assert len(clump_issues) == 1, "Only U10 should trigger a clump, not U12"
    assert "U10" in clump_issues[0], "Issue should reference U10"
    assert "U12" not in clump_issues[0], "Issue should not reference U12"
    assert "3" in clump_issues[0], "Issue should report count of 3"


# ---------------------------------------------------------------------------
# Game count outlier detection
# ---------------------------------------------------------------------------


def test_game_count_outlier_detected():
    """Spread > 4 should trigger an outlier issue."""
    plan = _make_plan(
        team_game_counts={"Jar 1": 12, "Holmen 1": 6},
        game_count_spread=6,
    )
    result = generate_critic_summary(plan)
    assert len(result) >= 1
    assert any("spread" in issue.lower() or "game" in issue.lower() for issue in result)


def test_game_count_spread_of_four_is_ok():
    """Spread == 4 is at the boundary and should NOT trigger an issue."""
    plan = _make_plan(
        team_game_counts={"Jar 1": 10, "Holmen 1": 6},
        game_count_spread=4,
    )
    result = generate_critic_summary(plan)
    outlier_issues = [i for i in result if "spread" in i.lower() or "redistribute" in i.lower()]
    assert outlier_issues == []


def test_game_count_outlier_scoped_per_age_group():
    """U12 imbalance should fire; balanced U7 group should not appear in issues."""
    # Two tournaments — one per age group — so the critic can map team labels.
    u7_t = _make_tournament("Jar", 2027, 1, 15, age_group="U7")
    u7_t.teams = [
        Team(club="Jar", label="Jar U7", age_group="U7"),
        Team(club="Holmen", label="Holmen U7", age_group="U7"),
    ]
    u12_t = _make_tournament("Kongsberg", 2027, 2, 5, age_group="U12")
    u12_t.teams = [
        Team(club="Kongsberg", label="Kongsberg U12", age_group="U12"),
        Team(club="Skien", label="Skien U12", age_group="U12"),
    ]
    plan = _make_plan(
        tournaments=[u7_t, u12_t],
        # U7 balanced (spread 0), U12 imbalanced (spread 6)
        team_game_counts={
            "Jar U7": 6,
            "Holmen U7": 6,
            "Kongsberg U12": 12,
            "Skien U12": 6,
        },
        game_count_spread=6,
        game_count_spread_by_age_group={"U7": 0, "U12": 6},
    )
    result = generate_critic_summary(plan)
    spread_issues = [i for i in result if "spread" in i.lower() or "redistribute" in i.lower()]
    # U12 issue should fire
    assert any("U12" in issue for issue in spread_issues), (
        f"Expected a U12 spread issue but got: {spread_issues}"
    )
    # U7 should NOT appear in spread issues
    assert not any("U7" in issue for issue in spread_issues), (
        f"U7 is balanced — should not appear in spread issues: {spread_issues}"
    )


def test_stored_spread_by_age_group_used_over_global():
    """When game_count_spread_by_age_group is set, its values are reported even
    if the raw team_game_counts difference would give a different number.

    This test sets U10 stored spread = 7 while the actual count difference is 2
    (which would be below the threshold). The critic should report a spread issue
    citing the stored value of 7.
    """
    u10_t = _make_tournament("Jar", 2027, 3, 5, age_group="U10")
    u10_t.teams = [
        Team(club="Jar", label="Jar U10", age_group="U10"),
        Team(club="Holmen", label="Holmen U10", age_group="U10"),
    ]
    plan = _make_plan(
        tournaments=[u10_t],
        team_game_counts={"Jar U10": 8, "Holmen U10": 6},  # diff = 2, below threshold
        game_count_spread=2,
        game_count_spread_by_age_group={"U10": 7},  # stored says 7 — above threshold
    )
    result = generate_critic_summary(plan)
    spread_issues = [i for i in result if "spread" in i.lower() or "redistribute" in i.lower()]
    assert any("7" in issue for issue in spread_issues), (
        f"Expected stored spread value 7 to be reported, got: {spread_issues}"
    )


def test_stored_spread_by_age_group_suppresses_false_positive():
    """When game_count_spread_by_age_group reports 0 for an age group, no issue
    is raised even if the global game_count_spread is high.

    The stored per-age-group value is the source of truth.
    """
    u10_t = _make_tournament("Kongsberg", 2027, 4, 12, age_group="U10")
    u10_t.teams = [
        Team(club="Kongsberg", label="Kongsberg U10", age_group="U10"),
        Team(club="Skien", label="Skien U10", age_group="U10"),
    ]
    plan = _make_plan(
        tournaments=[u10_t],
        team_game_counts={"Kongsberg U10": 8, "Skien U10": 8},
        game_count_spread=8,  # misleadingly high global
        game_count_spread_by_age_group={"U10": 0},  # per-age-group says balanced
    )
    result = generate_critic_summary(plan)
    spread_issues = [i for i in result if "spread" in i.lower() or "redistribute" in i.lower()]
    assert spread_issues == [], (
        f"Stored per-age-group spread of 0 should suppress any spread issue: {spread_issues}"
    )


# ---------------------------------------------------------------------------
# Fairness gate
# ---------------------------------------------------------------------------


def test_fairness_gate_fail_detected():
    """A 'fail' metric in fairness_gate should produce a fail issue."""
    gate = {
        "status": "fail",
        "metrics": [
            {
                "status": "fail",
                "label": "Travel distance",
                "value": 250,
                "threshold": 200,
                "detail": "Max travel exceeds threshold",
            }
        ],
    }
    plan = _make_plan(fairness_gate=gate)
    result = generate_critic_summary(plan)
    assert len(result) >= 1
    assert any("FAIL" in issue or "fail" in issue.lower() for issue in result)


def test_fairness_gate_warn_detected():
    """A 'warn' metric should produce a warning issue."""
    gate = {
        "status": "warn",
        "metrics": [
            {
                "status": "warn",
                "label": "Home/away balance",
                "value": 0.55,
                "threshold": 0.6,
                "detail": "",
            }
        ],
    }
    plan = _make_plan(fairness_gate=gate)
    result = generate_critic_summary(plan)
    assert len(result) >= 1
    assert any("warning" in issue.lower() or "warn" in issue.lower() for issue in result)


def test_fairness_gate_pass_no_issue():
    """Pass status with all-pass metrics should produce no issue."""
    gate = {
        "status": "pass",
        "metrics": [
            {"status": "pass", "label": "Travel distance", "value": 100, "threshold": 200, "detail": ""},
        ],
    }
    plan = _make_plan(fairness_gate=gate)
    result = generate_critic_summary(plan)
    assert result == []


# ---------------------------------------------------------------------------
# Arena-day collision detection
# ---------------------------------------------------------------------------


def test_arena_day_collision_detected():
    """Non-empty arena_day_collisions should produce a collision issue."""
    collisions = [
        {"date": "2027-03-12", "arena": "Kongsberg", "age_group": "U10"},
        {"date": "2027-03-12", "arena": "Kongsberg", "age_group": "U12"},
    ]
    plan = _make_plan(arena_day_collisions=collisions)
    result = generate_critic_summary(plan)
    assert len(result) >= 1
    assert any("collision" in issue.lower() or "2" in issue for issue in result)


def test_no_arena_day_collisions_no_issue():
    plan = _make_plan(arena_day_collisions=[])
    result = generate_critic_summary(plan)
    assert result == []


# ---------------------------------------------------------------------------
# Month balance
# ---------------------------------------------------------------------------


def test_low_month_balance_detected():
    plan = _make_plan(month_balance_score=0.4)
    result = generate_critic_summary(plan)
    assert len(result) >= 1
    assert any("0.40" in issue or "unevenly" in issue.lower() for issue in result)


def test_month_balance_at_threshold_ok():
    """score == 0.6 is right at the limit — should NOT trigger an issue."""
    plan = _make_plan(month_balance_score=0.6)
    result = generate_critic_summary(plan)
    balance_issues = [i for i in result if "balance" in i.lower() or "unevenly" in i.lower()]
    assert balance_issues == []


# ---------------------------------------------------------------------------
# Result is capped at 5
# ---------------------------------------------------------------------------


def test_result_never_exceeds_5():
    """Even with every detector firing, at most 5 issues are returned."""
    tournaments = [
        _make_tournament("Kongsberg", 2027, 3, 5),
        _make_tournament("Kongsberg", 2027, 3, 12),
        _make_tournament("Kongsberg", 2027, 3, 19),
        _make_tournament("Ringerike", 2027, 4, 9),
        _make_tournament("Ringerike", 2027, 4, 16),
        _make_tournament("Ringerike", 2027, 4, 23),
    ]
    gate = {
        "status": "fail",
        "metrics": [
            {"status": "fail", "label": "M1", "value": 1, "threshold": 0, "detail": "d1"},
            {"status": "fail", "label": "M2", "value": 2, "threshold": 0, "detail": "d2"},
            {"status": "warn", "label": "M3", "value": 3, "threshold": 2, "detail": "d3"},
        ],
    }
    collisions = [{"date": "2027-03-12", "arena": "Kongsberg", "age_group": "U10"}]
    plan = _make_plan(
        tournaments=tournaments,
        team_game_counts={"A": 15, "B": 5},
        game_count_spread=10,
        month_balance_score=0.3,
        fairness_gate=gate,
        arena_day_collisions=collisions,
    )
    result = generate_critic_summary(plan)
    assert len(result) <= 5


# ---------------------------------------------------------------------------
# count_issues_from_plan
# ---------------------------------------------------------------------------


def test_count_issues_from_plan_with_season_plan_object():
    """Passing a SeasonPlan object should produce the same count as a plain dict."""
    plan = _make_plan(month_balance_score=0.4)  # triggers one issue
    count = count_issues_from_plan(plan)
    assert count >= 1


def test_count_issues_from_plan_with_plain_dict():
    """Passing a pre-serialised dict should work without any SeasonPlan."""
    plan_dict = {
        "month_balance_score": 0.4,
        "tournaments": [],
        "fairness_gate": None,
        "arena_day_collisions": [],
        "game_count_spread": 0,
    }
    count = count_issues_from_plan(plan_dict)
    assert count >= 1


def test_count_issues_from_plan_empty_dict_returns_zero():
    """An empty dict (e.g. checkpoint missing) should return 0, not raise."""
    count = count_issues_from_plan({})
    assert count == 0


def test_count_issues_from_plan_no_issues():
    """A healthy plan should return 0."""
    plan = _make_plan()
    count = count_issues_from_plan(plan)
    assert count == 0
