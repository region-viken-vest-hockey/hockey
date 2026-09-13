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


class TestReconcileParticipationShortfallProvenance:
    """issue #321: the final verifier's `manual_participation_placements`
    only carries club/label/age_group/half/actual/target -- it does not
    know *why* a team fell short. `SeasonPlanner` already worked that out
    (category + plain-language reason + #318 same-date-capacity evidence)
    before `_reconcile_verified_manual_state` overwrote it with a generic
    "does not match target" reason. The reconciled list must recover that
    provenance by matching against the plan's own prior finding instead of
    discarding it.
    """

    def _problem(self) -> dict:
        return {
            "teams": [{"club": "Kongsberg", "age_group": "U12", "label": "Kongsberg U12A"}],
            "start_date": "2026-09-01",
            "end_date": "2027-04-30",
            "participation_targets_by_age_group": {
                "U12": {"before_christmas": 7, "after_christmas": 7}
            },
        }

    def _plan(self) -> dict:
        return {
            "plan": {
                "tournaments": [_tournament("Kongsberg", "U12")],
                "unresolved_hosting_obligations": [],
                "unresolved_external_conflicts": [],
                "unresolved_participation_shortfalls": [
                    {
                        "club": "Kongsberg",
                        "label": "Kongsberg U12A",
                        "age_group": "U12",
                        "period": "before_christmas",
                        "actual": "6",
                        "target": "7",
                        "reason": (
                            "Kongsberg U12A (Kongsberg, U12) deltar 6 ganger i "
                            "before_christmas, forventet 7 -- flere parallelle "
                            "turneringer for U12 delte samme dato(er) enn det "
                            "fantes unike lag til."
                        ),
                        "category": "participation_under_target_same_date_capacity",
                    }
                ],
                "same_date_capacity_evidence": [
                    {
                        "category": "same_date_uniqueness_limit",
                        "date": "2026-12-13",
                        "age_group": "U12",
                        "period": "before_christmas",
                        "requested_slots": 6,
                        "feasible_slots": 5,
                        "distinct_team_count": 17,
                        "relocated_slots": 0,
                        "unrelocated_slots": 1,
                        "alternatives_considered": [
                            {"date": "2026-12-20", "rejected_reason": "same_date_uniqueness_limit"}
                        ],
                    }
                ],
            }
        }

    def test_category_reason_and_half_survive_reconciliation(self):
        plan = self._plan()

        _reconcile_verified_manual_state(plan, self._problem(), log_fn=lambda *_: None)

        shortfalls = plan["plan"]["unresolved_participation_shortfalls"]
        [entry] = [s for s in shortfalls if s.get("half") == "before_christmas"]
        assert entry["category"] == "participation_under_target_same_date_capacity"
        assert entry["half"] == "before_christmas"
        assert "samme dato" in entry["reason"]
        assert entry["reason"] != "actual participation count does not match target"

    def test_same_date_capacity_evidence_is_attached(self):
        plan = self._plan()

        _reconcile_verified_manual_state(plan, self._problem(), log_fn=lambda *_: None)

        entry = plan["plan"]["unresolved_participation_shortfalls"][0]
        assert entry["same_date_capacity_evidence"][0]["category"] == "same_date_uniqueness_limit"
        assert entry["same_date_capacity_evidence"][0]["unrelocated_slots"] == 1

    def test_generic_reason_only_used_when_no_prior_finding_matches(self):
        """A finding the verifier reports with no matching prior entry (e.g.
        the plan's own list was empty) still gets a plausible fallback
        category/reason instead of crashing or leaving the field missing."""
        plan = self._plan()
        plan["plan"]["unresolved_participation_shortfalls"] = []
        plan["plan"]["same_date_capacity_evidence"] = []

        _reconcile_verified_manual_state(plan, self._problem(), log_fn=lambda *_: None)

        entry = plan["plan"]["unresolved_participation_shortfalls"][0]
        assert entry["category"] == "participation_under_target"
        assert entry["reason"] == "actual participation count does not match target"
        assert "same_date_capacity_evidence" not in entry
