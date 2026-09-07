"""Deterministic planner reproducibility check.

Verifies the concrete guarantee issue #16 asks CI to enforce: the same
inputs and seeds reproduce the same selected-candidate metadata. This has
no external dependency (no network, no real workbook), so it belongs in the
required-fast CI tier.
"""

from __future__ import annotations

from datetime import date, datetime

from tests.test_stage3_planning import _make_config
from tournament_scheduler.pipeline.state import PipelineState
from tournament_scheduler.pipeline.stage3_planning import run

# Pinned "today" (issue #272 effective_start_date) so this fixed 2025 season
# window stays valid regardless of the real wall clock.
_TODAY = date(2025, 8, 25)


def _run_twice(tmp_path, *, iterations: int) -> tuple[dict, dict]:
    cfg = _make_config()
    start, end = datetime(2025, 9, 1), datetime(2025, 12, 15)

    state_a = PipelineState(tmp_path / "a")
    result_a = run(cfg, {}, state_a, start, end, iterations=iterations, today=_TODAY)

    state_b = PipelineState(tmp_path / "b")
    result_b = run(cfg, {}, state_b, start, end, iterations=iterations, today=_TODAY)

    return result_a, result_b


class TestPlannerReproducibility:
    def test_same_inputs_and_seeds_reproduce_selected_candidate_metadata(self, tmp_path):
        """iterations=3 uses fixed seeds [0, 1, 2] regardless of invocation,
        so two independent runs of the same config must select the exact
        same candidate with identical reproducibility metadata."""
        result_a, result_b = _run_twice(tmp_path, iterations=3)

        assert result_a["selected_candidate_attempt"] == result_b["selected_candidate_attempt"]

        candidates_a = result_a["candidates"]
        candidates_b = result_b["candidates"]
        assert len(candidates_a) == len(candidates_b) == 3

        for candidate_a, candidate_b in zip(candidates_a, candidates_b):
            assert candidate_a["seed"] == candidate_b["seed"]
            assert candidate_a["status"] == candidate_b["status"]
            assert candidate_a["score"] == candidate_b["score"]
            assert candidate_a["tournament_count"] == candidate_b["tournament_count"]
            assert candidate_a["rank"] == candidate_b["rank"]
            assert candidate_a["config_fingerprint"] == candidate_b["config_fingerprint"]
            assert candidate_a["source_fingerprint"] == candidate_b["source_fingerprint"]
            assert candidate_a["planner_version"] == candidate_b["planner_version"]

    def test_selected_plan_tournament_dates_are_identical(self, tmp_path):
        """Reproducibility must hold for the actual plan output, not just
        its metadata summary."""
        result_a, result_b = _run_twice(tmp_path, iterations=3)

        dates_a = sorted(t["date"] for t in result_a["plan"]["tournaments"])
        dates_b = sorted(t["date"] for t in result_b["plan"]["tournaments"])
        assert dates_a == dates_b

    def test_different_config_produces_different_fingerprint(self, tmp_path):
        """Sanity check on the other direction: fingerprints must actually
        vary with input, or the reproducibility assertions above would be
        vacuous."""
        cfg_a = _make_config()
        cfg_b = _make_config()
        cfg_b["target_tournament_count"] = 99

        start, end = datetime(2025, 9, 1), datetime(2025, 12, 15)
        state_a = PipelineState(tmp_path / "a")
        result_a = run(cfg_a, {}, state_a, start, end, iterations=1, today=_TODAY)
        state_b = PipelineState(tmp_path / "b")
        result_b = run(cfg_b, {}, state_b, start, end, iterations=1, today=_TODAY)

        assert result_a["candidates"][0]["config_fingerprint"] != result_b["candidates"][0]["config_fingerprint"]
