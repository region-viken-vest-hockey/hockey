"""Per-iteration refinement decision logic (apply candidate? continue?)."""

from __future__ import annotations

from typing import Any

from .judgment import _extract_plan_obj, _fairness_gate, _score_attr

def _refinement_decision_problem(state: "Any", plan_obj: "Any") -> "dict[str, Any] | None":
    """Best-effort ``planning_problem`` for a refinement-candidate decision.

    Mirrors ``_mid_planning_decision_problem``'s "degrade to None rather than
    raise" philosophy, adapted for the ``date``-typed ``start_date``/
    ``end_date`` already on a loaded :class:`SeasonPlan` (the mid-planning
    variant takes ``datetime`` and calls ``.date()`` on it, which does not
    apply here).
    """
    try:
        from ...pipeline.state import StageName
        from ...planning_contract import build_planning_problem

        start_date = getattr(plan_obj, "start_date", None)
        end_date = getattr(plan_obj, "end_date", None)
        if not start_date or not end_date:
            return None
        cfg = state.read_stage(StageName.CONFIG) or {}
        scraping = state.read_stage(StageName.SCRAPING) or {}
        return build_planning_problem(cfg, scraping, start_date, end_date)
    except Exception:
        return None


def _decide_refinement_candidate(
    before_candidate: "dict[str, Any]",
    after_candidate: "dict[str, Any]",
    problem: "dict[str, Any] | None",
    *,
    run_id: str,
    iteration: int,
    work_dir: str,
    log_fn: "Any",
) -> "tuple[str, str]":
    """Decide whether to persist this refinement iteration's proposed candidate
    (issue #260 Phase 4: ``cli/plan_critic.py`` + the refinement-candidate
    verification boundary).

    ``after_candidate`` is the plan *after* this iteration's plan-critic
    moves have already been tentatively applied in memory (via
    ``ManualAdjustmentWorkflow.apply``, not yet persisted) — unlike the
    earlier, narrower version of this gate, the decision now sees the actual
    resulting candidate, verified and A/B-scored against the baseline via
    the same ``stage3_ab``/``stage3_decision`` machinery the Stage 3
    optimizer promote/reject decision uses, not just a description of the
    proposed moves.

    Returns ``(outcome, reason)`` where ``outcome`` is one of:
    - ``"no_judge"`` — no headless judge is configured (harness-active or
      ``RVV_JUDGE_BACKEND`` unset). The caller falls back to its own
      explicitly-named legacy auto-apply compatibility path so unattended/
      non-interactive production runs are unaffected by this change.
    - ``"persist"`` — a judge explicitly chose ``apply_candidate`` and the
      deterministic validator accepted it (which itself refuses to accept a
      candidate that fails the verifier).
    - ``"hold"`` — a judge is configured but chose not to apply this
      candidate, its action was deterministically rejected (including
      because ``after_candidate`` fails the verifier), or the judge call
      itself failed.

    Unlike the Stage 3 promote/reject, mid-planning critic, and multi-seed
    decisions (which fall back to the pre-existing deterministic
    quality-rank comparison on any judge failure), ``"hold"`` here does
    *not* fall back to auto-apply — a configured-but-failing judge leaves
    the baseline intact rather than silently reverting to the old
    always-on behavior. That old behavior is preserved only as the
    separate, explicitly-named ``"no_judge"`` legacy path.
    """
    from ...application.decisions import decide, record_llm_decision
    from ...llm_judge import build_action_decision_prompt, get_judge_if_headless, parse_action_verdict
    from ...stage3_ab import build_ab_report
    from ...stage3_decision import build_stage3_decision_context

    try:
        judge = get_judge_if_headless()
    except ValueError:
        return "no_judge", "no_judge_configured"
    if judge is None:
        return "no_judge", "no_judge_configured"

    try:
        report = build_ab_report(before_candidate, after_candidate, problem)
    except (ValueError, KeyError) as exc:
        log_fn(f"Refinement {iteration}: could not build A/B report for judge — holding: {exc}")
        return "hold", "ab_report_failed"

    baseline_ref = f"refinement_iteration_{iteration}:before"
    candidate_ref = f"refinement_iteration_{iteration}:after"
    context = build_stage3_decision_context(
        report,
        run_id=run_id,
        baseline_ref=baseline_ref,
        candidate_ref=candidate_ref,
        objective=(
            "Decide whether to apply this iteration's plan-critic-derived "
            "repair (proposed dates/moves already computed deterministically "
            "and applied to a trial candidate, verified and A/B-scored "
            "against the current plan), ask the operator, or keep the "
            "current plan unchanged."
        ),
    )
    try:
        raw_verdict = judge.judge(build_action_decision_prompt(context))
    except RuntimeError as exc:
        log_fn(f"Refinement {iteration}: judge call failed — holding: {exc}")
        return "hold", "judge_call_failed"

    action = parse_action_verdict(context, raw_verdict)
    if action.action_id == "apply_candidate" and not action.arguments.get("candidate_ref"):
        from dataclasses import replace as _dc_replace

        action = _dc_replace(action, arguments={**action.arguments, "candidate_ref": candidate_ref})

    result = decide(context, action)
    try:
        record_llm_decision(work_dir, context, action, result)
    except Exception as exc:
        log_fn(f"Refinement {iteration}: record_llm_decision failed: {exc}")

    if not result.accepted:
        log_fn(
            f"Refinement {iteration}: judge action {action.action_id!r} rejected "
            f"({result.rejection_reason}) — holding, not persisting"
        )
        return "hold", result.rejection_reason or "rejected"

    if action.action_id != "apply_candidate":
        log_fn(f"Refinement {iteration}: judge decided {action.action_id} — holding, not persisting")
        return "hold", action.action_id

    log_fn(
        f"Refinement {iteration}: judge decided apply_candidate "
        f"({(action.rationale or '')[:200]})"
    )
    return "persist", "apply_candidate"


def _refinement_metrics(checkpoint: "dict[str, Any] | Any") -> dict[str, Any]:
    """Underlying deterministic facts behind the rough/mixed/strong tone bucket.

    Used by :func:`_decide_continue_refinement` so the LLM/agent controller
    sees the actual measurements rather than a single pre-collapsed tone
    label standing in for them.

    Issue #260 Phase 4 ("separate fairness measurement from soft default
    threshold policy"): reads ``fairness_scoring.build_fairness_gate``'s
    canonical ``policy_gate`` (hard invariants + config-threaded
    thresholds only) rather than its legacy blended ``status``/``score`` —
    a soft/default measurement outside its reference threshold must not,
    by itself, look like control authority to this decision. The other
    (unconfigured-default) metrics are still surfaced under
    ``measurements`` for context, never as a pass/warn/fail authority.
    """
    plan_obj = _extract_plan_obj(checkpoint)
    gate = _fairness_gate(plan_obj)
    policy_gate = gate.get("policy_gate") or {}
    policy_gate_metrics = policy_gate.get("metrics") or []
    measurements = gate.get("measurements") or []
    return {
        "policy_gate_status": str(policy_gate.get("status", "pass")),
        "policy_gate_metrics": "; ".join(
            f"{m.get('key')}={m.get('value')} (threshold {m.get('threshold')}, {m.get('status')})"
            for m in policy_gate_metrics
        ),
        "measurements": "; ".join(
            f"{m.get('key')}={m.get('value')} (reference threshold {m.get('threshold')}, not policy)"
            for m in measurements
        ),
        "pairwise_matchup_score": _score_attr(plan_obj, "pairwise_matchup_score"),
        "diversity_score": _score_attr(plan_obj, "diversity_score"),
        "month_balance_score": _score_attr(plan_obj, "month_balance_score"),
        "game_count_spread": int(_score_attr(plan_obj, "game_count_spread")),
    }


def _decide_continue_refinement(
    metrics: "dict[str, Any]",
    *,
    run_id: str,
    iteration: int,
    work_dir: str,
    log_fn: "Any",
) -> "tuple[str, str]":
    """Decide whether to continue attempting refinement this iteration
    (issue #260 Phase 4: "remove tone classification from control authority").

    ``_run_refinement_loop`` previously used ``_compute_verdict_tone``'s
    rough/mixed/strong bucket as a hard gate: keep attempting refinement
    only while the tone is "rough", stop the moment it is anything else.
    That bucket remains a useful display label — ``tone``/``tone_label``
    are still computed and returned unchanged by the caller — but whether
    spending another iteration trying to improve the plan is worthwhile is
    a contextual tradeoff, not something three fixed threshold bands should
    decide unconditionally.

    When a headless judge is configured, builds a ``DecisionContext`` from
    :func:`_refinement_metrics` — the underlying measurements, not the tone
    bucket itself — and asks it to choose ``optimize_plan`` (continue
    attempting refinement this run) or ``keep_baseline`` (stop; the current
    plan is accepted as final for this run).

    Returns ``(outcome, reason)``:
    - ``"no_judge"`` — no judge configured; the caller falls back to the
      pre-existing tone-bucket gate (explicitly-named legacy compatibility,
      so unattended/non-interactive runs are unaffected).
    - ``"continue"`` — the judge chose ``optimize_plan``.
    - ``"stop"`` — the judge chose anything else, its action was
      deterministically rejected, or the judge call itself failed.
      Safe-by-default: a configured-but-failing/declining judge does not
      force more iterations that could keep mutating the plan without
      oversight — it simply stops where the plan already is, same as the
      "hold" outcome in :func:`_decide_refinement_candidate`.
    """
    from ...application.decisions import DecisionContext, decide, record_llm_decision
    from ...llm_judge import build_action_decision_prompt, get_judge_if_headless, parse_action_verdict

    try:
        judge = get_judge_if_headless()
    except ValueError:
        return "no_judge", "no_judge_configured"
    if judge is None:
        return "no_judge", "no_judge_configured"

    context = DecisionContext(
        run_id=run_id,
        capability="plan_critic_refinement",
        stage="refinement",
        objective=(
            "Decide whether to continue attempting plan-critic-driven "
            "refinement this iteration, or stop and accept the current "
            "plan as final for this run."
        ),
        facts=metrics,
        available_actions=("optimize_plan", "keep_baseline", "request_operator"),
    )
    try:
        raw_verdict = judge.judge(build_action_decision_prompt(context))
    except RuntimeError as exc:
        log_fn(f"Refinement {iteration}: continue-decision judge call failed — stopping: {exc}")
        return "stop", "judge_call_failed"

    action = parse_action_verdict(context, raw_verdict)
    result = decide(context, action)
    try:
        record_llm_decision(work_dir, context, action, result)
    except Exception as exc:
        log_fn(f"Refinement {iteration}: record_llm_decision failed: {exc}")

    if not result.accepted:
        log_fn(
            f"Refinement {iteration}: continue-decision action {action.action_id!r} rejected "
            f"({result.rejection_reason}) — stopping"
        )
        return "stop", result.rejection_reason or "rejected"

    if action.action_id == "optimize_plan":
        log_fn(f"Refinement {iteration}: judge decided to continue ({(action.rationale or '')[:200]})")
        return "continue", "optimize_plan"

    log_fn(f"Refinement {iteration}: judge decided {action.action_id} — stopping")
    return "stop", action.action_id
