"""Stage 3 v2 optimizer as an LLM-directed decision (issue #260 Phase 4).

Turns a :func:`stage3_ab.build_ab_report` old-vs-new comparison into a
:class:`~tournament_scheduler.application.decisions.DecisionContext` so an
LLM/agent controller — not a Python quality-ranking heuristic — decides
whether to adopt the Stage 3 v2 candidate, request another optimization
pass, ask the operator, or keep the ``SeasonPlanner`` baseline. Deterministic
validation (:func:`application.decisions.validate_decision_action`) remains
the authority that stops an LLM response from applying a candidate that
fails the verifier, matching ADR 0002's "deterministic code owns hard
rules" boundary.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .application.decisions import DecisionContext

# The optimizer/A/B capability only ever needs this subset of the global
# DECISION_ACTION_IDS vocabulary. Keep it narrow here too, rather than
# letting every context offer every action.
STAGE3_DECISION_ACTIONS: "tuple[str, ...]" = (
    "apply_candidate",
    "keep_baseline",
    "optimize_plan",
    "request_operator",
)


def build_stage3_decision_context(
    report: Dict[str, Any],
    *,
    run_id: str,
    baseline_ref: Optional[str] = None,
    candidate_ref: Optional[str] = None,
    objective: str = "",
) -> DecisionContext:
    """Build the :class:`DecisionContext` for an old-vs-new Stage 3 A/B *report*.

    *report* is whatever :func:`tournament_scheduler.stage3_ab.build_ab_report`
    returned. ``apply_candidate`` is deterministically blocked whenever the
    new candidate itself fails the verifier
    (``report["new"]["verification"]["ok"]`` is ``False``) — a still-invalid
    candidate cannot be adopted by prose, regardless of whether that
    violation is a regression versus the baseline or was already present.
    """
    new_verification = (report.get("new") or {}).get("verification") or {}
    old_verification = (report.get("old") or {}).get("verification") or {}

    hard_violations: List[str] = []
    if not new_verification.get("ok", True):
        for violation in new_verification.get("violations", []) or []:
            hard_violations.append(f"{violation.get('code')}: {violation.get('message')}")

    warnings: List[str] = [
        f"{age_group}: {', '.join(regressions)}"
        for age_group, regressions in sorted((report.get("per_age_group_regressions") or {}).items())
    ]
    if report.get("hard_constraint_regressed") and not hard_violations:
        # Defensive: hard_constraint_regressed implies new_verification failed,
        # so hard_violations above should already be non-empty. Surface it as
        # a warning too in case the report shape ever drifts.
        warnings.append("new candidate introduces hard violations baseline did not have")

    scorecard = {
        "overall_metrics": (report.get("overall_comparison") or {}).get("metrics", []),
        "dominates_baseline": bool(report.get("dominates_baseline", False)),
        "production_ready": bool(report.get("production_ready", False)),
    }

    return DecisionContext(
        run_id=run_id,
        capability="stage3_optimize",
        stage="stage3",
        objective=objective
        or (
            "Decide whether to adopt the Stage 3 v2 optimizer candidate over "
            "the SeasonPlanner baseline for this season, request another "
            "optimization pass with different weights/settings, ask the "
            "operator, or keep the baseline."
        ),
        facts={
            "old_verification_ok": old_verification.get("ok"),
            "new_verification_ok": new_verification.get("ok"),
            "hard_constraint_regressed": bool(report.get("hard_constraint_regressed", False)),
        },
        hard_violations=tuple(hard_violations),
        warnings=tuple(warnings),
        scorecard=scorecard,
        baseline_ref=baseline_ref,
        candidate_ref=candidate_ref,
        available_actions=STAGE3_DECISION_ACTIONS,
    )


def apply_stage3_candidate(work_dir: str, candidate: Dict[str, Any]) -> None:
    """Replace the Stage 3 checkpoint's plan with *candidate*.

    Called after an ``apply_candidate`` decision has been deterministically
    accepted by :func:`application.decisions.decide`. Preserves every other
    key already in the checkpoint (e.g. ``warnings``, ``rules_report``) and
    only swaps the ``plan`` payload, mirroring how the existing mid-planning
    critic loop persists a better candidate
    (``cli.pipeline_orchestrator._run_mid_planning_critic_loop``).
    """
    from .pipeline.state import PipelineState, StageName, StageStatus

    state = PipelineState(work_dir)
    checkpoint = dict(state.read_stage(StageName.PLANNING) or {})
    checkpoint["plan"] = candidate
    state.write_stage(StageName.PLANNING, checkpoint, status=StageStatus.DONE)
