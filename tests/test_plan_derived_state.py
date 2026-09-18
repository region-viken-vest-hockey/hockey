"""Unit coverage for the single canonical plan-derived-state reconciliation."""

from __future__ import annotations

from tournament_scheduler.plan_derived_state import (
    publication_readiness_with_plan_placements,
    reconcile_plan_derived_state,
)


def _verify_source(**overrides):
    source = {
        "violations": [],
        "unresolved_hosting_obligations": [],
        "hosting_balance": [],
        "hosting_balance_imbalances": [],
        "manual_external_conflict_placements": [],
        "manual_participation_placements": [],
        "waived_violations": [],
    }
    source.update(overrides)
    return source


def test_reconcile_refreshes_every_verifier_derived_projection() -> None:
    plan = {
        "unresolved_hosting_obligations": [{"club": "Stale", "age_group": "U9"}],
        "hosting_balance_imbalances": [{"club": "Stale"}],
        "unresolved_external_conflicts": [{"tournament_id": "stale"}],
        "unresolved_participation_shortfalls": [{"club": "Stale"}],
        "same_age_hosting_repairs": [{"status": "unresolved", "club": "Stale", "age_group": "U9"}],
    }
    source = _verify_source(
        unresolved_hosting_obligations=[{"club": "Live", "age_group": "U9", "reason": "why"}],
        hosting_balance_imbalances=[{"club": "Live"}],
        manual_external_conflict_placements=[
            {"tournament_id": "t1", "host_club": "A", "age_group": "U9", "date": "2026-10-10"}
        ],
        manual_participation_placements=[
            {"club": "A", "label": "A 1", "age_group": "U9", "actual": "1", "target": "2", "half": "before_christmas"}
        ],
    )

    reconcile_plan_derived_state(plan, source)

    assert plan["unresolved_hosting_obligations"] == [
        {"club": "Live", "age_group": "U9", "reason": "why"}
    ]
    assert plan["hosting_balance_imbalances"] == [{"club": "Live"}]
    # The stale "still unresolved" repair row for a no-longer-unresolved
    # obligation is dropped.
    assert plan["same_age_hosting_repairs"] == []
    assert plan["unresolved_external_conflicts"] == [
        {
            "tournament_id": "t1",
            "host_club": "A",
            "age_group": "U9",
            "date": "2026-10-10",
            "reason": "external calendar conflict requires manual resolution",
        }
    ]
    assert plan["unresolved_participation_shortfalls"] == [
        {
            "club": "A",
            "label": "A 1",
            "age_group": "U9",
            "actual": "1",
            "target": "2",
            "category": "participation_under_target",
            "reason": "actual participation count does not match target",
            "half": "before_christmas",
        }
    ]


def test_partial_source_never_clears_untouched_plan_fields() -> None:
    plan = {
        "unresolved_hosting_obligations": [{"club": "Keep", "age_group": "U9"}],
        "unresolved_external_conflicts": [{"tournament_id": "keep"}],
        "unresolved_participation_shortfalls": [{"club": "Keep"}],
        "unresolved_tournament_placements": [{"age_group": "U9", "date": "2026-10-10"}],
    }

    # A verify result that only carries hosting facts must not wipe the rest.
    reconcile_plan_derived_state(plan, {"unresolved_hosting_obligations": []})

    assert plan["unresolved_hosting_obligations"] == []
    assert plan["unresolved_external_conflicts"] == [{"tournament_id": "keep"}]
    assert plan["unresolved_participation_shortfalls"] == [{"club": "Keep"}]
    # Planner-time obligations are not verifier projections and stay put.
    assert plan["unresolved_tournament_placements"] == [{"age_group": "U9", "date": "2026-10-10"}]


def test_participation_provenance_and_same_date_evidence_are_recovered() -> None:
    plan = {
        "unresolved_participation_shortfalls": [
            {
                "club": "A",
                "label": "A 1",
                "age_group": "U9",
                "half": "before_christmas",
                "category": "participation_under_target_same_date_capacity",
                "reason": "no free shared weekend",
            }
        ],
        "same_date_capacity_evidence": [
            {"age_group": "U9", "period": "before_christmas", "weekend": "2026-10-10"}
        ],
    }
    source = _verify_source(
        manual_participation_placements=[
            {"club": "A", "label": "A 1", "age_group": "U9", "actual": "1", "target": "2", "half": "before_christmas"}
        ]
    )

    reconcile_plan_derived_state(plan, source)

    entry = plan["unresolved_participation_shortfalls"][0]
    assert entry["category"] == "participation_under_target_same_date_capacity"
    assert entry["reason"] == "no free shared weekend"
    assert entry["same_date_capacity_evidence"] == [
        {"age_group": "U9", "period": "before_christmas", "weekend": "2026-10-10"}
    ]


def test_operator_waivers_are_projected_and_filtered_by_applied_id() -> None:
    plan = {"operator_waivers": [{"id": "old"}], "operator_waived_violations": [{"waiver_id": "old"}]}
    source = _verify_source(waived_violations=[{"waiver_id": "w1"}, {"waiver_id": "w2"}])
    problem = {
        "operator_waivers": [
            {"id": "w1", "rule": "participation_hard_max", "reason": "operator decision"},
            {"id": "w2", "rule": "participation_hard_max", "reason": "another"},
            {"id": "w3", "rule": "participation_hard_max", "reason": "not applied"},
        ]
    }

    reconcile_plan_derived_state(plan, source, problem=problem)

    assert [item["waiver_id"] for item in plan["operator_waived_violations"]] == ["w1", "w2"]
    assert [row["id"] for row in plan["operator_waivers"]] == ["w1", "w2"]


def test_operator_waivers_are_left_untouched_without_a_problem() -> None:
    plan = {"operator_waivers": [{"id": "keep"}]}
    source = _verify_source(waived_violations=[{"waiver_id": "w1"}])

    reconcile_plan_derived_state(plan, source)

    assert plan["operator_waivers"] == [{"id": "keep"}]
    assert [item["waiver_id"] for item in plan["operator_waived_violations"]] == ["w1"]


def test_readiness_override_wins_and_plan_placements_are_folded_once() -> None:
    plan = {"unresolved_tournament_placements": [{"age_group": "U9", "date": "2026-10-10"}]}
    source = _verify_source(violations=[])

    derived = publication_readiness_with_plan_placements(source, plan)
    assert derived["status"] == "REVIEW_REQUIRED"
    assert any(reason["code"] == "tournament_placement_shortfall" for reason in derived["reasons"])

    bound = {"status": "PUBLISHABLE", "publishable": True, "reasons": []}
    reconcile_plan_derived_state(plan, source, readiness=bound)
    assert plan["publication_readiness"] == bound
