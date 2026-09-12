from datetime import date

from tournament_scheduler.html.renderers.rules_table import render_rules_table_html
from tournament_scheduler.models import SeasonPlan, Team, Tournament
from tournament_scheduler.rules_model import build_rules_model, group_rules_by_type, rules_summary_counts


def _plan(**overrides) -> SeasonPlan:
    plan = SeasonPlan()
    for key, value in overrides.items():
        setattr(plan, key, value)
    return plan


def test_build_rules_model_is_empty_for_bare_plan():
    rules = build_rules_model(_plan())
    ids = {rule["id"] for rule in rules}
    # The always-present required-obligation rules report "Oppfylt" even
    # with no data, and the plan-derived hard constraints/static rows are
    # trivially satisfied on an empty plan, so no metric/shared-host rules
    # appear.
    assert ids == {
        "hosting_obligation_coverage",
        "external_calendar_conflicts",
        "participation_shortfalls",
        "age_group_exact_match",
        "no_same_date_double_participation",
        "participation_target_exceeded",
        "arena_day_collisions",
        "date_within_planning_window",
        "banned_dates_not_used",
        "excluded_host_clubs_not_used",
        "locked_dates_preserved",
        "pinned_tournaments_preserved",
        "calendar_trust_by_host",
        "registered_teams_only",
        "tournament_capacity",
        "christmas_half_boundary",
    }
    for rule in rules:
        assert rule["ok"] is True


def test_build_rules_model_reads_canonical_fairness_gate_split():
    plan = _plan(
        fairness_gate={
            "policy_gate": {
                "metrics": [
                    {
                        "key": "hosting_deviation",
                        "label": "Hjemmekampfordeling",
                        "provenance": "configured",
                        "status": "warn",
                        "value": 1,
                        "threshold": 1,
                        "detail": "innenfor terskel",
                    },
                ]
            },
            "measurements": [
                {
                    "key": "opponent_diversity",
                    "label": "Motstandervariasjon",
                    "provenance": "measurement",
                    "status": "pass",
                    "value": 0.9,
                    "unit": "",
                    "detail": "",
                }
            ],
        }
    )
    rules = build_rules_model(plan)
    by_id = {rule["id"]: rule for rule in rules}

    obligation_rule = by_id["metric_hosting_deviation"]
    assert obligation_rule["type"] == "required_obligation"
    assert obligation_rule["owner"] == "deterministic_measurement"

    soft_rule = by_id["metric_opponent_diversity"]
    assert soft_rule["type"] == "soft"


def test_arena_day_collisions_has_one_source_of_truth():
    """issue #314: arena-day collisions must be sourced only from the
    freshly recomputed ``plan.arena_day_collisions`` -- an inherited
    ``fairness_gate`` snapshot from a superseded candidate must not be able
    to reintroduce a stale collision count/status alongside (or instead
    of) the dedicated rule."""
    plan = _plan(
        arena_day_collisions=[],
        fairness_gate={
            "policy_gate": {
                "metrics": [
                    {
                        "key": "arena_day_collisions",
                        "label": "Arenakollisjoner",
                        "provenance": "hard_invariant",
                        "status": "fail",
                        "value": 2,
                        "threshold": 0,
                        "detail": "2 kollisjoner (foreldet snapshot)",
                    },
                ]
            },
        },
    )
    rules = build_rules_model(plan)
    by_id = {rule["id"]: rule for rule in rules}

    # The generic metric path must not have produced a competing rule.
    assert "metric_arena_day_collisions" not in by_id

    dedicated_rule = by_id["arena_day_collisions"]
    assert dedicated_rule["type"] == "hard"
    assert dedicated_rule["ok"] is True
    assert "Ingen kollisjoner" in dedicated_rule["status"]


def test_arena_day_collisions_rule_reflects_fresh_plan_state():
    plan = _plan(
        arena_day_collisions=[
            {"date": "2025-10-05", "arena": "Jarahallen", "host_club": "Jar"},
        ],
    )
    rules = build_rules_model(plan)
    by_id = {rule["id"]: rule for rule in rules}

    dedicated_rule = by_id["arena_day_collisions"]
    assert dedicated_rule["ok"] is False
    assert "1 kollisjon" in dedicated_rule["status"]
    assert "Jar" in dedicated_rule["status"]


def test_build_rules_model_surfaces_unresolved_obligations():
    plan = _plan(
        unresolved_hosting_obligations=[{"club": "Ringerike", "age_group": "U12"}],
        unresolved_external_conflicts=[{"tournament_id": "t1", "host_club": "Drammen"}],
        unresolved_participation_shortfalls=[
            {"label": "Kongsberg U10-A", "actual": 3, "target": 5}
        ],
        shared_host_decisions=[
            {
                "registration": "Kongsberg/Tønsberg",
                "age_group": "U12",
                "chosen_club": "Kongsberg",
                "rationale": "Kongsberg har ledig kalender.",
                "decided_by": "llm",
            }
        ],
    )
    rules = build_rules_model(plan)
    by_id = {rule["id"]: rule for rule in rules}

    assert "Ringerike" in by_id["hosting_obligation_coverage"]["status"]
    assert "Drammen" in by_id["external_calendar_conflicts"]["status"]
    assert "Kongsberg U10-A" in by_id["participation_shortfalls"]["status"]

    shared_rule = by_id["shared_host_Kongsberg/Tønsberg_U12"]
    assert shared_rule["type"] == "decision"
    assert shared_rule["owner"] == "llm_controller"
    assert shared_rule["configured_value"] == "Kongsberg"


def test_age_group_mismatch_is_a_hard_violation():
    mismatched_team = Team(club="Jar", label="Jar JU10", age_group="JU10")
    tournament = Tournament(
        id="t1", date=date(2026, 9, 5), arena="Jarhallen", age_group="U10", teams=[mismatched_team]
    )
    plan = _plan(tournaments=[tournament])
    rules = build_rules_model(plan)
    by_id = {rule["id"]: rule for rule in rules}

    rule = by_id["age_group_exact_match"]
    assert rule["type"] == "hard"
    assert rule["ok"] is False
    assert "Jar JU10" in rule["status"]


def test_participation_target_exceeded_is_hard_and_shortfall_is_obligation():
    team = Team(club="Jar", label="Jar A", age_group="U10", target_tournament_count=1)
    tournaments = [
        Tournament(id="t1", date=date(2026, 9, 5), arena="A", age_group="U10", teams=[team]),
        Tournament(id="t2", date=date(2026, 9, 12), arena="A", age_group="U10", teams=[team]),
    ]
    plan = _plan(tournaments=tournaments)
    rules = build_rules_model(plan)
    by_id = {rule["id"]: rule for rule in rules}

    exceeded_rule = by_id["participation_target_exceeded"]
    assert exceeded_rule["type"] == "hard"
    assert exceeded_rule["ok"] is False
    assert "Jar A" in exceeded_rule["status"]

    # Under-target stays a non-blocking obligation, driven by a separate
    # plan field -- not derived from the same over-target check.
    shortfall_rule = by_id["participation_shortfalls"]
    assert shortfall_rule["type"] == "required_obligation"


def test_participation_target_exceeded_checks_age_group_half_target():
    """With no explicit per-team override, a team scheduled
    above its age group's before/after-Christmas target for that half is
    still a hard violation."""
    team = Team(club="Jar", label="Jar A", age_group="U11")
    tournaments = [
        Tournament(id="t1", date=date(2026, 1, 10), arena="A", age_group="U11", teams=[team]),
        Tournament(id="t2", date=date(2026, 2, 10), arena="A", age_group="U11", teams=[team]),
    ]
    plan = _plan(
        tournaments=tournaments,
        start_date=date(2025, 9, 1),
        end_date=date(2026, 6, 30),
        participation_targets_by_age_group={"U11": {"before_christmas": 3, "after_christmas": 1}},
    )
    rules = build_rules_model(plan)
    by_id = {rule["id"]: rule for rule in rules}

    exceeded_rule = by_id["participation_target_exceeded"]
    assert exceeded_rule["ok"] is False
    assert "Jar A" in exceeded_rule["status"]
    assert "after_christmas" in exceeded_rule["status"]

    targets_rule = by_id["participation_targets_by_age_group"]
    assert targets_rule["type"] == "decision"
    assert targets_rule["ok"] is True
    assert "U11" in targets_rule["configured_value"]


def test_participation_targets_by_age_group_rule_absent_when_unconfigured():
    plan = _plan(tournaments=[])
    rules = build_rules_model(plan)
    ids = {rule["id"] for rule in rules}
    assert "participation_targets_by_age_group" not in ids


def test_manual_adjustment_violations_are_hard():
    tournament = Tournament(
        id="t1",
        date=date(2026, 12, 25),
        arena="A",
        age_group="U10",
        host_club="Excluded FK",
        teams=[Team(club="Jar", label="Jar A", age_group="U10")],
    )
    plan = _plan(
        tournaments=[tournament],
        manual_adjustments={
            "banned_dates": ["2026-12-25"],
            "excluded_host_clubs": ["Excluded FK"],
            "locked_dates": ["2026-01-01"],
            "pinned_tournament_ids": ["missing-id"],
        },
    )
    rules = build_rules_model(plan)
    by_id = {rule["id"]: rule for rule in rules}

    assert by_id["banned_dates_not_used"]["ok"] is False
    assert by_id["excluded_host_clubs_not_used"]["ok"] is False
    assert by_id["locked_dates_preserved"]["ok"] is False
    assert by_id["pinned_tournaments_preserved"]["ok"] is False
    for rule_id in (
        "banned_dates_not_used",
        "excluded_host_clubs_not_used",
        "locked_dates_preserved",
        "pinned_tournaments_preserved",
    ):
        assert by_id[rule_id]["type"] == "hard"


def test_calendar_trust_rule_reflects_manual_booking_reason():
    trusted = Tournament(
        id="t1", date=date(2026, 9, 5), arena="A", age_group="U10", host_club="Jar",
        teams=[Team(club="Jar", label="Jar A", age_group="U10")],
    )
    untrusted = Tournament(
        id="t2", date=date(2026, 9, 12), arena="B", age_group="U10", host_club="Tønsberg",
        teams=[Team(club="Tønsberg", label="Tønsberg A", age_group="U10")],
        manual_booking_reason="Kalender utilgjengelig for Tønsberg — istid må bookes/verifiseres manuelt.",
    )
    plan = _plan(tournaments=[trusted, untrusted])
    rules = build_rules_model(plan)
    by_id = {rule["id"]: rule for rule in rules}

    rule = by_id["calendar_trust_by_host"]
    assert rule["type"] == "hard"
    assert rule["ok"] is False
    assert "Tønsberg" in rule["status"]
    assert "Jar" not in rule["status"]


def test_group_rules_by_type_and_summary_counts():
    plan = _plan(
        unresolved_hosting_obligations=[{"club": "Ringerike", "age_group": "U12"}],
        shared_host_decisions=[
            {
                "registration": "Kongsberg/Tønsberg",
                "age_group": "U12",
                "chosen_club": "Kongsberg",
                "decided_by": "llm",
            }
        ],
    )
    rules = build_rules_model(plan)
    groups = group_rules_by_type(rules)
    assert all(rule["type"] == "hard" for rule in groups["hard"])
    assert all(rule["type"] == "required_obligation" for rule in groups["required_obligation"])
    assert all(rule["type"] == "decision" for rule in groups["decision"])
    assert len(groups["decision"]) == 1

    counts = rules_summary_counts(rules)
    assert counts["hard_total"] == counts["hard_ok"]
    assert counts["obligations_unresolved"] == 1


def test_render_rules_table_html_empty_when_no_rules():
    assert render_rules_table_html([]) == ""


def test_render_rules_table_html_renders_columns_and_type_badges():
    rules = build_rules_model(
        _plan(unresolved_hosting_obligations=[{"club": "Ringerike", "age_group": "U12"}])
    )
    html = render_rules_table_html(rules)
    assert '<table class="report-table rules-table">' in html
    assert "<th>Regel</th>" in html
    assert "<th>Type</th>" in html
    assert "<th>Status</th>" in html
    assert "rules-type-badge--required_obligation" in html
    assert "Ringerike" in html
