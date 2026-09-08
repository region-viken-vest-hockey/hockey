from tournament_scheduler.html.renderers.rules_table import render_rules_table_html
from tournament_scheduler.models import SeasonPlan
from tournament_scheduler.rules_model import build_rules_model


def _plan(**overrides) -> SeasonPlan:
    plan = SeasonPlan()
    for key, value in overrides.items():
        setattr(plan, key, value)
    return plan


def test_build_rules_model_is_empty_for_bare_plan():
    rules = build_rules_model(_plan())
    ids = {rule["id"] for rule in rules}
    # The three always-present required-obligation rules report "Oppfylt"
    # even with no data, plus no metric/shared-host rules on an empty plan.
    assert ids == {
        "hosting_obligation_coverage",
        "external_calendar_conflicts",
        "participation_shortfalls",
    }
    for rule in rules:
        assert rule["status"] == "Oppfylt"
        assert rule["type"] == "required_obligation"


def test_build_rules_model_reads_canonical_fairness_gate_split():
    plan = _plan(
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
                        "detail": "2 kollisjoner",
                    },
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

    hard_rule = by_id["metric_arena_day_collisions"]
    assert hard_rule["type"] == "hard"
    assert hard_rule["owner"] == "deterministic_verifier"
    assert "fail" in hard_rule["status"]

    obligation_rule = by_id["metric_hosting_deviation"]
    assert obligation_rule["type"] == "required_obligation"
    assert obligation_rule["owner"] == "deterministic_measurement"

    soft_rule = by_id["metric_opponent_diversity"]
    assert soft_rule["type"] == "soft"


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
    assert shared_rule["type"] == "soft"
    assert shared_rule["owner"] == "llm_controller"
    assert shared_rule["configured_value"] == "Kongsberg"


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
