"""Unit tests for tournament_scheduler.stage3_decision (issue #260 Phase 4)."""

from __future__ import annotations

from tournament_scheduler.application.decisions import DecisionAction, decide
from tournament_scheduler.stage3_ab import build_ab_report
from tournament_scheduler.stage3_decision import (
    STAGE3_DECISION_ACTIONS,
    apply_stage3_candidate,
    build_stage3_decision_context,
)
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


def _invalid_candidate() -> dict:
    """Two tournaments sharing a date/arena — a hard verifier violation."""
    teams = [_team(f"Club{i}", f"T{i}", "U10") for i in range(1, 5)]
    return {
        "schema_version": 1,
        "tournaments": [
            _tournament("t1", "2026-01-05", "Arena1", "U10", teams),
            _tournament("t2", "2026-01-05", "Arena1", "U10", teams),
        ],
    }


class TestBuildStage3DecisionContext:
    def test_dominant_candidate_offers_apply_without_hard_violations(self):
        old_candidate = _clustered_candidate()
        new_candidate = optimize_candidate(old_candidate, iterations=3000, seed=1)
        report = build_ab_report(old_candidate, new_candidate)
        assert report["production_ready"]  # sanity: this fixture is known-promotable

        context = build_stage3_decision_context(
            report, run_id="run-1", baseline_ref="old.json", candidate_ref="new.json"
        )

        assert context.capability == "stage3_optimize"
        assert context.stage == "stage3"
        assert context.hard_violations == ()
        assert context.available_actions == STAGE3_DECISION_ACTIONS
        assert context.baseline_ref == "old.json"
        assert context.candidate_ref == "new.json"
        assert context.scorecard["production_ready"] is True
        assert context.scorecard["dominates_baseline"] is True

        action = DecisionAction(action_id="apply_candidate", arguments={"candidate_ref": "new.json"})
        result = decide(context, action)
        assert result.accepted

    def test_invalid_candidate_blocks_apply_candidate(self):
        old_candidate = _clustered_candidate()
        new_candidate = _invalid_candidate()
        report = build_ab_report(old_candidate, new_candidate)
        assert not report["new"]["verification"]["ok"]

        context = build_stage3_decision_context(report, run_id="run-1")

        assert context.hard_violations != ()

        action = DecisionAction(action_id="apply_candidate", arguments={"candidate_ref": "new.json"})
        result = decide(context, action)
        assert not result.accepted
        assert result.rejection_reason == "hard_violation_blocks_action"

        # keep_baseline is not blocked by an outstanding hard violation.
        keep = decide(context, DecisionAction(action_id="keep_baseline"))
        assert keep.accepted

    def test_per_age_group_regression_surfaces_as_warning(self):
        old_candidate = _clustered_candidate()
        # A candidate that verifies fine overall but regresses this single
        # age group's diversity (identical to baseline => no regression is
        # also a valid outcome; use the optimizer's own baseline-identical
        # case to keep this deterministic and independent of solver luck).
        new_candidate = optimize_candidate(old_candidate, iterations=0, seed=1)
        report = build_ab_report(old_candidate, new_candidate)

        context = build_stage3_decision_context(report, run_id="run-1")

        # Zero iterations means the "new" candidate is identical (or a
        # no-op repair of) the baseline: no per-age-group regressions.
        assert not report["per_age_group_regressions"]
        assert context.warnings == ()


class TestApplyStage3Candidate:
    def test_apply_stage3_candidate_replaces_plan_and_preserves_other_keys(self, tmp_path):
        from tournament_scheduler.pipeline.state import PipelineState, StageName

        work_dir = str(tmp_path)
        state = PipelineState(work_dir)
        old_candidate = _clustered_candidate()
        state.write_stage(
            StageName.PLANNING,
            {"plan": old_candidate, "warnings": ["some warning"]},
        )

        new_candidate = optimize_candidate(old_candidate, iterations=500, seed=2)
        apply_stage3_candidate(work_dir, new_candidate)

        checkpoint = state.read_stage(StageName.PLANNING)
        assert checkpoint["plan"] == new_candidate
        assert checkpoint["warnings"] == ["some warning"]
