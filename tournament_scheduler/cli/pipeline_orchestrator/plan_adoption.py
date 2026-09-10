"""Deterministic/LLM plan-adoption decision and the mid-planning critic loop."""

from __future__ import annotations

import argparse
from typing import Any

from ._shared import _console
from .judgment import _format_plan_attempt_quality, _plan_attempt_quality, _plan_attempt_quality_adopts
from .stage3_optimize_core import _build_mid_planning_critic_hints
from .stage3_run import _run_stage3
from .verification import _mid_planning_decision_problem

def _decide_plan_adoption(
    best_plan: "dict[str, Any]",
    rerun_plan: "dict[str, Any]",
    problem: "dict[str, Any] | None",
    *,
    run_id: str,
    iteration: int,
    work_dir: str,
    log_fn: "Any",
    label: str = "mid_planning_critic",
) -> "tuple[str, str]":
    """Decide whether *rerun_plan* should replace *best_plan* as the best
    pre-export attempt (issue #260 Phase 4).

    Shared by the mid-planning critic loop (``_run_mid_planning_critic_loop``)
    and the multi-seed Stage 3 best-attempt loop in ``_cmd_run`` — both are
    the same "does this new attempt replace the current best?" decision,
    just at different points before export. *label* only distinguishes the
    two in logs/decision-context refs (e.g. ``"mid_planning_critic"`` vs.
    ``"stage3_multi_seed"``); it does not change the decision logic.

    When a headless judge is configured (``RVV_JUDGE_BACKEND``), builds the
    same kind of ``DecisionContext`` the Stage 3 v2 optimizer promote/reject
    decision uses (:func:`stage3_decision.build_stage3_decision_context`)
    from an old-vs-new A/B report of best-vs-rerun, asks the judge to choose
    ``apply_candidate``/``keep_baseline``, validates the reply
    deterministically, and records it to the run manifest's
    ``decision_log``.

    Returns ``(outcome, reason)``:
    - ``"no_judge"`` — no headless judge configured at all. The caller
      falls back to its own explicitly-named legacy path
      (:func:`_plan_attempt_quality_adopts`).
    - ``"adopt"`` — the judge chose ``apply_candidate`` and it was accepted
      (which itself refuses a rerun that fails the verifier).
    - ``"hold"`` — a judge is configured but the A/B report couldn't be
      built, the judge call failed, or its action was rejected/declined.
      Safe-by-default (issue #260 Phase 4 — this previously fell back to
      :func:`_plan_attempt_quality_adopts` in every one of these cases,
      "the remaining unsafe decision fallback" the issue names): a
      configured-but-failing/declining judge no longer silently reverts to
      the deterministic quality rank — it holds, i.e. does not adopt.
    """
    from ...application.decisions import decide, record_llm_decision
    from ...llm_judge import (
        build_action_decision_prompt,
        get_judge_if_headless,
        parse_action_verdict,
    )
    from ...planning_contract import extract_candidate
    from ...stage3_ab import build_ab_report
    from ...stage3_decision import build_stage3_decision_context

    try:
        judge = get_judge_if_headless()
    except ValueError:
        return "no_judge", "no_judge_configured"
    if judge is None:
        return "no_judge", "no_judge_configured"

    try:
        report = build_ab_report(extract_candidate(best_plan), extract_candidate(rerun_plan), problem)
    except (ValueError, KeyError) as exc:
        log_fn(f"{label} {iteration}: could not build A/B report for judge — holding: {exc}")
        return "hold", "ab_report_failed"

    context = build_stage3_decision_context(
        report,
        run_id=run_id,
        baseline_ref=f"{label}:best_attempt",
        candidate_ref=f"{label}:iteration_{iteration}",
        objective=(
            f"Decide whether this {label} rerun should replace "
            "the current best pre-export plan, or the current best should "
            "be kept."
        ),
    )
    try:
        raw_verdict = judge.judge(build_action_decision_prompt(context))
    except RuntimeError as exc:
        log_fn(f"{label} {iteration}: judge call failed — holding: {exc}")
        return "hold", "judge_call_failed"

    action = parse_action_verdict(context, raw_verdict)
    if action.action_id == "apply_candidate" and not action.arguments.get("candidate_ref"):
        from dataclasses import replace as _dc_replace

        action = _dc_replace(
            action,
            arguments={**action.arguments, "candidate_ref": context.candidate_ref or action.target},
        )

    result = decide(context, action)
    try:
        record_llm_decision(work_dir, context, action, result)
    except Exception as exc:
        log_fn(f"{label} {iteration}: record_llm_decision failed: {exc}")

    if not result.accepted:
        if result.rejection_reason == "hard_violation_blocks_action":
            # The rerun itself fails the verifier — a known-invalid candidate.
            # Not adopting is always safe: the current best is, by
            # definition, not this newly-invalid rerun.
            log_fn(
                f"{label} {iteration}: judge chose apply_candidate but the rerun "
                "fails the verifier — not adopting (a hard violation cannot be bypassed)"
            )
            return "hold", "hard_violation_blocks_action"
        log_fn(
            f"{label} {iteration}: judge action {action.action_id!r} rejected "
            f"({result.rejection_reason}) — holding"
        )
        return "hold", result.rejection_reason or "rejected"

    if action.action_id != "apply_candidate":
        log_fn(f"{label} {iteration}: judge decided {action.action_id} — holding")
        return "hold", action.action_id

    log_fn(
        f"{label} {iteration}: judge decided apply_candidate "
        f"({(action.rationale or '')[:200]})"
    )
    return "adopt", "apply_candidate"


def _run_mid_planning_critic_loop(
    args: "argparse.Namespace",
    cfg: "dict[str, Any]",
    scraping: "dict[str, Any]",
    state: "Any",
    start: "Any",
    end: "Any",
    strict: bool,
    resume_from: int,
    log_fn: "Any",
    plan: "dict[str, Any]",
) -> "tuple[dict[str, Any], bool, bool]":
    """Optionally run a Stage 3 checkpoint critic loop before Stage 4 export.

    The loop is deliberately separate from post-Stage-4 refinement: it only
    inspects the Stage 3 checkpoint, converts critic/fairness findings into
    planner penalty hints, and reruns Stage 3 before export artifacts exist.
    """
    max_iterations = max(0, int(getattr(args, "mid_planning_critic_iterations", 0) or 0))
    if max_iterations <= 0 or resume_from > 3:
        return plan, False, False

    current_plan = plan
    best_plan = plan
    run_failed = False
    base_iterations = max(1, int(getattr(args, "iterations", 1) or 1))
    problem = _mid_planning_decision_problem(cfg, scraping, start, end)
    try:
        from ...pipeline.run_manifest import RunManifest

        run_id = str(RunManifest(state.work_dir).read().get("run_id") or "")
    except Exception:
        run_id = ""

    for iteration in range(1, max_iterations + 1):
        hints = _build_mid_planning_critic_hints(current_plan, iteration, log_fn)
        issues = hints.get("issues", []) or []
        penalty_hints = hints.get("penalty_hints", {}) or {}
        if not issues and not penalty_hints:
            log_fn(f"Mid-planning critic {iteration}: no issues or penalty hints — stopping")
            break

        _console.print(
            f"  [cyan]↻[/cyan] Midtplanleggingskritiker {iteration}/{max_iterations}: "
            f"{len(issues)} funn, {len(penalty_hints)} hint(s)"
        )
        log_fn(
            f"Mid-planning critic {iteration}: tone={hints.get('tone')}, "
            f"issues={len(issues)}, penalty_hints={penalty_hints}"
        )

        rerun_iterations = base_iterations + iteration
        rerun_plan, abort, stage_failed = _run_stage3(
            args,
            cfg,
            scraping,
            state,
            start,
            end,
            strict,
            3,
            log_fn,
            rerun_iterations,
            penalty_hints,
            hints,
        )
        if abort:
            return current_plan, True, run_failed
        if stage_failed:
            run_failed = True
        if not rerun_plan:
            log_fn(f"Mid-planning critic {iteration}: Stage 3 rerun returned no plan — stopping")
            break

        current_plan = rerun_plan
        adoption_outcome, adoption_reason = _decide_plan_adoption(
            best_plan,
            rerun_plan,
            problem,
            run_id=run_id,
            iteration=iteration,
            work_dir=str(state.work_dir),
            log_fn=log_fn,
        )
        if adoption_outcome == "no_judge":
            # Explicitly-named legacy compatibility path (issue #260 Phase 4):
            # no judge configured at all, so fall back to the deterministic
            # quality rank — never used for a configured judge that failed
            # or declined (that is "hold", handled by adopts=False below).
            adopts = _plan_attempt_quality_adopts(best_plan, rerun_plan)
        else:
            adopts = adoption_outcome == "adopt"
        if adopts:
            best_plan = rerun_plan
            log_fn(
                f"Mid-planning critic {iteration}: adopted rerun as new best "
                f"{_format_plan_attempt_quality(_plan_attempt_quality(rerun_plan))}"
            )

    if best_plan is not current_plan and best_plan is not None:
        try:
            from ...pipeline.state import StageName, StageStatus

            state.write_stage(StageName.PLANNING, best_plan, status=StageStatus.DONE)
            log_fn("Mid-planning critic: checkpoint reset to best pre-export plan")
        except Exception as exc:
            log_fn(f"Mid-planning critic: could not persist best plan: {exc}")

    return best_plan, False, run_failed
