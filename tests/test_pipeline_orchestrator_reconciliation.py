"""Verifier -> export reconciliation (issue #274 P0).

Reproduces the reported production bug: Stage 3 verification detects
unresolved Kongsberg U9/U10 hosting obligations, but the stale
`plan.unresolved_hosting_obligations` computed mid-pipeline is empty, so
`manual_schedule.html` never showed them. `_reconcile_verified_manual_state`
must overwrite the plan's manual/unresolved lists with the canonical
final `verify_candidate` recomputation right before export.
"""

from tournament_scheduler.cli.pipeline_orchestrator.verification import _reconcile_verified_manual_state


def _tournament(host_club: str, age_group: str) -> dict:
    return {
        "id": f"{host_club}-{age_group}",
        "date": "2025-10-05",
        "age_group": age_group,
        "host_club": host_club,
        "teams": [{"club": host_club, "label": f"{host_club} {age_group}A", "age_group": age_group}],
    }


def _plan_checkpoint(tournaments: list[dict], stale_unresolved: list[dict]) -> dict:
    return {
        "plan": {
            "tournaments": tournaments,
            "unresolved_hosting_obligations": stale_unresolved,
            "unresolved_external_conflicts": [],
            "unresolved_participation_shortfalls": [],
        }
    }


class TestReconcileVerifiedManualState:
    def test_surfaces_unresolved_hosting_the_stale_plan_omitted(self):
        # Kongsberg has registered U9/U10 teams but the plan only hosts U11 --
        # exactly the reported "Kongsberg has registered U9 and U10 teams but
        # no Kongsberg-hosted U9/U10 tournament" bug.
        problem = {
            "teams": [
                {"club": "Kongsberg", "age_group": "U9", "label": "Kongsberg U9A"},
                {"club": "Kongsberg", "age_group": "U10", "label": "Kongsberg U10A"},
                {"club": "Kongsberg", "age_group": "U11", "label": "Kongsberg U11A"},
            ]
        }
        plan = _plan_checkpoint([_tournament("Kongsberg", "U11")], stale_unresolved=[])

        _reconcile_verified_manual_state(plan, problem, log_fn=lambda *_: None)

        unresolved = {
            (item["club"], item["age_group"]) for item in plan["plan"]["unresolved_hosting_obligations"]
        }
        assert ("Kongsberg", "U9") in unresolved
        assert ("Kongsberg", "U10") in unresolved
        assert ("Kongsberg", "U11") not in unresolved
        for item in plan["plan"]["unresolved_hosting_obligations"]:
            assert item["reason"]

    def test_clears_a_stale_entry_that_is_now_actually_resolved(self):
        problem = {"teams": [{"club": "Kongsberg", "age_group": "U9", "label": "Kongsberg U9A"}]}
        plan = _plan_checkpoint(
            [_tournament("Kongsberg", "U9")],
            stale_unresolved=[{"club": "Kongsberg", "age_group": "U9", "reason": "stale"}],
        )

        _reconcile_verified_manual_state(plan, problem, log_fn=lambda *_: None)

        assert plan["plan"]["unresolved_hosting_obligations"] == []

    def test_best_effort_leaves_plan_untouched_on_failure(self):
        plan = {"plan": "not-a-dict-shaped-plan"}
        # Should not raise, and should not mutate a plan it can't extract a
        # candidate from.
        _reconcile_verified_manual_state(plan, None, log_fn=lambda *_: None)
        assert plan == {"plan": "not-a-dict-shaped-plan"}

    def test_none_plan_is_a_noop(self):
        _reconcile_verified_manual_state(None, None, log_fn=lambda *_: None)
