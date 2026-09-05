"""Pipeline-oriented RVV CLI command handlers."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console

from ..pipeline.run_log_paths import resolve_active_run_log_dir

_console = Console()

_STAGE_NAMES = {1: "stage1", 2: "stage2", 3: "stage3"}

_DEFAULT_OPERATOR_OBJECTIVE = "Produce the best trustworthy season plan from the current workbook."


# Work dirs (as absolute-path strings) where a manifest persistence failure
# was observed during this process — i.e. this one CLI invocation. Since
# each ``rvv-miniputt``/``rvv-miniputt operator`` invocation is a fresh
# Python process, this never leaks state across runs; it exists purely so
# the final outcome computed near the end of ``_cmd_run`` (far away from
# where any individual manifest call happened) can tell whether persistence
# degraded at any point during *this* run (issue #14).
_MANIFEST_DEGRADED_WORK_DIRS: set[str] = set()


def _manifest_degraded(work_dir: str) -> bool:
    """Whether a manifest persistence failure was observed for *work_dir*
    during this process — see ``_MANIFEST_DEGRADED_WORK_DIRS``."""
    return str(Path(work_dir).resolve()) in _MANIFEST_DEGRADED_WORK_DIRS


def _warn_manifest_failure(work_dir: str, operation: str, exc: Exception) -> None:
    """Surface a manifest persistence failure instead of swallowing it silently.

    Issue #14: manifest reads/writes used to fail via a bare ``except
    Exception: pass``, so an AI operator (or a human) could see incomplete
    or stale control state with no indication anything went wrong. This
    prints a visible warning, appends a line to a manifest-specific warning
    log next to the workspace's normal per-run logs, and marks the work
    dir degraded for this process so the final run outcome can be capped at
    ``warning`` even if the scheduling pipeline itself completed cleanly —
    losing operator control-state must never look identical to a clean run.
    """
    message = f"Kunne ikke {operation} run manifest: {exc}"
    _console.print(f"[yellow]⚠[/yellow] {message}")
    _MANIFEST_DEGRADED_WORK_DIRS.add(str(Path(work_dir).resolve()))
    try:
        log_dir = Path(work_dir) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "manifest_warnings.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now().isoformat()} operation={operation} error={exc}\n")
    except OSError:
        pass  # the console warning above already happened; this is a bonus, not the primary signal


def _manifest_record(work_dir: str, capability: str, status: str, summary: str, **kwargs: Any) -> None:
    """Best-effort append to the run manifest. Never raises — the manifest is
    an operator-facing summary layered on top of the pipeline, not a
    dependency of it — but a failure is surfaced, not swallowed (issue #14)."""
    try:
        from ..pipeline.capability_result import CapabilityResult
        from ..pipeline.run_manifest import RunManifest

        RunManifest(work_dir).record_capability(
            CapabilityResult(status=status, summary=summary, capability=capability, **kwargs)
        )
    except Exception as exc:
        _warn_manifest_failure(work_dir, f"registrere kapabilitet '{capability}' i", exc)


def _manifest_set_active(work_dir: str, capability: str) -> None:
    """Best-effort mark *capability* active before it starts executing (issue #15).

    Counterpart to ``_manifest_record`` below, called before rather than
    after a stage runs, so a crash mid-stage still leaves a durable record
    of what was in progress when execution stopped instead of showing
    whatever the previous stage last recorded.
    """
    try:
        from ..pipeline.run_manifest import RunManifest

        RunManifest(work_dir).set_current_capability(capability)
    except Exception as exc:
        _warn_manifest_failure(work_dir, f"markere kapabilitet '{capability}' som aktiv i", exc)


def _manifest_start_run(work_dir: str, input_path: str, objective: str | None = None) -> None:
    """Best-effort start of a new run manifest at the top of ``rvv-miniputt run``."""
    try:
        from ..pipeline.fingerprints import file_sha256
        from ..pipeline.run_manifest import RunManifest

        input_fingerprint: dict[str, Any] = {"path": input_path}
        try:
            input_fingerprint["sha256"] = file_sha256(input_path)
        except OSError:
            pass
        RunManifest(work_dir).start_run(
            objective or _DEFAULT_OPERATOR_OBJECTIVE,
            input_fingerprint=input_fingerprint,
        )
    except Exception as exc:
        _warn_manifest_failure(work_dir, "starte", exc)


def _manifest_finalize(work_dir: str, outcome: str) -> None:
    """Best-effort finalization of the run manifest at the end of a run.

    When persistence degraded at any point during this run (issue #14), the
    outcome is capped at ``warning`` even if *outcome* would otherwise have
    been ``ok`` — a clean scheduling result whose control-state didn't
    reliably persist is not the same thing as a clean run.
    """
    if outcome == "ok" and _manifest_degraded(work_dir):
        outcome = "warning"
    try:
        from ..pipeline.run_manifest import RunManifest

        RunManifest(work_dir).finalize(outcome)
    except Exception as exc:
        _warn_manifest_failure(work_dir, "avslutte", exc)


def _judge_stage(
    stage_num: int,
    checkpoint_summary: dict[str, Any],
    state: "Any",
    log_fn: "Any",
    stage_name: "StageName | None" = None,
) -> bool:
    """Ask the headless judge whether to proceed after a stage.

    Returns True if the pipeline should continue, False if it should abort.

    Distinguishes "no judge configured at all" from "a judge is configured
    but the call failed" (issue #260 Phase 4 — "_judge_stage() still treats
    a configured judge failure as proceed"):

    - No judge present (harness active, or headless with
      ``RVV_JUDGE_BACKEND`` unset): always returns True — an explicitly-named
      legacy compatibility case where there is nothing to ask, not a
      decision at all. This is unchanged.
    - A judge *is* configured but the call itself fails (backend error):
      now returns False (abort) rather than silently proceeding. Every
      Stage 1-3 checkpoint is already persisted before this point, so
      aborting here is a safe, non-destructive stop — the run can be
      resumed from this stage — rather than an implicit "trust it anyway"
      policy choice standing in for an actual decision.

    The verdict is persisted into the stage checkpoint via
    ``state.write_judgment`` so it appears in ``.pipeline/stage*.json``.
    """
    import os as _os

    from ..application.decisions import DecisionAction, decide, record_llm_decision
    from ..llm_judge import build_decision_context, build_stage_prompt, get_judge_if_headless

    try:
        judge = get_judge_if_headless()
    except ValueError:
        # RVV_JUDGE_BACKEND not set — headless but no backend configured.
        # Treat as "proceed" so the pipeline is not silently broken.
        return True
    if judge is None:
        return True  # harness is active — it will judge interactively

    backend_name = _os.environ.get("RVV_JUDGE_BACKEND", "unknown")
    stage_key = _STAGE_NAMES.get(stage_num, f"stage{stage_num}")
    try:
        decision_context = build_decision_context(stage_key, checkpoint_summary)
        prompt = build_stage_prompt(stage_key, checkpoint_summary)
    except ValueError:
        # Unknown stage — fall back to a generic prompt and no decision context
        # (the decision-contract audit trail is skipped for unrecognised stages).
        decision_context = None
        prompt = (
            f"Pipeline stage {stage_num} completed. "
            f"Summary: {checkpoint_summary}. "
            "Respond PROCEED or ABORT."
        )
    try:
        verdict_raw = judge.judge(prompt).strip()
    except RuntimeError as exc:
        # Safe-by-default (issue #260 Phase 4): a configured judge that
        # fails to answer is not the same as no judge being configured at
        # all. Aborting is safe here — every prior stage's checkpoint is
        # already persisted, so the run can simply be resumed from this
        # stage rather than silently trusting an unavailable judge.
        log_fn(f"Stage {stage_num} judge call failed: {exc}")
        _console.print(f"  [red]✗[/red] Dommerkall feilet etter Stage {stage_num}: {exc} — avbryter (kan gjenopptas)")
        if stage_name is not None:
            try:
                state.write_judgment(stage_name, "ERROR", reasoning=str(exc), backend=backend_name)
            except Exception:
                pass
        return False

    # Split verdict keyword from any trailing reasoning text.
    lines = verdict_raw.splitlines()
    verdict_keyword = lines[0].strip() if lines else verdict_raw
    reasoning = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""

    log_fn(f"Stage {stage_num} judge verdict: {verdict_raw[:200]}")
    log_fn(f"Stage {stage_num} judge backend: {backend_name}")

    if stage_name is not None:
        try:
            state.write_judgment(
                stage_name,
                verdict=verdict_keyword,
                reasoning=reasoning,
                backend=backend_name,
            )
        except Exception as exc:
            log_fn(f"Stage {stage_num} write_judgment failed: {exc}")

    if decision_context is not None:
        action_id = "abort" if verdict_keyword.upper().startswith("ABORT") else "proceed"
        decision_action = DecisionAction(action_id=action_id, rationale=reasoning or verdict_raw)
        decision_result = decide(decision_context, decision_action)
        try:
            record_llm_decision(str(state.work_dir), decision_context, decision_action, decision_result)
        except Exception as exc:
            log_fn(f"Stage {stage_num} record_llm_decision failed: {exc}")
        if not decision_result.accepted:
            # Deterministic validator rejected the LLM's action (e.g. it
            # is not one of the vocabulary this context offers) — this
            # cannot happen for proceed/abort today, but stay safe rather
            # than silently trusting an unvalidated verdict.
            log_fn(f"Stage {stage_num} decision rejected: {decision_result.rejection_reason}")
            _console.print(
                f"  [red]✗ Ugyldig dommeravgjørelse etter Stage {stage_num}:[/red] "
                f"{decision_result.rejection_reason}"
            )
            return False

    if verdict_keyword.upper().startswith("ABORT"):
        _console.print(f"  [red]✗ Headless dommer avbrøt etter Stage {stage_num}:[/red] {verdict_raw}")
        return False
    return True


def _extract_plan_obj(plan: "dict[str, Any] | Any") -> "dict[str, Any] | Any":
    """Return the SeasonPlan-like payload from a Stage 3 checkpoint or plan object."""
    return plan.get("plan", plan) if isinstance(plan, dict) else plan


def _score_attr(plan_obj: "dict[str, Any] | Any", name: str, default: float = 0.0) -> float:
    """Read a numeric metric from either a dict payload or a SeasonPlan object."""
    if isinstance(plan_obj, dict):
        raw = plan_obj.get(name, default)
    else:
        raw = getattr(plan_obj, name, default)
    try:
        return float(raw or default)
    except (TypeError, ValueError):
        return default


def _fairness_gate(plan_obj: "dict[str, Any] | Any") -> dict[str, Any]:
    """Read the fairness gate mapping from either a dict payload or a plan object."""
    gate = plan_obj.get("fairness_gate", {}) if isinstance(plan_obj, dict) else getattr(plan_obj, "fairness_gate", {})
    return gate if isinstance(gate, dict) else {}


def _compute_verdict_tone(plan: "dict[str, Any] | Any") -> str:
    """Compute the verdict tone ('rough', 'mixed', or 'strong') from a plan.

    Accepts either a Stage 3 checkpoint dict (with a ``"plan"`` key holding a
    SeasonPlan-like object) or a SeasonPlan object directly.  Delegates to
    ``judgment._score_tone`` using the plan's stored metric scores.
    """
    from ..html.renderers import judgment as _judgment
    from ..pipeline.stage4_helpers import _dict_to_plan

    plan_obj = _extract_plan_obj(plan)

    if isinstance(plan_obj, dict):
        try:
            plan_obj = _dict_to_plan(plan_obj)
        except Exception:
            pass

    fairness_gate = _fairness_gate(plan_obj)
    gate_status = str(fairness_gate.get("status", "pass")).lower()
    gate_score = int(fairness_gate.get("score", 0) or 0)

    pairwise = _score_attr(plan_obj, "pairwise_matchup_score")
    diversity = _score_attr(plan_obj, "diversity_score")
    month_balance = _score_attr(plan_obj, "month_balance_score")

    return _judgment._score_tone(
        gate_status=gate_status,
        gate_score=gate_score,
        pairwise=pairwise,
        diversity=diversity,
        month_balance=month_balance,
        missing_hosts=[],
        spread=0,
    )


def _plan_attempt_quality(plan: "dict[str, Any] | Any") -> dict[str, Any]:
    """Return comparable quality components for a Stage 3 retry attempt.

    Fairness gate score is already stored on a 0-100 scale while the planner's
    pairwise/diversity/month-balance metrics are fractions.  Normalize all four
    into the same 0-100-ish composite so an attempt is not kept merely because
    it won a single gate score while regressing the rest of the plan.
    """
    plan_obj = _extract_plan_obj(plan)
    gate = _fairness_gate(plan_obj)
    gate_status = str(gate.get("status", "pass")).lower()
    fairness_score = float(gate.get("score", 0) or 0)
    pairwise = _score_attr(plan_obj, "pairwise_matchup_score")
    diversity = _score_attr(plan_obj, "diversity_score")
    month_balance = _score_attr(plan_obj, "month_balance_score")
    composite_score = fairness_score + (pairwise * 100.0) + (diversity * 100.0) + (month_balance * 100.0)
    status_rank = {"fail": 0, "warn": 1, "pass": 2}.get(gate_status, 1)

    return {
        "gate_status": gate_status,
        "fairness_score": fairness_score,
        "pairwise_matchup_score": pairwise,
        "diversity_score": diversity,
        "month_balance_score": month_balance,
        "composite_score": composite_score,
        "rank": (
            status_rank,
            composite_score,
            fairness_score,
            pairwise,
            diversity,
            month_balance,
        ),
    }


def _format_plan_attempt_quality(quality: dict[str, Any]) -> str:
    """Human-readable one-line quality summary for logs/console output."""
    return (
        f"gate={quality['gate_status']}:{quality['fairness_score']:.0f}, "
        f"pairwise={quality['pairwise_matchup_score']:.2f}, "
        f"diversity={quality['diversity_score']:.2f}, "
        f"month={quality['month_balance_score']:.2f}, "
        f"composite={quality['composite_score']:.1f}"
    )


_MAX_REFINEMENT_ITERATIONS = 3


def _refinement_decision_problem(state: "Any", plan_obj: "Any") -> "dict[str, Any] | None":
    """Best-effort ``planning_problem`` for a refinement-candidate decision.

    Mirrors ``_mid_planning_decision_problem``'s "degrade to None rather than
    raise" philosophy, adapted for the ``date``-typed ``start_date``/
    ``end_date`` already on a loaded :class:`SeasonPlan` (the mid-planning
    variant takes ``datetime`` and calls ``.date()`` on it, which does not
    apply here).
    """
    try:
        from ..pipeline.state import StageName
        from ..planning_contract import build_planning_problem

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
    from ..application.decisions import decide, record_llm_decision
    from ..llm_judge import build_action_decision_prompt, get_judge_if_headless, parse_action_verdict
    from ..stage3_ab import build_ab_report
    from ..stage3_decision import build_stage3_decision_context

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
    from ..application.decisions import DecisionContext, decide, record_llm_decision
    from ..llm_judge import build_action_decision_prompt, get_judge_if_headless, parse_action_verdict

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


def _run_refinement_loop(
    plan_checkpoint: "dict[str, Any]",
    state: "Any",
    args: "argparse.Namespace",
    strict: bool,
    log_fn: "Any",
) -> "tuple[str, dict[str, Any]]":
    """Run the skill-driven plan refinement loop after Stage 4.

    On each iteration:
    1. Computes the tone display label and decides whether to continue
       attempting refinement at all (:func:`_decide_continue_refinement`,
       issue #260 Phase 4: "remove tone classification from control
       authority") — a headless judge, when configured, sees the
       underlying metrics (:func:`_refinement_metrics`) and chooses,
       rather than the rough/mixed/strong bucket alone deciding.
    2. Loads the current SeasonPlan and generates every deterministic
       critic finding (:func:`plan_critic.generate_critic_findings`,
       uncapped/unranked — issue #260 Phase 4); stops if there are none.
    3. Branches on whether a judge is configured (issue #260 Phase 4 —
       "remove plan_critic.suggest_moves from the canonical LLM path"):

       - **No judge at all** (explicitly-named legacy compatibility path,
         for unattended/non-interactive runs): ``plan_critic.suggest_moves()``
         picks one bespoke repair per finding and it is applied
         unconditionally, exactly as before this change.
       - **A judge is configured** (canonical path, already chosen to
         continue in step 1): the repair candidate comes from the existing
         Stage 3 v2 optimizer/search (:func:`stage3_optimizer.optimize_candidate`)
         instead of a bespoke per-finding repair. The optimized candidate's
         SeasonPlan-derived metadata is recomputed via
         ``ManualAdjustmentWorkflow.apply()``, then verified and A/B-scored
         against the current plan and decided via
         :func:`_decide_refinement_candidate` — a headless judge must
         explicitly choose ``apply_candidate`` for it to be persisted; a
         configured-but-failing/declining judge leaves the baseline intact
         (safe-by-default) rather than silently auto-applying.

    Args:
        plan_checkpoint: Stage 3 checkpoint dict (with a ``"plan"`` key).
        state: ``PipelineState`` instance for reading/writing stage checkpoints.
        args: Parsed CLI arguments (used for logging context).
        strict: Whether the pipeline is in strict mode.
        log_fn: Callable that appends a timestamped message to the run log.

    Returns:
        A ``(tone, updated_plan_checkpoint)`` tuple with the final tone string
        and the updated Stage 3 checkpoint dict after all refinement rounds.
    """
    from datetime import date as _date

    from ..pipeline.manual_adjustment_workflow import ManualAdjustmentWorkflow
    from ..pipeline.stage3_helpers import _resolve_plan_dict
    from ..pipeline.stage4_helpers import _dict_to_plan
    from ..pipeline.state import StageName
    from ..stage3_optimizer import optimize_candidate
    from .plan_critic import generate_critic_findings, suggest_moves

    workflow = ManualAdjustmentWorkflow(state)
    current_checkpoint = plan_checkpoint
    try:
        from ..pipeline.run_manifest import RunManifest

        run_id = str(RunManifest(state.work_dir).read().get("run_id") or "")
    except Exception:
        run_id = ""

    for iteration in range(1, _MAX_REFINEMENT_ITERATIONS + 1):
        tone = _compute_verdict_tone(current_checkpoint)
        log_fn(f"Refinement iteration {iteration}: tone={tone} (display label)")
        _console.print(f"  [dim]Refinement {iteration}/{_MAX_REFINEMENT_ITERATIONS}: tone={tone}[/dim]")

        continue_outcome, continue_reason = _decide_continue_refinement(
            _refinement_metrics(current_checkpoint),
            run_id=run_id,
            iteration=iteration,
            work_dir=str(state.work_dir),
            log_fn=log_fn,
        )
        if continue_outcome == "no_judge":
            # Legacy compatibility: no headless judge configured — preserve
            # the pre-existing tone-bucket gate so unattended/non-interactive
            # production runs are unaffected by this decision.
            if tone != "rough":
                log_fn(f"Refinement loop exiting early at iteration {iteration}: tone={tone} (legacy tone-gate)")
                return tone, current_checkpoint
        elif continue_outcome == "stop":
            log_fn(f"Refinement loop stopping at iteration {iteration} per continue-decision ({continue_reason})")
            return tone, current_checkpoint
        # else continue_outcome == "continue": proceed regardless of tone —
        # the "no critic findings" check just below still stops the loop
        # once there is genuinely nothing left to fix.

        # Load current SeasonPlan object
        try:
            plan_obj = workflow.load_plan()
        except Exception as exc:
            log_fn(f"Refinement {iteration}: load_plan failed: {exc}")
            _console.print(f"  [yellow]⚠[/yellow] Refinement {iteration}: kan ikke laste plan: {exc}")
            break

        # Snapshot the plan *before* any repair is applied, for the
        # verify+A/B decision below — must be captured now, since plan_obj
        # is mutated in place by move_date/apply() in the legacy branch.
        before_candidate = _resolve_plan_dict(plan_obj)

        # Generate every deterministic critic finding (uncapped/unranked —
        # which one matters most is a contextual judgment, not this
        # function's to make). Only used to decide whether there is
        # anything worth attempting; which repair to try is no longer
        # decided by parsing these messages (see the canonical branch below).
        findings = generate_critic_findings(plan_obj)
        if not findings:
            log_fn(f"Refinement {iteration}: no critic findings — stopping")
            break

        if continue_outcome == "no_judge":
            # LEGACY no-judge compatibility path (issue #260 Phase 4 —
            # "remove plan_critic.suggest_moves from the canonical LLM
            # path"): plan_critic.suggest_moves() picks one bespoke repair
            # per finding (e.g. hardcoding "+7 days" for an arena collision)
            # and it is applied unconditionally, exactly as before this
            # change. Kept only for unattended/non-interactive runs with no
            # judge configured at all — the canonical branch below never
            # calls suggest_moves().
            issues = [finding["message"] for finding in findings]
            moves = suggest_moves(plan_obj, issues)
            auto_moves = [m for m in moves if m.get("can_auto_fix")]
            if not auto_moves:
                log_fn(f"Refinement {iteration}: no auto-fixable moves — stopping")
                break

            # Apply auto-fixable moves directly via TournamentUpdater.move_date
            # when both tournament_id and new_date are present. Fall back to
            # banning the old date (so the planner picks a replacement) for
            # any move that lacks a tournament_id or cannot be moved directly.
            requested_adjustments: dict[str, list[str]] = {}
            direct_move_count = 0
            for move in auto_moves:
                new_date_str = move.get("new_date")
                old_date = move.get("old_date")
                tid = move.get("tournament_id", "")
                if not new_date_str:
                    continue
                log_fn(f"  Move: tournament={tid} → {new_date_str} ({move.get('reason', '')[:80]})")
                if tid and workflow.updater is not None:
                    try:
                        parsed_new_date = _date.fromisoformat(new_date_str)
                    except ValueError:
                        log_fn(f"  Move skipped: cannot parse new_date={new_date_str!r}")
                        continue
                    try:
                        move_result = workflow.updater.move_date(
                            tid, parsed_new_date, plan=plan_obj, force=True, cascade=True
                        )
                        direct_move_count += 1
                        log_fn(f"  move_date({tid}, {parsed_new_date}): {move_result.summary_nb[:80]}")
                    except Exception as exc:
                        log_fn(f"  move_date({tid}) failed: {exc} — falling back to banned_dates")
                        if old_date:
                            requested_adjustments.setdefault("banned_dates", []).append(old_date)
                else:
                    # No tournament_id — ban the old date so the planner relocates it
                    if old_date:
                        requested_adjustments.setdefault("banned_dates", []).append(old_date)

            if requested_adjustments:
                existing_adj = getattr(plan_obj, "manual_adjustments", {}) or {}
                plan_obj.manual_adjustments = ManualAdjustmentWorkflow.merge_manual_adjustments(
                    existing_adj, requested_adjustments
                )

            has_direct_moves = direct_move_count > 0
            has_adj_changes = any(v for v in requested_adjustments.values())
            if not has_direct_moves and not has_adj_changes:
                log_fn(f"Refinement {iteration}: no effective changes — skipping apply/persist")
                break

            try:
                result = workflow.apply(plan_obj)
            except Exception as exc:
                log_fn(f"Refinement {iteration}: apply() failed: {exc}")
                _console.print(f"  [yellow]⚠[/yellow] Refinement {iteration}: apply feilet: {exc}")
                break

            try:
                workflow.updater.write_updated_checkpoint(plan_obj, log_entry=result)
            except Exception as exc:
                log_fn(f"Refinement {iteration}: write_updated_checkpoint failed: {exc}")

            updated = state.read_stage(StageName.PLANNING)
            if updated:
                current_checkpoint = updated
            else:
                log_fn(f"Refinement {iteration}: could not re-read PLANNING checkpoint after apply")

            log_fn(f"Refinement {iteration} applied (legacy suggest_moves path): {result.summary_nb[:120]}")
            _console.print(
                f"  [cyan]✓[/cyan] Refinement {iteration}: {result.summary_nb[:80]}"
                if result.success
                else f"  [yellow]⚠[/yellow] Refinement {iteration}: {result.summary_nb[:80]}"
            )
            continue

        # CANONICAL path (a headless judge is configured and already chose
        # to continue via _decide_continue_refinement above): reuse the
        # existing Stage 3 v2 optimizer/search instead of
        # plan_critic.suggest_moves() picking one bespoke repair. This is
        # the same optimizer plan_command._execute_optimize_plan and
        # stage3_ab use — "arena collision exists" is a fact
        # (generate_critic_findings), but the resulting repair is a
        # generic search outcome, not a hardcoded date offset.
        problem = _refinement_decision_problem(state, plan_obj)
        try:
            after_candidate = optimize_candidate(before_candidate, problem)
        except Exception as exc:
            log_fn(f"Refinement {iteration}: optimize_candidate failed — stopping: {exc}")
            _console.print(f"  [yellow]⚠[/yellow] Refinement {iteration}: optimalisering feilet: {exc}")
            break

        # Recompute SeasonPlan-derived metadata (fairness_gate,
        # diversity/pairwise/month-balance scores, team_game_counts, ...)
        # for the optimized candidate — the Stage 3 v2 candidate contract
        # carries none of this, and both this loop's own tone/metric
        # bookkeeping and Stage 4 export need it. ManualAdjustmentWorkflow
        # .apply() already does exactly this recompute (its "recalculate
        # metrics and conflicts" step runs regardless of whether there are
        # manual_adjustments to process), so it is reused here rather than
        # duplicating that logic.
        after_plan_obj = _dict_to_plan(after_candidate)
        try:
            result = workflow.apply(after_plan_obj)
        except Exception as exc:
            log_fn(f"Refinement {iteration}: metadata recompute after optimize failed — stopping: {exc}")
            _console.print(f"  [yellow]⚠[/yellow] Refinement {iteration}: metadata-oppdatering feilet: {exc}")
            break
        after_full_candidate = _resolve_plan_dict(after_plan_obj)

        outcome, reason = _decide_refinement_candidate(
            before_candidate,
            after_full_candidate,
            problem,
            run_id=run_id,
            iteration=iteration,
            work_dir=str(state.work_dir),
            log_fn=log_fn,
        )
        if outcome != "persist":
            log_fn(f"Refinement {iteration}: not persisting optimizer candidate ({reason}) — stopping")
            _console.print(
                f"  [yellow]⚠[/yellow] Refinement {iteration}: optimalisert kandidat ikke akseptert ({reason}) — stopper."
            )
            break

        try:
            workflow.updater.write_updated_checkpoint(after_plan_obj, log_entry=result)
        except Exception as exc:
            log_fn(f"Refinement {iteration}: write_updated_checkpoint failed: {exc}")

        updated = state.read_stage(StageName.PLANNING)
        if updated:
            current_checkpoint = updated
        else:
            log_fn(f"Refinement {iteration}: could not re-read PLANNING checkpoint after apply")

        log_fn(f"Refinement {iteration}: optimizer candidate accepted and persisted ({reason})")
        _console.print(f"  [cyan]✓[/cyan] Refinement {iteration}: optimalisert kandidat akseptert og lagret.")

    # Final tone check after loop
    final_tone = _compute_verdict_tone(current_checkpoint)
    log_fn(f"Refinement loop ended after {_MAX_REFINEMENT_ITERATIONS} max iterations: final tone={final_tone}")
    return final_tone, current_checkpoint


def _cmd_calendars(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt calendars [--refresh]``."""
    from ..pipeline.calendar_viewer import generate_html
    from ..pipeline.cache_manager import ScrapedDataCache
    from ..utils.calendar_cache import CalendarCache

    work_dir = args.work_dir

    if args.refresh:
        _console.print("[bold]🔄 Tvinger full re-skraping av kalendere...[/bold]\n")

        # 1. Clear iCal scraper cache (avoids stale cached HTTP responses)
        _console.print("  Tømmer iCal-skraper-cache...", end=" ")
        try:
            CalendarCache(work_dir=work_dir).clear()
            _console.print("[green]✓[/green]")
        except Exception as exc:
            _console.print(f"[yellow]⚠[/yellow] ({exc})")

        # 2. Mark unified cache stale
        _console.print("  Markerer unified-cache som utdatert...", end=" ")
        try:
            ScrapedDataCache(work_dir=work_dir).force_refresh()
            _console.print("[green]✓[/green]")
        except Exception as exc:
            _console.print(f"[yellow]⚠[/yellow] ({exc})")

        # 3. Run stage 2 scraping
        _console.print("  Skraper kalendere (Stage 2)...")
        try:
            from ..pipeline.state import PipelineState, StageName
            from ..pipeline.stage1_config import load_effective_config
            from ..pipeline.stage2_scraping import run as stage2_run

            state = PipelineState(work_dir)
            cfg = load_effective_config(state)
            if not cfg:
                _console.print("[red]✗[/red] Stage 1 checkpoint mangler — kjør 'rvv-miniputt run' først")
                return 1

            start = datetime.strptime(cfg["start_date"], "%Y-%m-%d")
            end = datetime.strptime(cfg["end_date"], "%Y-%m-%d")
            result = stage2_run(cfg, state, start, end, strict=False)
            n = len(result.get("sources", []))
            blocked = result.get("blocked", [])
            _console.print(f"  [green]✓[/green] Stage 2: {n} kilder, {len(blocked)} blokkert")
        except Exception as exc:
            _console.print(f"  [red]✗[/red] Stage 2 feilet: {exc}")
            return 1

        # 4. Rebuild unified cache from the fresh Stage 2 checkpoint
        _console.print("  Bygger unified-cache fra Stage 2 checkpoint...", end=" ")
        try:
            scraping_result = state.read_stage(StageName.SCRAPING)
            if scraping_result:
                ScrapedDataCache(work_dir=work_dir).build_from_checkpoint(cfg, scraping_result)
                _console.print("[green]✓[/green]")
            else:
                _console.print("[yellow]⚠[/yellow] (ingen Stage 2 checkpoint)")
        except Exception as exc:
            _console.print(f"[yellow]⚠[/yellow] ({exc})")

        # 5. Regenerate calendar HTML (in export/ alongside season plan)
        _console.print("  Genererer calendars.html...", end=" ")
        try:
            path = generate_html(work_dir=work_dir, export_dir="export")
            _console.print(f"[green]✓[/green] {path}")
        except Exception as exc:
            _console.print(f"[red]✗[/red] {exc}")
            return 1

        _console.print(f"\n[bold green]✓ Full re-skraping fullført.[/bold green]")
        return 0

    # No --refresh: just regenerate HTML from cache (in export/)
    _console.print("Genererer calendars.html fra cache...", end=" ")
    try:
        path = generate_html(work_dir=work_dir, export_dir="export")
        _console.print(f"[green]✓[/green] {path}")
    except Exception as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1
    return 0



def _resolve_run_log_dir(args: argparse.Namespace, state: "Any", start_time: datetime) -> Path:
    """Return the export folder where the current run log should live."""
    export_dir = Path(getattr(args, "export_dir", "export"))
    if not export_dir.is_absolute():
        export_dir = Path.cwd() / export_dir
    if getattr(args, "timestamped_export", True):
        export_dir = export_dir / start_time.strftime("%Y-%m-%dT%H%M")
    return resolve_active_run_log_dir(state, preferred_export_dir=export_dir)



def _archive_structured_run_log(work_dir: str, export_log_dir: Path) -> None:
    """Move the JSONL run log into the export folder when it exists."""
    try:
        from ..pipeline.run_manifest import RunManifest

        run_id = (RunManifest(work_dir).read().get("run_id") or "").strip()
        if not run_id:
            return
        source_dir = Path(work_dir) / "logs"
        source = source_dir / f"{run_id}.jsonl"
        if not source.exists():
            return
        export_log_dir.mkdir(parents=True, exist_ok=True)
        target = export_log_dir / source.name
        if target.exists():
            target.unlink()
        source.replace(target)
    except Exception:
        pass



def _write_run_log(
    args: argparse.Namespace,
    state: "Any",
    start_time: datetime,
    lines: list[str],
    *,
    success: bool,
) -> None:
    """Write a per-run log file into the export folder."""
    log_dir = _resolve_run_log_dir(args, state, start_time)
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = start_time.strftime("%Y%m%d_%H%M%S")
    status = "OK" if success else "FAILED"
    filename = f"pipeline_run_{timestamp}_{status}.log"
    log_path = log_dir / filename

    content = f"# Pipeline run log\n"
    content += f"# Started: {start_time.isoformat()}\n"
    content += f"# Status: {'SUCCESS' if success else 'FAILED'}\n\n"
    for line in lines:
        content += line + "\n"

    log_path.write_text(content, encoding="utf-8")
    _archive_structured_run_log(args.work_dir, log_dir)
    _console.print(f"[dim]Run log saved: {log_path}[/dim]")



def _resolve_resume_stage(value: str | int | None) -> int:
    mapping = {
        "1": 1, "config": 1, "stage1": 1,
        "2": 2, "scraping": 2, "stage2": 2,
        "3": 3, "planning": 3, "plan": 3, "stage3": 3,
        "4": 4, "export": 4, "stage4": 4,
    }
    if value is None:
        return 1
    return mapping.get(str(value).lower(), 1)



def _force_refresh_stage2_inputs(work_dir: str) -> None:
    from ..pipeline.cache_manager import ScrapedDataCache
    from ..utils.calendar_cache import CalendarCache

    CalendarCache(work_dir=work_dir).clear()
    ScrapedDataCache(work_dir=work_dir).force_refresh()


def _run_approval_gate(
    args: argparse.Namespace,
    plan_checkpoint: "dict[str, Any]",
    state: "Any",
    strict: bool,
    console: "Console",
    log_fn: "Any",
) -> bool:
    """Run the fairness gate and plan critic checks.

    Returns False when *strict* is True and fairness_gate.status is "fail",
    blocking the pipeline.  Returns True in all other cases (warn, pass, or
    non-strict failure).
    """
    from .plan_critic import generate_critic_summary

    season_plan = plan_checkpoint.get("plan") if isinstance(plan_checkpoint, dict) else None

    # ── Fairness gate check ──────────────────────────────────────────────────
    fairness_gate: dict[str, Any] = {}
    if isinstance(season_plan, dict):
        fairness_gate = season_plan.get("fairness_gate") or {}
    elif season_plan is not None:
        fairness_gate = getattr(season_plan, "fairness_gate", {}) or {}

    gate_status = str(fairness_gate.get("status", "pass")).lower() if isinstance(fairness_gate, dict) else "pass"

    if gate_status == "fail":
        gate_score = fairness_gate.get("score", 0) if isinstance(fairness_gate, dict) else 0
        console.print(
            f"  [red]✗[/red] Kritisk rettferdighetsfeil (score={gate_score}) — planen oppfyller ikke minimumskravene."
        )
        log_fn(f"Approval gate FAILED: fairness_gate status=fail score={gate_score}")
        if strict:
            return False
        console.print("  [yellow]⚠[/yellow] Fortsetter pga --non-strict")

    elif gate_status == "warn":
        if season_plan is not None:
            try:
                issues = generate_critic_summary(season_plan)
                if issues:
                    console.print("[bold cyan]Plan critic (advarsel):[/bold cyan]")
                    for issue in issues:
                        console.print(f"  [yellow]⚠[/yellow] {issue}")
            except Exception as exc:
                console.print(f"  [yellow]⚠[/yellow] Plan critic feilet: {exc}")

    # ── General critic pass (for pass status) ───────────────────────────────
    if gate_status == "pass" and season_plan is not None:
        try:
            issues = generate_critic_summary(season_plan)
            if issues:
                console.print("[bold cyan]Plan critic:[/bold cyan]")
                for issue in issues:
                    console.print(f"  [cyan]•[/cyan] {issue}")
            else:
                console.print("[bold cyan]Plan critic:[/bold cyan] Ingen problemer oppdaget.")
        except Exception as exc:
            console.print(f"  [yellow]⚠[/yellow] Plan critic feilet: {exc}")

    return True


def _check_stage2_checkpoint(
    scraping_checkpoint: "dict[str, Any]",
    strict: bool,
    console: "Console",
    log_fn: "Any",
    *,
    harness_active: bool = False,
) -> bool:
    """Deterministic Stage 2 gate: inspect checkpoint fields directly.

    Reads ``sources[].event_count``, ``sources[].blocked``, and ``blocked[]``
    from *scraping_checkpoint* to decide whether the pipeline should proceed
    to Stage 3.

    When *harness_active* is True the gate auto-proceeds if at least one
    source returned events (threshold check), avoiding any interactive prompt.
    When *harness_active* is False and strict mode is on, the operator is
    prompted to confirm before proceeding despite warnings.

    Args:
        scraping_checkpoint: Stage 2 checkpoint dict written by stage2_scraping.run().
        strict: Whether the pipeline is running in strict mode.
        console: Rich ``Console`` for interactive output.
        log_fn: Callable that appends a message to the run log.
        harness_active: True when running headless under a harness (no LLM judge
            configured) — skips interactive prompts and uses threshold logic only.

    Returns:
        ``True`` if the pipeline should proceed to Stage 3, ``False`` if it should
        halt.
    """
    sources: list[dict[str, Any]] = scraping_checkpoint.get("sources", [])
    blocked_names: list[str] = scraping_checkpoint.get("blocked", [])

    total_events = sum(s.get("event_count", 0) for s in sources if not s.get("blocked"))
    sources_with_events = sum(
        1 for s in sources if not s.get("blocked") and s.get("event_count", 0) > 0
    )
    blocked_count = len(blocked_names)

    log_fn(
        f"Stage 2 checkpoint check: {sources_with_events} sources with events, "
        f"{total_events} total events, {blocked_count} blocked"
    )

    # No sources configured — nothing to validate; let the pipeline proceed.
    if not sources:
        log_fn("Stage 2 gate: no sources configured — skipping threshold check")
        return True

    if sources_with_events == 0:
        console.print(
            "  [red]✗[/red] Stage 2-sjekkpunkt: ingen kilder returnerte hendelser"
        )
        log_fn("Stage 2 gate FAIL: zero sources with events")
        if not strict:
            console.print("  [yellow]⚠[/yellow] Fortsetter pga --non-strict")
            return True
        return False

    if blocked_count > 0:
        console.print(
            f"  [yellow]⚠[/yellow] Stage 2-sjekkpunkt: {blocked_count} kilde(r) blokkert, "
            f"men {sources_with_events} kilde(r) returnerte hendelser"
        )
        log_fn(f"Stage 2 gate WARN: {blocked_count} blocked sources")

        if harness_active:
            # Harness mode: threshold met (at least one source with events) — auto-proceed
            log_fn("Stage 2 gate: harness active, threshold met — auto-proceeding")
            return True

        if not strict:
            console.print("  [yellow]⚠[/yellow] Fortsetter pga --non-strict")
            return True

        # strict + interactive: ask the operator
        try:
            answer = input("\n  Vil du fortsette til planlegging likevel? (j/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        log_fn(f"Operator confirmation answer (stage2 gate): {answer!r}")
        if answer in ("j", "y", "ja", "yes"):
            console.print("  [yellow]⚠[/yellow] Operatør har overstyrt advarsel — fortsetter")
            log_fn("Stage 2 gate WARN overridden by operator")
            return True
        return False

    # All sources OK
    log_fn("Stage 2 gate PASS: all sources returned events")
    return True


def _run_stage1(
    args: "argparse.Namespace",
    state: "Any",
    strict: bool,
    log_fn: "Any",
    resume_from: int,
) -> "tuple[dict[str, Any] | None, bool]":
    """Run Stage 1 (config) or skip it when resuming from a later stage.

    Returns ``(cfg, abort)`` where *cfg* is the loaded config dict and
    *abort* is True if the pipeline should stop (caller should write the run
    log and return 1).
    """
    from ..pipeline.stage1_config import load_effective_config, run as stage1_run
    from ..pipeline.state import StageName

    if resume_from <= 1:
        _console.print("[bold]Stage 1:[/bold] Konfigurasjon...")
        try:
            stage1_run(args.input, state, strict=strict)
            cfg = load_effective_config(state, input_path=args.input)
            _console.print(
                f"  [green]✓[/green] {len(cfg.get('sources', []))} kilder, "
                f"{cfg.get('start_date', '?')} → {cfg.get('end_date', '?')}"
            )
            log_fn(
                f"Stage 1 OK: {cfg.get('source_count', 0)} sources, "
                f"{cfg.get('start_date', '?')} → {cfg.get('end_date', '?')}"
            )
            stage1_summary = {
                "sources": len(cfg.get("sources", [])),
                "start_date": cfg.get("start_date", "?"),
                "end_date": cfg.get("end_date", "?"),
                "age_groups": cfg.get("age_groups", []),
                "clubs": cfg.get("clubs", []),
            }
            if not _judge_stage(1, stage1_summary, state, log_fn, stage_name=StageName.CONFIG):
                return None, True
        except Exception as exc:
            _console.print(f"  [red]✗[/red] {exc}")
            log_fn(f"Stage 1 FAILED: {exc}")
            return None, True
    else:
        cfg = load_effective_config(state)
        if not cfg:
            _console.print("[red]✗[/red] Kan ikke gjenoppta: Stage 1-checkpoint mangler.")
            return None, True
        _console.print("[bold]Stage 1:[/bold] Hoppet over (gjenopptatt)")
        log_fn("Stage 1 skipped via --resume-from")

    return cfg, False


def _run_stage2(
    args: "argparse.Namespace",
    cfg: "dict[str, Any]",
    state: "Any",
    start: "Any",
    end: "Any",
    strict: bool,
    log_fn: "Any",
    resume_from: int,
) -> "tuple[dict[str, Any] | None, bool, bool]":
    """Run Stage 2 (scraping) or skip it when resuming from a later stage.

    Returns ``(scraping, abort, stage_failed)`` where *scraping* is the
    checkpoint dict, *abort* is True when the pipeline should stop (caller
    writes the run log and returns 1), and *stage_failed* is True when the
    stage failed in non-strict mode (pipeline continues but ``run_failed``
    should be set).
    """
    import os

    from ..llm_judge import get_judge_if_headless
    from ..pipeline.stage2_scraping import run as stage2_run
    from ..pipeline.state import StageName

    allow_missing_sources = getattr(args, "allow_missing_sources", False)
    if getattr(args, "manual_bookup_login", False):
        os.environ["RVV_BOOKUP_MANUAL_LOGIN"] = "1"
    timeout = getattr(args, "manual_bookup_login_timeout", None)
    if timeout is not None:
        os.environ["RVV_BOOKUP_MANUAL_LOGIN_TIMEOUT"] = str(timeout)

    if resume_from <= 2:
        _console.print("[bold]Stage 2:[/bold] Skraping...")
        if getattr(args, "manual_bookup_login", False):
            _console.print(
                "  [cyan]ℹ[/cyan] BookUp manuell innlogging er aktiv — "
                "fullfør Vipps/SMS i nettleseren når den åpnes."
            )
        if getattr(args, "force_refresh", False):
            try:
                _force_refresh_stage2_inputs(args.work_dir)
                _console.print("  [green]✓[/green] Cache tvangsoppdatert før Stage 2")
                log_fn("Stage 2 inputs force-refreshed")
            except Exception as exc:
                _console.print(f"  [yellow]⚠[/yellow] Cache-refresh feilet: {exc}")
                log_fn(f"Stage 2 force-refresh warning: {exc}")
        try:
            scraping = stage2_run(
                cfg,
                state,
                start,
                end,
                strict=strict,
                allow_missing_sources=allow_missing_sources,
            )
            n = len(scraping.get("sources", []))
            blocked = scraping.get("blocked", [])
            if scraping.get("skipped"):
                _console.print(f"  [green]✓[/green] Skraping hoppet over — {scraping.get('skip_reason', 'ingen lag registrert')}")
                log_fn(f"Stage 2 skipped: {scraping.get('skip_reason', 'no registered teams')}")
            else:
                _console.print(f"  [green]✓[/green] {n} kilder skannet, {len(blocked)} blokkert")
                log_fn(f"Stage 2 OK: {n} sources scanned, {len(blocked)} blocked")
            if blocked:
                for blocked_name in blocked:
                    _console.print(f"    [yellow]⚠[/yellow] {blocked_name}")
                    log_fn(f"  Blocked: {blocked_name}")
                if scraping.get("warning"):
                    _console.print(f"  [dim]{scraping['warning']}[/dim]")
                if allow_missing_sources:
                    _console.print("  [green]✓[/green] Delvise resultater er lagret og pipeline fortsetter med godkjente mangler.")
                else:
                    _console.print("  [dim]Delvise resultater er lagret; kjør [bold]rvv-miniputt run --allow-missing-sources[/bold] for å fortsette med slike mangler neste gang.[/dim]")
            stage2_summary = {
                "sources_scanned": n,
                "blocked": blocked,
            }
            if not _judge_stage(2, stage2_summary, state, log_fn, stage_name=StageName.SCRAPING):
                return None, True, False
            # Deterministic checkpoint inspection — runs regardless of judge backend.
            try:
                _harness_active = get_judge_if_headless() is None
            except ValueError:
                _harness_active = True  # no backend configured — treat as harness
            if not _check_stage2_checkpoint(
                scraping, strict, _console, log_fn, harness_active=_harness_active
            ):
                return None, True, False
        except Exception as exc:
            _console.print(f"  [red]✗[/red] {exc}")
            log_fn(f"Stage 2 FAILED: {exc}")
            scraping = state.read_stage(StageName.SCRAPING) or {"sources": [], "blocked": []}
            if scraping.get("warning"):
                _console.print(f"  [dim]{scraping['warning']}[/dim]")
            if strict:
                return None, True, False
            _console.print("  [yellow]⚠[/yellow] Fortsetter pga --non-strict")
            return scraping, False, True
    else:
        scraping = state.read_stage(StageName.SCRAPING)
        if not scraping:
            _console.print("[red]✗[/red] Kan ikke gjenoppta: Stage 2-checkpoint mangler.")
            return None, True, False
        _console.print("[bold]Stage 2:[/bold] Hoppet over (gjenopptatt)")
        log_fn("Stage 2 skipped via --resume-from")

    return scraping, False, False


def _run_stage4_export(
    args: "argparse.Namespace",
    plan: "dict[str, Any]",
    state: "Any",
    strict: bool,
    log_fn: "Any",
    resume_from: int,
) -> "tuple[bool, bool, bool]":
    """Run Stage 4 (export) or skip it when resuming from a later stage.

    Returns ``(generated_calendars, abort, stage_failed)`` where
    *generated_calendars* is True when the export produced a calendars_html
    file, *abort* is True when the pipeline should stop (caller writes the
    run log and returns 1), and *stage_failed* is True when the stage failed
    in non-strict mode.
    """
    from ..pipeline.stage4_export import run as stage4_run

    if resume_from <= 4:
        _console.print("[bold]Stage 4:[/bold] Eksport...")
        try:
            export = stage4_run(
                plan,
                state,
                export_dir=args.export_dir,
                strict=strict,
                timestamped_export=getattr(args, "timestamped_export", True),
            )
            files = export.get("output_files", {})
            generated_calendars = "calendars_html" in files
            _console.print(f"  [green]✓[/green] {len(files)} fil(er) eksportert")
            for label, file_path in files.items():
                _console.print(f"    → {file_path}")
            log_fn(f"Stage 4 OK: {len(files)} files exported")
            return generated_calendars, False, False
        except Exception as exc:
            _console.print(f"  [red]✗[/red] {exc}")
            log_fn(f"Stage 4 FAILED: {exc}")
            if strict:
                return False, True, False
            _console.print("  [yellow]⚠[/yellow] Fortsetter pga --non-strict")
            return False, False, True
    else:
        _console.print("[bold]Stage 4:[/bold] Hoppet over (gjenopptatt)")
        log_fn("Stage 4 skipped via --resume-from")
        return False, False, False


def _regenerate_calendar(
    args: "argparse.Namespace",
    log_fn: "Any",
) -> bool:
    """Regenerate calendars.html from scrape cache when Stage 4 did not produce it.

    Returns True if calendar generation failed (caller should set run_failed).
    """
    from ..pipeline.calendar_viewer import generate_html as generate_calendars

    _console.print("Genererer calendars.html...", end=" ")
    try:
        path = generate_calendars(work_dir=args.work_dir, export_dir=args.export_dir)
        _console.print(f"[green]✓[/green] {path}")
        log_fn(f"calendars.html generated: {path}")
        return False
    except Exception as exc:
        _console.print(f"[red]✗[/red] {exc}")
        log_fn(f"calendars.html FAILED: {exc}")
        return True


def _fairness_penalty_hints_from_checkpoint(plan_checkpoint: "dict[str, Any]") -> dict[str, float]:
    """Extract planner penalty hints from a Stage 3 checkpoint's fairness data."""
    hints: dict[str, float] = {}
    plan_obj = _extract_plan_obj(plan_checkpoint)
    gate = _fairness_gate(plan_obj)

    try:
        metrics = gate.get("metrics", []) if isinstance(gate, dict) else []
        for metric in metrics or []:
            if not isinstance(metric, dict):
                continue
            key = str(metric.get("key", ""))
            status = str(metric.get("status", "pass")).lower()
            if key and status != "pass":
                hints[f"{key}_score"] = float(metric.get("score", 100) or 0)
    except Exception:
        pass

    for key in ("pairwise_matchup_score", "diversity_score", "month_balance_score"):
        score = _score_attr(plan_obj, key, default=1.0)
        if score < 0.75:
            hints[key] = score * 100.0

    return hints


def _build_mid_planning_critic_hints(
    plan_checkpoint: "dict[str, Any]",
    iteration: int,
    log_fn: "Any",
) -> dict[str, Any]:
    """Inspect a Stage 3 checkpoint and return structured hints for a rerun."""
    from .plan_critic import generate_critic_summary

    plan_obj = _extract_plan_obj(plan_checkpoint)
    issues: list[str] = []
    if plan_obj:
        try:
            issues = list(generate_critic_summary(plan_obj) or [])
        except Exception as exc:
            log_fn(f"Mid-planning critic {iteration}: generate_critic_summary failed: {exc}")

    penalty_hints = _fairness_penalty_hints_from_checkpoint(plan_checkpoint)
    tone = _compute_verdict_tone(plan_checkpoint)
    quality = _plan_attempt_quality(plan_checkpoint) if plan_checkpoint else {}

    return {
        "source": "mid_planning_critic",
        "iteration": iteration,
        "tone": tone,
        "issues": issues,
        "penalty_hints": penalty_hints,
        "quality": quality,
    }


def _run_stage3(
    args: "argparse.Namespace",
    cfg: "dict[str, Any]",
    scraping: "dict[str, Any]",
    state: "Any",
    start: "Any",
    end: "Any",
    strict: bool,
    resume_from: int,
    log_fn: "Any",
    iterations: int | None = None,
    penalty_hints: "dict[str, float] | None" = None,
    planning_critic_hints: "dict[str, Any] | None" = None,
) -> "tuple[dict[str, Any] | None, bool, bool]":
    """Run Stage 3 (planning) or skip it when resuming from a later stage.

    Returns ``(plan, abort, run_failed)`` where *plan* is the planning checkpoint
    dict, *abort* is True when the pipeline should stop (caller writes the run log
    and returns 1), and *run_failed* is True when the stage failed in non-strict
    mode (pipeline continues but ``run_failed`` should be set).

    When *penalty_hints* is provided, it is injected into the config dict under the
    key ``"penalty_hints"`` before calling the planner, so failed fairness metrics
    from a previous attempt can relax thresholds in the next attempt — but only
    when no headless judge is configured (issue #260 Phase 4: "remove
    penalty_hints threshold relaxation from the canonical decision-driven
    path"). When a judge *is* configured, ``allow_penalty_hint_relaxation``
    is set to False in the merged config, which disables both this initial
    hint handoff's effect and Stage 3's own internal per-seed feed-forward
    (see ``stage3_planning.run``/``SeasonPlanner``) — the canonical path
    should see the scorecard and choose a search/optimization action itself
    rather than have Python quietly lower acceptance thresholds behind it.
    """
    from ..llm_judge import get_judge_if_headless
    from ..pipeline.stage3_planning import run as stage3_run
    from ..pipeline.state import StageName

    try:
        judge_configured = get_judge_if_headless() is not None
    except ValueError:
        judge_configured = False

    if resume_from <= 3:
        _console.print("[bold]Stage 3:[/bold] Sesongplanlegging...")
        try:
            # Inject penalty hints from a previous failed attempt into config
            merged_cfg = dict(cfg)
            merged_cfg["allow_penalty_hint_relaxation"] = not judge_configured
            if judge_configured:
                log_fn("Stage 3: penalty-hint threshold relaxation disabled (headless judge configured)")
            if penalty_hints:
                merged_cfg["penalty_hints"] = dict(penalty_hints)
                hint_display = ", ".join(f"{k}={v}" for k, v in penalty_hints.items())
                _console.print(f"  [dim]Straffetips: {hint_display}[/dim]")
                log_fn(f"Stage 3 penalty hints injected: {hint_display}")
            if planning_critic_hints:
                merged_cfg["planning_critic_hints"] = dict(planning_critic_hints)
                log_fn(
                    "Stage 3 planning critic metadata injected: "
                    f"source={planning_critic_hints.get('source', 'unknown')} "
                    f"iteration={planning_critic_hints.get('iteration', '?')}"
                )
            plan = stage3_run(merged_cfg, scraping, state, start, end, strict=strict, iterations=iterations or getattr(args, "iterations", 1))
            n_tournaments = len(plan.get("plan", {}).get("tournaments", []))
            _console.print(f"  [green]✓[/green] {n_tournaments} turneringer planlagt")
            log_fn(f"Stage 3 OK: {n_tournaments} tournaments planned")
            stage3_summary = {
                "tournaments_planned": n_tournaments,
                "warnings": plan.get("warnings", []),
            }
            if not _judge_stage(3, stage3_summary, state, log_fn, stage_name=StageName.PLANNING):
                return None, True, False
        except Exception as exc:
            _console.print(f"  [red]✗[/red] {exc}")
            log_fn(f"Stage 3 FAILED: {exc}")
            if strict:
                return None, True, False
            _console.print("  [yellow]⚠[/yellow] Fortsetter pga --non-strict")
            plan = state.read_stage(StageName.PLANNING) or {}
            return plan, False, True
    else:
        plan = state.read_stage(StageName.PLANNING)
        if not plan:
            _console.print("[red]✗[/red] Kan ikke gjenoppta: Stage 3-checkpoint mangler.")
            return None, True, False
        _console.print("[bold]Stage 3:[/bold] Hoppet over (gjenopptatt)")
        log_fn("Stage 3 skipped via --resume-from")

    return plan, False, False


def _mid_planning_decision_problem(
    cfg: "dict[str, Any]", scraping: "dict[str, Any]", start: "Any", end: "Any"
) -> "dict[str, Any] | None":
    """Best-effort ``planning_problem`` for mid-planning critic decisions.

    Returns ``None`` (rather than raising) when it can't be built from *cfg*/
    *scraping* — the A/B report and decision prompt both degrade gracefully
    to self-consistency-only verification in that case
    (``planning_contract.verify_candidate``), matching how ``plan ab``
    already treats a missing ``--problem``.
    """
    try:
        from ..planning_contract import build_planning_problem

        return build_planning_problem(cfg, scraping, start.date(), end.date())
    except Exception:
        return None


def _plan_attempt_quality_adopts(best_plan: "dict[str, Any]", rerun_plan: "dict[str, Any]") -> bool:
    """Legacy deterministic composite-quality rank comparison.

    The pre-#260 behavior of ``_decide_plan_adoption``'s callers: adopt
    *rerun_plan* only if its :func:`_plan_attempt_quality` rank is strictly
    higher than *best_plan*'s. Kept as an explicitly-named, standalone
    legacy compatibility path — callers use it only when
    :func:`_decide_plan_adoption` returns ``"no_judge"``, never as a
    fallback for a configured judge that failed or declined (issue #260
    Phase 4: "fix the remaining unsafe decision fallbacks").
    """
    best_quality = _plan_attempt_quality(best_plan)
    rerun_quality = _plan_attempt_quality(rerun_plan)
    return rerun_quality["rank"] > best_quality["rank"]


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
    from ..application.decisions import decide, record_llm_decision
    from ..llm_judge import (
        build_action_decision_prompt,
        get_judge_if_headless,
        parse_action_verdict,
    )
    from ..planning_contract import extract_candidate
    from ..stage3_ab import build_ab_report
    from ..stage3_decision import build_stage3_decision_context

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
        from ..pipeline.run_manifest import RunManifest

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
            from ..pipeline.state import StageName, StageStatus

            state.write_stage(StageName.PLANNING, best_plan, status=StageStatus.DONE)
            log_fn("Mid-planning critic: checkpoint reset to best pre-export plan")
        except Exception as exc:
            log_fn(f"Mid-planning critic: could not persist best plan: {exc}")

    return best_plan, False, run_failed


def _run_refinement_and_reexport(
    args: "argparse.Namespace",
    plan: "dict[str, Any]",
    state: "Any",
    strict: bool,
    log_fn: "Any",
    resume_from: int,
) -> "tuple[dict[str, Any], bool, bool]":
    """Run the skill-driven refinement loop and optional Stage 4 re-export.

    Only runs when the plan verdict tone is 'rough'.  Failures here do not
    abort the pipeline.

    Returns ``(plan, generated_calendars, stage_failed)`` where *plan* is the
    (possibly refined) plan dict, *generated_calendars* is True when the
    re-export produced a calendars_html file, and *stage_failed* is always
    False (refinement is best-effort).
    """
    from ..pipeline.stage4_export import run as stage4_run

    generated_calendars = False
    try:
        plan_payload = _extract_plan_obj(plan)
        not_started = isinstance(plan, dict) and (
            plan.get("not_started") or (
                isinstance(plan_payload, dict) and plan_payload.get("placeholder") == "not_started"
            )
        )
        if not_started:
            log_fn("Post-Stage4 refinement skipped: no registered teams")
            return plan, generated_calendars, False

        initial_tone = _compute_verdict_tone(plan)
        log_fn(f"Post-Stage4 verdict tone: {initial_tone}")
        if initial_tone == "rough":
            _console.print(
                "\n[bold cyan]Plankvalitet: ROUGH — starter automatisk refinering...[/bold cyan]"
            )
            final_tone, refined_plan = _run_refinement_loop(
                plan, state, args, strict, log_fn
            )
            log_fn(f"Refinement loop complete: final tone={final_tone}")
            tone_label = {"strong": "SOLID", "mixed": "OK", "rough": "ROUGH"}.get(final_tone, final_tone.upper())
            if final_tone != "rough":
                _console.print(
                    f"  [green]✓[/green] Plankvalitet etter refinering: {tone_label} — re-eksporterer..."
                )
                # Re-run Stage 4 to export with the improved plan
                try:
                    export2 = stage4_run(
                        refined_plan,
                        state,
                        export_dir=args.export_dir,
                        strict=strict,
                        timestamped_export=getattr(args, "timestamped_export", True),
                    )
                    files2 = export2.get("output_files", {})
                    generated_calendars = "calendars_html" in files2
                    _console.print(f"  [green]✓[/green] Re-eksport: {len(files2)} fil(er)")
                    log_fn(f"Post-refinement Stage 4 re-export OK: {len(files2)} files")
                    plan = refined_plan
                except Exception as exc:
                    _console.print(f"  [yellow]⚠[/yellow] Re-eksport feilet: {exc}")
                    log_fn(f"Post-refinement Stage 4 re-export FAILED: {exc}")
            else:
                _console.print(
                    f"  [yellow]⚠[/yellow] Plankvalitet fremdeles ROUGH etter {_MAX_REFINEMENT_ITERATIONS} forsøk"
                )
        else:
            tone_label = {"strong": "SOLID", "mixed": "OK"}.get(initial_tone, initial_tone.upper())
            _console.print(f"\n  [dim]Plankvalitet: {tone_label} — ingen refinering nødvendig[/dim]")
    except Exception as exc:
        _console.print(f"  [yellow]⚠[/yellow] Refinering feilet uventet: {exc}")
        log_fn(f"Refinement loop unexpected error: {exc}")

    return plan, generated_calendars, False


# stage-number -> (StageName, DecisionContext stage key) for the interactive
# stage-by-stage mode. build_decision_context accepts either "config"/"stage1"
# etc. — pick the readable form.
_INTERACTIVE_STAGE_KEYS = {1: "config", 2: "scraping", 3: "planning", 4: "export"}


def _decision_summary_for_checkpoint(
    stage_num: int, checkpoint: dict[str, Any], *, effective_config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build the ``checkpoint_summary`` dict ``build_decision_context`` expects,
    directly from a persisted stage checkpoint (issue #260 Phase 5's
    interactive stage mode reads checkpoints after the fact, rather than the
    in-flight local summaries ``_run_stageN`` build for the headless judge).

    Stage 1's ``sources``/``start_date``/``end_date`` are intentionally
    *not* stored in its checkpoint — they live in ``input.xlsx`` and are
    merged in dynamically by ``load_effective_config`` at runtime (see
    ``_run_stage1``) — so callers must pass the already-loaded
    *effective_config* for stage 1 rather than relying on *checkpoint*
    alone, or these facts silently read as empty/"?".
    """
    if stage_num == 1:
        cfg = effective_config or checkpoint
        return {
            "sources": len(cfg.get("sources", [])),
            "start_date": cfg.get("start_date", "?"),
            "end_date": cfg.get("end_date", "?"),
            "age_groups": cfg.get("age_groups", []),
            "clubs": cfg.get("clubs", []),
        }
    if stage_num == 2:
        return {
            "sources_scanned": len(checkpoint.get("sources", [])),
            "blocked": checkpoint.get("blocked", []),
        }
    if stage_num == 3:
        plan_obj = checkpoint.get("plan", {})
        tournaments = plan_obj.get("tournaments", []) if isinstance(plan_obj, dict) else []
        return {
            "tournaments_planned": len(tournaments),
            "warnings": checkpoint.get("warnings", []),
            "tone": _compute_verdict_tone(checkpoint),
        }
    # stage 4 / export
    return {
        "files_written": list((checkpoint.get("output_files") or {}).keys()),
        "errors": checkpoint.get("errors", []),
    }


def _emit_interactive_decision_context(
    stage_num: int, state: "Any", work_dir: str, *, input_path: str | None = None
) -> int:
    """Build, persist and print the :class:`DecisionContext` for the stage
    that was just completed, then return the process exit code (always 2 —
    "paused for decision" — distinct from 0/success and 1/hard failure, so a
    caller script can branch on it without parsing output)."""
    import json as _json

    from ..llm_judge.prompts import build_decision_context
    from ..pipeline.state import StageName

    stage_name = list(StageName)[stage_num - 1]
    checkpoint = state.read_stage(stage_name) or {}
    effective_config = None
    if stage_num == 1:
        from ..pipeline.stage1_config import load_effective_config

        effective_config = load_effective_config(state, input_path=input_path)
    summary = _decision_summary_for_checkpoint(stage_num, checkpoint, effective_config=effective_config)
    context = build_decision_context(_INTERACTIVE_STAGE_KEYS[stage_num], summary)
    payload = context.to_dict()

    try:
        from ..pipeline.run_log_paths import resolve_active_run_log_dir

        log_dir = resolve_active_run_log_dir(work_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "decision_context.json", "w", encoding="utf-8") as fh:
            _json.dump(payload, fh, indent=2, ensure_ascii=False)
    except Exception:
        pass  # best-effort audit copy; stdout below is authoritative

    print(_json.dumps(payload, indent=2, ensure_ascii=False))
    return 2


def _cmd_run_interactive(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt run --interactive`` (issue #260 Phase 5).

    Runs exactly one stage (the stage at ``--resume-from``, default 1) using
    the same ``_run_stageN`` helpers ``_cmd_run`` uses, then emits a
    :class:`~tournament_scheduler.application.decisions.DecisionContext` for
    that stage as JSON on stdout and exits 2 — never advancing to the next
    stage on its own. This is the canonical capability a thin harness
    adapter (Claude/ChatGPT/OpenCode/Codex/Pi) calls between checkpoints
    instead of invoking ``stageN_*`` modules directly and hand-rolling
    recovery/refinement policy in prose (see
    ``.claude/commands/rvv-miniputt/run.md``).

    Pass ``--decision-action``/``--decision-action-file`` (a JSON
    :class:`DecisionAction`) on the *next* invocation to validate and record
    a decision for the stage just emitted before advancing:

    - ``proceed`` (or any action not listed below): the target stage runs.
    - ``abort``: the run stops here, exit 1, target stage does not run.
    - ``retry_stage``: the *previous* stage re-runs instead of the target
      (e.g. after ``--force-refresh`` for a fresh scrape).
    - ``recover_source``: advisory only — the adapter is expected to have
      already called ``recovery-inject`` for the named source before
      retrying; this action just gets recorded, then the target stage runs.

    An invalid/not-offered action, or a hard-violation/human-approval
    conflict, is rejected deterministically by
    :func:`~tournament_scheduler.application.decisions.decide` — the same
    validator ``_judge_stage`` uses — before anything runs.

    Single-attempt only: unlike ``rvv-miniputt run``, this does not run
    Stage 3's multi-seed best-of-N retry loop or the post-Stage4 tone-gated
    refinement pass. Once a plan looks good enough to finalize, either
    accept it as-is or invoke the non-interactive ``run --resume-from 3``
    for the full retry/refinement machinery.
    """
    import json as _json

    from ..application.decisions import DecisionAction, decide, record_llm_decision
    from ..llm_judge.prompts import build_decision_context
    from ..pipeline.state import PipelineState, StageName

    strict = not args.non_strict
    resume_from = _resolve_resume_stage(getattr(args, "resume_from", None))
    state = PipelineState(args.work_dir)

    log_lines: list[str] = []

    def _log(msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        log_lines.append(f"[{ts}] {msg}")

    decision_payload: dict[str, Any] | None = None
    if getattr(args, "decision_action", None):
        try:
            decision_payload = _json.loads(args.decision_action)
        except _json.JSONDecodeError as exc:
            _console.print(f"[red]✗[/red] Ugyldig --decision-action JSON: {exc}")
            return 1
    elif getattr(args, "decision_action_file", None):
        try:
            with open(args.decision_action_file, "r", encoding="utf-8") as fh:
                decision_payload = _json.load(fh)
        except (OSError, _json.JSONDecodeError) as exc:
            _console.print(f"[red]✗[/red] Kunne ikke lese --decision-action-file: {exc}")
            return 1

    if decision_payload is not None:
        prev_stage_num = resume_from - 1
        if prev_stage_num < 1:
            _console.print(
                "[red]✗[/red] --decision-action krever --resume-from > 1 "
                "(ingen forrige stage å avgjøre)."
            )
            return 1
        prev_stage_name = list(StageName)[prev_stage_num - 1]
        if not state.checkpoint_path(prev_stage_name).exists():
            _console.print(f"[red]✗[/red] Fant ingen sjekkpunkt for Stage {prev_stage_num} å avgjøre.")
            return 1
        prev_checkpoint = state.read_stage(prev_stage_name)
        prev_effective_config = None
        if prev_stage_num == 1:
            from ..pipeline.stage1_config import load_effective_config

            prev_effective_config = load_effective_config(state, input_path=args.input)
        prev_summary = _decision_summary_for_checkpoint(
            prev_stage_num, prev_checkpoint, effective_config=prev_effective_config
        )
        prev_context = build_decision_context(_INTERACTIVE_STAGE_KEYS[prev_stage_num], prev_summary)
        try:
            decision_action = DecisionAction.from_dict(decision_payload)
        except Exception as exc:
            _console.print(f"[red]✗[/red] Ugyldig DecisionAction: {exc}")
            return 1
        decision_result = decide(prev_context, decision_action)
        try:
            record_llm_decision(str(state.work_dir), prev_context, decision_action, decision_result)
        except Exception as exc:
            _log(f"record_llm_decision failed: {exc}")
        if not decision_result.accepted:
            _console.print(
                f"[red]✗[/red] Avgjørelse avvist: {decision_result.rejection_reason}"
            )
            return 1
        if decision_action.action_id == "abort":
            _console.print("[yellow]Avbrutt etter operatørens avgjørelse.[/yellow]")
            return 1
        if decision_action.action_id == "retry_stage":
            resume_from = prev_stage_num

    cfg, abort = _run_stage1(args, state, strict, _log, resume_from)
    if abort:
        return 1
    if resume_from == 1:
        return _emit_interactive_decision_context(1, state, args.work_dir, input_path=args.input)

    start = datetime.strptime(cfg["start_date"], "%Y-%m-%d")
    end = datetime.strptime(cfg["end_date"], "%Y-%m-%d")

    scraping, abort, _stage2_failed = _run_stage2(args, cfg, state, start, end, strict, _log, resume_from)
    if abort:
        return 1
    if resume_from == 2:
        return _emit_interactive_decision_context(2, state, args.work_dir)

    plan, abort, _stage3_failed = _run_stage3(args, cfg, scraping, state, start, end, strict, resume_from, _log)
    if abort:
        return 1
    if resume_from == 3:
        return _emit_interactive_decision_context(3, state, args.work_dir)

    _generated_calendars, abort, _stage4_failed = _run_stage4_export(
        args, plan, state, strict, _log, resume_from
    )
    if abort:
        return 1
    return _emit_interactive_decision_context(4, state, args.work_dir)


def _cmd_run(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt run`` — full pipeline stages 1→4 + HTML."""
    if getattr(args, "interactive", False):
        return _cmd_run_interactive(args)

    from ..pipeline.state import PipelineState

    strict = not args.non_strict
    resume_from = _resolve_resume_stage(getattr(args, "resume_from", None))
    state = PipelineState(args.work_dir)

    log_start = datetime.now()
    log_lines: list[str] = []
    run_failed = False

    def _log(msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        log_lines.append(f"[{ts}] {msg}")

    _console.print("[bold]🏒 RVV Miniputt — full pipeline[/bold]\n")
    _log(
        f"Pipeline started (work_dir={args.work_dir}, input={args.input}, strict={strict}, "
        f"resume_from={resume_from}, log_level={getattr(args, 'log_level', 'info')})"
    )
    if resume_from > 1:
        _console.print(f"[dim]Gjenopptar fra Stage {resume_from}[/dim]")

    _manifest_start_run(args.work_dir, args.input, getattr(args, "objective", None))

    plan: dict[str, Any] | None = None

    _manifest_set_active(args.work_dir, "config")
    cfg, abort = _run_stage1(args, state, strict, _log, resume_from)
    if abort:
        _manifest_record(args.work_dir, "config", "failed", "Stage 1 (config) failed or aborted the run.")
        _manifest_finalize(args.work_dir, "failed")
        _write_run_log(args, state, log_start, log_lines, success=False)
        return 1
    _manifest_record(
        args.work_dir,
        "config",
        "ok",
        f"{len(cfg.get('sources', []))} source(s) configured, "
        f"{cfg.get('start_date', '?')} → {cfg.get('end_date', '?')}",
    )

    start = datetime.strptime(cfg["start_date"], "%Y-%m-%d")
    end = datetime.strptime(cfg["end_date"], "%Y-%m-%d")

    _manifest_set_active(args.work_dir, "scraping")
    scraping, abort, stage2_failed = _run_stage2(args, cfg, state, start, end, strict, _log, resume_from)
    if abort:
        _manifest_record(args.work_dir, "scraping", "failed", "Stage 2 (scraping) failed or aborted the run.")
        _manifest_finalize(args.work_dir, "failed")
        _write_run_log(args, state, log_start, log_lines, success=False)
        return 1
    if stage2_failed:
        run_failed = True
    _scraping_blocked = (scraping or {}).get("blocked", [])
    _health_problems: list[str] = []
    _health_actions: list[str] = []
    _health_requires_human = bool(_scraping_blocked)
    try:
        from ..pipeline.source_health import compute_source_health

        for _health_result in compute_source_health(args.work_dir):
            if _health_result.status == "ok":
                continue
            _health_problems.extend(_health_result.problems)
            _health_actions.extend(_health_result.suggested_actions)
            _health_requires_human = _health_requires_human or _health_result.requires_human
    except Exception:
        pass
    _manifest_record(
        args.work_dir,
        "scraping",
        "failed" if stage2_failed else ("warning" if (_scraping_blocked or _health_problems) else "ok"),
        f"{len((scraping or {}).get('sources', []))} source(s) scraped, {len(_scraping_blocked)} blocked",
        problems=list(_scraping_blocked) + _health_problems,
        suggested_actions=_health_actions,
        requires_human=_health_requires_human,
    )

    # Retry Stage 3 planning when the verdict is still rough and we actually
    # have a populated tournament plan to improve. This gives the planner a
    # larger search budget before we export anything.
    # Also feeds fairness gate scores from each failed attempt back as penalty
    # hints into the next attempt, so the planner can relax thresholds for
    # metrics that failed.
    max_plan_attempts = 3
    base_iterations = max(1, int(getattr(args, "iterations", 1) or 1))
    final_tone = "rough"
    final_tournament_count = 0
    plan_needs_attention = False
    best_plan: "dict[str, Any] | None" = None
    best_quality: "dict[str, Any] | None" = None
    best_attempt: int = 0
    last_attempt: int = 0
    attempt_qualities: list[tuple[int, dict[str, Any]]] = []
    multi_seed_problem = _mid_planning_decision_problem(cfg, scraping, start, end)
    try:
        from ..pipeline.run_manifest import RunManifest

        multi_seed_run_id = str(RunManifest(state.work_dir).read().get("run_id") or "")
    except Exception:
        multi_seed_run_id = ""

    _manifest_set_active(args.work_dir, "planning")
    for attempt in range(1, max_plan_attempts + 1):
        last_attempt = attempt
        attempt_iterations = base_iterations + attempt - 1
        penalty_hints: "dict[str, float]" = {}
        if attempt > 1 and plan is not None:
            # Build penalty hints from fairness gate metrics of the previous attempt
            try:
                fg = (plan.get("plan", {}) or {}).get("fairness_gate", {}) or {}
                metrics: list = fg.get("metrics", []) or []
                for m in metrics:
                    if isinstance(m, dict):
                        key = m.get("key", "")
                        status = m.get("status", "pass")
                        score = m.get("score", 100)
                        if status != "pass":
                            penalty_hints[f"{key}_score"] = float(score)
            except Exception:
                pass
            if penalty_hints:
                hint_str = ", ".join(f"{k}={v}" for k, v in penalty_hints.items())
                _console.print(
                    f"  [dim]Forrige forsøk: straffetips {hint_str}[/dim]"
                )
                _log(f"Penalty hints from attempt {attempt-1}: {hint_str}")
        if attempt > 1:
            _console.print(
                f"[dim]Nytt planforsøk {attempt}/{max_plan_attempts} "
                f"(søk={attempt_iterations})[/dim]"
            )
        plan, abort, stage3_failed = _run_stage3(
            args, cfg, scraping, state, start, end, strict, resume_from,
            _log, attempt_iterations, penalty_hints,
        )
        if abort:
            _manifest_record(args.work_dir, "planning", "failed", "Stage 3 (planning) failed or aborted the run.")
            _manifest_finalize(args.work_dir, "failed")
            _write_run_log(args, state, log_start, log_lines, success=False)
            return 1
        if stage3_failed:
            run_failed = True

        final_tournament_count = len((plan or {}).get("plan", {}).get("tournaments", []))
        final_tone = _compute_verdict_tone(plan or {})

        # Track best plan across attempts. The first attempt with a plan
        # always becomes the initial best (nothing to compare it to yet);
        # every later attempt is an apply_candidate/keep_baseline decision
        # against the current best, routed through the same headless-judge
        # decision path as the mid-planning critic loop
        # (_decide_plan_adoption, issue #260 Phase 4) instead of a bare
        # Python composite-quality rank comparison deciding alone.
        attempt_quality: dict[str, Any] | None = None
        if plan is not None:
            attempt_quality = _plan_attempt_quality(plan)
            attempt_qualities.append((attempt, attempt_quality))
            if best_plan is None:
                adopt = True
            else:
                adoption_outcome, _adoption_reason = _decide_plan_adoption(
                    best_plan,
                    plan,
                    multi_seed_problem,
                    run_id=multi_seed_run_id,
                    iteration=attempt,
                    work_dir=str(state.work_dir),
                    log_fn=_log,
                    label="stage3_multi_seed",
                )
                if adoption_outcome == "no_judge":
                    # Explicitly-named legacy compatibility path (issue #260
                    # Phase 4): no judge configured at all, so fall back to
                    # the deterministic quality rank — never used for a
                    # configured judge that failed or declined.
                    adopt = _plan_attempt_quality_adopts(best_plan, plan)
                else:
                    adopt = adoption_outcome == "adopt"
            if adopt:
                best_quality = attempt_quality
                best_plan = plan
                best_attempt = attempt

        _log(
            f"Stage 3 attempt {attempt}/{max_plan_attempts}: tone={final_tone}, "
            f"tournaments={final_tournament_count}, iterations={attempt_iterations}, "
            f"quality={_format_plan_attempt_quality(attempt_quality) if attempt_quality else 'N/A'}"
        )

        if final_tone != "rough" or final_tournament_count == 0:
            break

        if attempt < max_plan_attempts:
            _console.print(
                "  [yellow]⚠[/yellow] Planen er fortsatt IKKE KLAR — "
                "kjører nytt planforsøk med straffetips fra forrige runde."
            )
            _log("Plan verdict still rough; retrying Stage 3 with penalty hints")

    # Use the best plan across all attempts, not just the last one.
    if best_plan is not None and best_quality is not None:
        selected_summary = _format_plan_attempt_quality(best_quality)
        all_summaries = "; ".join(
            f"attempt {num}: {_format_plan_attempt_quality(quality)}"
            for num, quality in attempt_qualities
        )
        _log(
            f"Selected Stage 3 attempt {best_attempt}/{last_attempt}: {selected_summary}. "
            f"Compared attempts: {all_summaries}"
        )
        if best_attempt != last_attempt:
            _console.print(
                f"  [green]✓[/green] Velger forsøk {best_attempt} "
                f"({selected_summary}) — best av {last_attempt} forsøkt"
            )
            plan = best_plan
            # Keep the planning checkpoint aligned with the plan that Stage 4
            # will export, otherwise later resume/refinement reads the losing
            # final attempt from disk.
            try:
                from ..pipeline.state import StageName, StageStatus

                state.write_stage(StageName.PLANNING, plan, status=StageStatus.DONE)
                _log(f"Stage 3 checkpoint reset to selected attempt {best_attempt}")
            except Exception as exc:
                _log(f"Could not persist selected Stage 3 attempt {best_attempt}: {exc}")
            # Recompute tone/count from the best plan.
            final_tone = _compute_verdict_tone(plan)
            final_tournament_count = len((plan or {}).get("plan", {}).get("tournaments", []))

    if plan is not None:
        plan, mid_abort, mid_failed = _run_mid_planning_critic_loop(
            args, cfg, scraping, state, start, end, strict, resume_from, _log, plan
        )
        if mid_abort:
            _manifest_record(args.work_dir, "planning", "failed", "Mid-planning critic loop aborted the run.")
            _manifest_finalize(args.work_dir, "failed")
            _write_run_log(args, state, log_start, log_lines, success=False)
            return 1
        if mid_failed:
            run_failed = True
        final_tone = _compute_verdict_tone(plan)
        final_tournament_count = len((plan or {}).get("plan", {}).get("tournaments", []))

    if final_tone == "rough" and final_tournament_count > 0:
        plan_needs_attention = True
        _console.print(
            f"  [red]✗[/red] Planen er fortsatt IKKE KLAR etter {max_plan_attempts} forsøk — eksport kan ikke regnes som godkjent."
        )
        _log(f"Planning remained rough after {max_plan_attempts} attempts")

    _manifest_record(
        args.work_dir,
        "planning",
        "failed" if run_failed and plan is None else ("warning" if plan_needs_attention else "ok"),
        f"{final_tournament_count} tournament(s) planned, verdict tone={final_tone}",
        confidence=1.0 if final_tone == "strong" else (0.6 if final_tone == "mixed" else 0.3),
        requires_human=plan_needs_attention,
    )

    # ── LLM approval gate (between Stage 3 and Stage 4) ──────────────────────
    # Only runs when RVV_APPROVAL_ENDPOINT is set (opt-in).  If not configured
    # the gate is skipped silently so non-LLM deployments are unaffected.
    if not _run_approval_gate(args, plan, state, strict, _console, _log):
        _manifest_record(args.work_dir, "planning", "blocked", "LLM approval gate rejected the plan.")
        _manifest_finalize(args.work_dir, "blocked")
        _write_run_log(args, state, log_start, log_lines, success=False)
        return 1

    _manifest_set_active(args.work_dir, "export")
    stage4_generated_calendars, abort, stage4_failed = _run_stage4_export(
        args, plan, state, strict, _log, resume_from
    )
    if abort:
        _manifest_record(args.work_dir, "export", "failed", "Stage 4 (export) failed or aborted the run.")
        _manifest_finalize(args.work_dir, "failed")
        _write_run_log(args, state, log_start, log_lines, success=False)
        return 1
    if stage4_failed:
        run_failed = True
    _manifest_record(
        args.work_dir,
        "export",
        "failed" if stage4_failed else "ok",
        "Export produced calendars_html output" if stage4_generated_calendars else "Export completed",
    )

    # ── Skill-driven refinement loop (post-Stage 4) ──────────────────────────
    # When the plan verdict tone is 'rough', attempt automated improvements by
    # applying critic-guided swap suggestions and re-running Stage 4 export.
    # This is a best-effort step — failures here do not abort the pipeline.
    if not run_failed:
        plan, refinement_calendars, _ = _run_refinement_and_reexport(args, plan, state, strict, _log, resume_from)
        if refinement_calendars:
            stage4_generated_calendars = refinement_calendars
        if plan_needs_attention and plan is not None:
            refined_tone = _compute_verdict_tone(plan)
            if refined_tone != "rough":
                plan_needs_attention = False
                final_tone = refined_tone
                _log(f"Refinement cleared rough verdict: tone={refined_tone}")

    # Only regenerate calendars.html here when stage4 did not already produce it
    # (e.g. stage4 was skipped via --resume-from, or no scrape data was available).
    if not stage4_generated_calendars:
        if _regenerate_calendar(args, _log):
            run_failed = True

    if run_failed or plan_needs_attention:
        _console.print("\n[bold yellow]⚠ Pipeline fullført med feil.[/bold yellow]")
        _log("Pipeline completed with failures")
        _manifest_finalize(args.work_dir, "failed" if run_failed else "warning")
    else:
        _console.print("\n[bold green]✓ Pipeline fullført.[/bold green]")
        _log("Pipeline completed successfully")
        _manifest_finalize(args.work_dir, "ok")
    _write_run_log(args, state, log_start, log_lines, success=not (run_failed or plan_needs_attention))
    return 1 if plan_needs_attention else 0


# ---------------------------------------------------------------------------
# Goal-oriented operator entry point
# ---------------------------------------------------------------------------


def _resolve_operator_resume_stage(state: "Any") -> "int | None":
    """Return the 1-based index of the earliest stage that needs (re)running.

    A stage needs running when it has no checkpoint yet, is not done, or was
    invalidated (stale) by an upstream change. Returns ``None`` when every
    stage is done and fresh — there is nothing pending for the operator to do.
    """
    from ..pipeline.state import StageName

    for stage in (StageName.CONFIG, StageName.SCRAPING, StageName.PLANNING, StageName.EXPORT):
        if not state.checkpoint_path(stage).exists():
            return stage.index
        if not state.is_done(stage) or state.is_stale(stage):
            return stage.index
    return None


def _raise_escalation_questions(work_dir: str) -> None:
    """Escalate every capability result that came back ``requires_human``.

    Best-effort and idempotent: raising the same question twice (same type,
    capability, and summary, in the same scope context) is a no-op, so this
    can safely run after every ``rvv-miniputt run`` invocation without ever
    re-asking something the human already answered, or duplicating a
    still-open question.

    Scoped to ``input_version`` (issue #12) when the run manifest has an
    input fingerprint: a blocked capability's cause is almost always a fact
    about *this* workbook's data (e.g. "0 turneringer mulig for U12"), so an
    answer recorded for one workbook must not be silently reused once the
    organizer uploads a different one — the old entry is marked stale
    instead, and the new workbook gets its own fresh escalation. Falls back
    to the durable ``workspace`` scope when no fingerprint is available
    (e.g. a legacy or synthesized manifest), matching pre-#12 behavior.
    """
    try:
        from ..pipeline.capability_result import CapabilityResult
        from ..pipeline.escalation import DecisionScope, from_capability_result, raise_question
        from ..pipeline.run_manifest import RunManifest

        manifest = RunManifest(work_dir).read()
        input_sha256 = (manifest.get("input_fingerprint") or {}).get("sha256")
        scope = DecisionScope.INPUT_VERSION.value if input_sha256 else DecisionScope.WORKSPACE.value
        scope_key = input_sha256 or ""

        for entry in manifest.get("capabilities") or []:
            if not entry.get("requires_human"):
                continue
            result = CapabilityResult.from_dict(entry)
            raise_question(work_dir, from_capability_result(result, scope=scope, scope_key=scope_key))
    except Exception as exc:
        _warn_manifest_failure(work_dir, "eskalere spørsmål til", exc)


def _print_operator_summary(work_dir: str) -> None:
    """Print the operator's final structured summary from the run manifest.

    Best-effort: the manifest is an operator-facing summary layered on top of
    the pipeline, so a failure to read it should never mask the pipeline's own
    exit code or console output.
    """
    try:
        from ..pipeline.run_manifest import RunManifest

        manifest = RunManifest(work_dir).read()
    except Exception:
        return

    outcome = str(manifest.get("final_outcome", "in_progress"))
    outcome_style = {"ok": "green", "warning": "yellow", "blocked": "yellow", "failed": "red"}.get(outcome, "white")
    icon_by_status = {"ok": "✓", "warning": "⚠", "blocked": "⛔", "failed": "✗"}
    style_by_status = {"ok": "green", "warning": "yellow", "blocked": "yellow", "failed": "red"}

    _console.print("\n[bold]Operator-sammendrag[/bold]")
    _console.print(f"  Mål:      {manifest.get('objective') or '-'}")
    _console.print(f"  Resultat: [{outcome_style}]{outcome.upper()}[/{outcome_style}]")

    action_log = manifest.get("action_log") or []
    if action_log:
        transition_icon = {"resolved": "✓", "retry": "↻", "escalate": "⛔", "no_progress_stop": "⏹"}
        transition_style = {"resolved": "green", "retry": "yellow", "escalate": "red", "no_progress_stop": "red"}
        _console.print(f"  Gjenopprettingsforsøk ({len(action_log)}):")
        for entry in action_log:
            transition = str(entry.get("transition", "?"))
            icon = transition_icon.get(transition, "?")
            style = transition_style.get(transition, "white")
            action_id = entry.get("action_id") or "(ingen handling)"
            _console.print(
                f"    [{style}]{icon}[/{style}] {entry.get('target', '?'):<12} {action_id:<20} {transition}"
            )

    capabilities = manifest.get("capabilities") or []
    if capabilities:
        _console.print("  Kapabiliteter:")
        for entry in capabilities:
            status = str(entry.get("status", "?"))
            icon = icon_by_status.get(status, "?")
            style = style_by_status.get(status, "white")
            name = str(entry.get("capability", "?"))
            _console.print(f"    [{style}]{icon}[/{style}] {name:<10} {entry.get('summary', '')}")
            for problem in entry.get("problems") or []:
                _console.print(f"        [dim]· {problem}[/dim]")
            if entry.get("requires_human"):
                for action in entry.get("suggested_actions") or []:
                    _console.print(f"        [cyan]→ {action}[/cyan]")

    unanswered = [q for q in manifest.get("pending_questions") or [] if not q.get("answered")]
    if unanswered:
        _console.print("\n  [bold yellow]Ubesvarte spørsmål:[/bold yellow]")
        for question in unanswered:
            _console.print(f"    [yellow]?[/yellow] ({question.get('type')}) {question.get('summary')}")
            if question.get("context"):
                _console.print(f"        [dim]Kontekst: {question['context']}[/dim]")
            if question.get("recommendation"):
                _console.print(f"        [cyan]Anbefaling: {question['recommendation']}[/cyan]")
            if question.get("impact"):
                _console.print(f"        [dim]Konsekvens: {question['impact']}[/dim]")
            for alt in question.get("alternatives") or []:
                _console.print(f"        [dim]· {alt}[/dim]")
            _console.print(
                f"        [dim]Svar med: rvv-miniputt operator answer {question.get('id')} \"<svar>\"[/dim]"
            )

    if outcome in ("blocked", "failed"):
        _console.print(
            "  [dim]Kjør 'rvv-miniputt status --json' for full detaljer, "
            "eller 'rvv-miniputt logs show' for siste kjøringslogg.[/dim]"
        )


def _run_recovery_loop(work_dir: str) -> "dict[str, Any] | None":
    """Best-effort observe-decide-act recovery pass (issue #11).

    Runs unconditionally at the top of ``operator run``: a no-op when
    Stage 2 hasn't produced a checkpoint yet or every source is already
    healthy. Never raises — a failure here should degrade to "the human
    sees the same blocked sources they would have anyway", not crash the
    operator entry point. A manifest persistence failure specifically is
    still surfaced as a visible warning (issue #14) rather than silently
    folded into "nothing to recover" — it's a materially different
    situation than the loop simply finding no unhealthy sources.
    """
    from ..pipeline.run_manifest import ManifestPersistenceError

    try:
        from ..pipeline.operator_loop import run_source_recovery_loop

        return run_source_recovery_loop(work_dir)
    except ManifestPersistenceError as exc:
        _warn_manifest_failure(work_dir, "registrere gjenopprettingshandlinger i", exc)
        return None
    except Exception:
        return None


def _cmd_operator_run(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator run`` — the goal-oriented AI operator entry point.

    A thin wrapper around ``rvv-miniputt run``, which already implements
    bounded retries, inter-stage judgment, and run-manifest bookkeeping: this
    resolves the active objective, runs a bounded observe-decide-act
    recovery pass over calendar source health (issue #11), auto-detects
    where to resume from unless the caller overrides it, skips work
    entirely when nothing is pending, and prints a final structured summary
    once the run completes. It does not duplicate any scheduling or
    recovery logic — the recovery pass only dispatches through the #10
    action registry, which itself calls the same stage/scraper code the
    portable CLI uses.
    """
    from ..pipeline.state import PipelineState

    args.objective = getattr(args, "objective", None) or _DEFAULT_OPERATOR_OBJECTIVE
    state = PipelineState(args.work_dir)

    recovery_summary = _run_recovery_loop(args.work_dir)
    recovered_any = bool(recovery_summary and recovery_summary.get("actions_taken"))

    explicit_resume = getattr(args, "resume_from", None)
    if getattr(args, "force", False):
        args.resume_from = "1"
    elif explicit_resume:
        args.resume_from = explicit_resume
    else:
        auto_stage = _resolve_operator_resume_stage(state)
        if recovered_any:
            # The recovery pass repaired the unified scrape cache, but only
            # an actual Stage 2 rerun rewrites the stage2_scraping.json
            # checkpoint to reflect that — force it back into the plan even
            # if auto-detection otherwise thought nothing was pending.
            auto_stage = 2 if auto_stage is None else min(auto_stage, 2)
        if auto_stage is None:
            _console.print("[bold]🏒 RVV Miniputt operator[/bold]\n")
            _console.print(f"[dim]Mål:[/dim] {args.objective}")
            _console.print(
                "[green]✓[/green] Alle stadier er allerede fullført og oppdaterte — ingenting å gjøre."
            )
            _console.print("[dim]Bruk --force for å kjøre pipelinen på nytt fra bunnen.[/dim]")
            _print_operator_summary(args.work_dir)
            return 0
        args.resume_from = str(auto_stage)

    rc = _cmd_run(args)
    _raise_escalation_questions(args.work_dir)
    _print_operator_summary(args.work_dir)

    if rc == 0 and getattr(args, "publish", False):
        publish_result = _execute_operator_publish(args)
        if publish_result is None:
            publish_rc = 1
        else:
            publish_rc = _print_pages_result(publish_result, as_json=getattr(args, "json", False))
            _append_publish_outcome_to_run_log(args.work_dir, state, publish_result)
        rc = rc if publish_rc == 0 else publish_rc

    return rc


def _execute_operator_publish(args: argparse.Namespace) -> "Any | None":
    """Build and execute the ``publish_pages`` action, recording it in the run manifest.

    Shared by ``_cmd_operator_publish`` (standalone ``operator publish``) and
    ``_cmd_operator_run`` (``operator run --publish``) so both paths build
    the exact same action from the exact same flags — see the module-level
    docstring note on ``op_run`` in ``cli/args.py`` (issue #32 follow-up):
    "run --publish" must behave identically to "run" followed by a separate
    "publish", not a distinct, weaker code path.

    Always invoked with ``approved=True`` at the :class:`ActionRegistry`
    level — running this command at all is the coarse consent to attempt an
    external-risk action (#10). That is deliberately *not* sufficient on
    its own to actually push to the Pages branch: ``_execute_publish_pages``
    additionally requires either ``--confirm-public`` on this exact
    invocation or a previously durable-answered approval for this exact
    bundle/target (issue #19).

    Returns the :class:`CapabilityResult` (whatever its status — the caller
    renders blocked/failed outcomes too), or ``None`` if the action registry
    itself raised before producing one (unknown action, missing approval, or
    persistence unavailable) — in which case this already printed the error.
    """
    from ..pipeline.operator_action import (
        DEFAULT_REGISTRY,
        ApprovalRequiredError,
        PersistenceUnavailableError,
        UnknownActionError,
    )
    from ..pipeline.run_manifest import ManifestPersistenceError, RunManifest

    action_kwargs: dict[str, Any] = {
        "work_dir": args.work_dir,
        "repo_dir": getattr(args, "repo_dir", ".") or ".",
        "branch": getattr(args, "branch", "gh-pages") or "gh-pages",
        "remote": getattr(args, "remote", "origin") or "origin",
        "push": getattr(args, "push", True),
        "confirm_public": getattr(args, "confirm_public", False),
        "dry_run": getattr(args, "dry_run", False),
        "verify": getattr(args, "verify", True),
    }
    # For `operator run --publish`, Stage 4 may write into a timestamped child of
    # args.export_dir (for example export/2026-07-29T1712).  Do not pass the
    # parent export root to the publish action; letting it resolve from the
    # Stage 4 checkpoint publishes the actual freshly exported bundle.  The
    # standalone `operator publish --export-dir ...` command still supports an
    # explicit override.
    if getattr(args, "operator_command", None) == "publish" and getattr(args, "export_dir", None):
        action_kwargs["export_dir"] = args.export_dir
    if getattr(args, "run_id", None):
        action_kwargs["run_id"] = args.run_id
    if getattr(args, "extra_public_files", None):
        from ..pipeline.pages_bundle import DEFAULT_ALLOWED_FILENAMES

        action_kwargs["allowed_filenames"] = DEFAULT_ALLOWED_FILENAMES | set(args.extra_public_files)
    if getattr(args, "allow_findings", None):
        action_kwargs["allow_findings"] = set(args.allow_findings)
    if getattr(args, "verify_max_attempts", None) is not None:
        action_kwargs["verify_max_attempts"] = args.verify_max_attempts
    if getattr(args, "verify_retry_delay_seconds", None) is not None:
        action_kwargs["verify_retry_delay_seconds"] = args.verify_retry_delay_seconds

    action = DEFAULT_REGISTRY.build("publish_pages", **action_kwargs)
    try:
        result = DEFAULT_REGISTRY.execute(action, approved=True)
    except (UnknownActionError, ApprovalRequiredError, PersistenceUnavailableError) as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return None

    try:
        RunManifest(args.work_dir).record_capability(result)
    except ManifestPersistenceError as exc:
        _warn_manifest_failure(args.work_dir, "registrere Pages-publisering i", exc)

    return result


def _append_publish_outcome_to_run_log(work_dir: str, state: "Any", result: "Any") -> None:
    """Append a run-triggered publish step's outcome to that run's own log file.

    ``_write_run_log`` (called inside ``_cmd_run``) already finishes and
    closes the pipeline run's log before ``operator run --publish`` gets a
    chance to publish — so without this, the publish step was completely
    invisible to the log the user checks after the fact, even though it ran
    in the very same invocation. "run --publish" is meant to be the same as
    "run" plus publishing, so its log should be the same run log with the
    publish outcome appended, not a separate silent action (issue #32
    follow-up).

    Best-effort: appended after the fact by locating the most recently
    written ``pipeline_run_*.log`` for this workspace, so a failure here
    (e.g. log dir missing) never affects the publish result itself.
    """
    try:
        log_dir = resolve_active_run_log_dir(state)
        candidates = sorted(
            log_dir.glob("pipeline_run_*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return
        log_path = candidates[0]
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n# Publish ({datetime.now().isoformat()})\n")
            handle.write(f"# Status: {result.status}\n")
            handle.write(f"{result.summary}\n")
            for artifact in result.artifacts:
                handle.write(f"  artifact: {artifact}\n")
            for problem in result.problems:
                handle.write(f"  problem: {problem}\n")
    except Exception:
        pass


def _cmd_operator_publish(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator publish`` — publish the exported plan to GitHub Pages (issue #17)."""
    result = _execute_operator_publish(args)
    if result is None:
        return 1
    return _print_pages_result(result, as_json=getattr(args, "json", False))


def _print_pages_result(result, *, as_json: bool) -> int:
    """Shared human/JSON rendering for verify/rollback results (issue #20)."""
    if as_json:
        import json as _json

        print(_json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0 if result.is_terminal_success else 1

    if result.status == "ok":
        _console.print(f"[green]✓[/green] {result.summary}")
    elif result.status == "warning":
        _console.print(f"[yellow]⚠[/yellow] {result.summary}")
    elif result.status == "blocked":
        _console.print(f"[yellow]?[/yellow] {result.summary}")
    else:
        _console.print(f"[red]✗[/red] {result.summary}")
    for artifact in result.artifacts:
        _console.print(f"    [cyan]{artifact}[/cyan]")
    for item in result.evidence:
        _console.print(f"    [dim]{item}[/dim]")
    for problem in result.problems:
        _console.print(f"    [dim]{problem}[/dim]")
    for action in result.suggested_actions:
        _console.print(f"    [dim]· {action}[/dim]")
    return 0 if result.is_terminal_success else 1


def _cmd_operator_verify(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator verify`` — re-check the last Pages publication (issue #20)."""
    from ..pipeline.operator_action import DEFAULT_REGISTRY, UnknownActionError

    action_kwargs: dict[str, Any] = {"work_dir": args.work_dir}
    if getattr(args, "max_attempts", None) is not None:
        action_kwargs["max_attempts"] = args.max_attempts
    if getattr(args, "retry_delay_seconds", None) is not None:
        action_kwargs["retry_delay_seconds"] = args.retry_delay_seconds

    action = DEFAULT_REGISTRY.build("verify_pages", **action_kwargs)
    try:
        result = DEFAULT_REGISTRY.execute(action, approved=True)
    except UnknownActionError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1
    return _print_pages_result(result, as_json=getattr(args, "json", False))


def _cmd_operator_rollback(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator rollback <run-id>`` — restore '/latest/' (issue #20).

    Gated exactly like ``operator publish`` (issue #19): always invoked with
    ``approved=True`` at the registry level (running the command is the
    coarse consent), but the actual git write additionally requires
    ``--confirm-public`` on this invocation or a prior durable approval for
    this exact rollback target.
    """
    from ..pipeline.operator_action import (
        DEFAULT_REGISTRY,
        ApprovalRequiredError,
        PersistenceUnavailableError,
        UnknownActionError,
    )
    from ..pipeline.run_manifest import ManifestPersistenceError, RunManifest

    action_kwargs: dict[str, Any] = {
        "work_dir": args.work_dir,
        "run_id": args.run_id,
        "repo_dir": getattr(args, "repo_dir", ".") or ".",
        "branch": getattr(args, "branch", "gh-pages") or "gh-pages",
        "remote": getattr(args, "remote", "origin") or "origin",
        "push": getattr(args, "push", True),
        "confirm_public": getattr(args, "confirm_public", False),
    }

    action = DEFAULT_REGISTRY.build("rollback_pages", **action_kwargs)
    try:
        result = DEFAULT_REGISTRY.execute(action, approved=True)
    except (UnknownActionError, ApprovalRequiredError, PersistenceUnavailableError) as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    try:
        RunManifest(args.work_dir).record_capability(result)
    except ManifestPersistenceError as exc:
        _warn_manifest_failure(args.work_dir, "registrere Pages-tilbakerulling i", exc)

    return _print_pages_result(result, as_json=getattr(args, "json", False))


def _cmd_operator_publish_history(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator publish-history`` — list Pages publish/rollback events (issue #20)."""
    from ..pipeline.pages_publish import list_publication_history

    history = list_publication_history(
        repo_dir=getattr(args, "repo_dir", ".") or ".", branch=getattr(args, "branch", "gh-pages") or "gh-pages"
    )

    if getattr(args, "json", False):
        import json as _json

        print(_json.dumps(history, indent=2, ensure_ascii=False))
        return 0

    if not history:
        _console.print("Ingen publiseringshistorikk funnet.")
        return 0

    _console.print(f"[bold]Publiseringshistorikk[/bold] ({len(history)})\n")
    for entry in history:
        marker = "[cyan]↩[/cyan]" if entry["kind"] == "rollback" else "[green]↑[/green]"
        label = "Tilbakerulling til" if entry["kind"] == "rollback" else "Publisert"
        _console.print(f"{marker} {label} kjøring [bold]{entry['run_id']}[/bold]")
        _console.print(f"    [dim]{entry['date']}  commit {entry['commit_sha'][:12]}[/dim]")
    return 0


def _cmd_scrape(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt scrape --club <name>`` — single-source scrape."""
    from datetime import datetime as dt
    from ..pipeline.state import PipelineState, StageName
    from ..pipeline.stage1_config import load_effective_config
    from ..pipeline.stage2_scraping import _scrape_source

    state = PipelineState(args.work_dir)
    cfg = load_effective_config(state)
    if not cfg:
        _console.print(
            "[red]✗[/red] Ingen Stage 1-konfigurasjon funnet. "
            "Kjør [bold]rvv-miniputt run[/bold] først."
        )
        return 1

    sources: list[dict[str, Any]] = cfg.get("sources", [])
    source_cfg = None
    for s in sources:
        if s.get("name", "").lower() == args.club.lower():
            source_cfg = s
            break

    if source_cfg is None:
        _console.print(f"[red]✗[/red] Ukjent kilde: '{args.club}'")
        _console.print("\n[bold]Tilgjengelige kilder:[/bold]")
        for s in sources:
            _console.print(f"  [cyan]{s.get('name', '?')}[/cyan] ({s.get('type', '?')})")
        return 1

    if getattr(args, "manual_bookup_login", False):
        import os

        os.environ["RVV_BOOKUP_MANUAL_LOGIN"] = "1"
        _console.print(
            "[cyan]ℹ[/cyan] BookUp manuell innlogging er aktiv — "
            "fullfør Vipps/SMS i nettleseren når den åpnes."
        )
    timeout = getattr(args, "manual_bookup_login_timeout", None)
    if timeout is not None:
        import os

        os.environ["RVV_BOOKUP_MANUAL_LOGIN_TIMEOUT"] = str(timeout)

    _console.print(
        f"[bold]Skraper:[/bold] {source_cfg['name']} "
        f"([dim]{source_cfg.get('type', '?')}[/dim])"
    )
    _console.print(f"  URL: [dim]{source_cfg.get('url', '')}[/dim]")

    start = dt.strptime(cfg["start_date"], "%Y-%m-%d")
    end = dt.strptime(cfg["end_date"], "%Y-%m-%d")

    result = _scrape_source(source_cfg, start_date=start, end_date=end, calendar_cache=None)

    n_events = result.get("event_count", 0)
    blocked = result.get("blocked", False)
    llm_fallback = result.get("llm_fallback", False)

    _console.print()
    if n_events > 0:
        _console.print(f"  [green]✓[/green] {n_events} hendelser funnet")
    else:
        _console.print(f"  [yellow]⚠[/yellow] {n_events} hendelser — ingen data i datoperioden")

    if blocked:
        _console.print(f"  [red]✗[/red] Blokkert: {result.get('block_reason', '')}")
        if result.get("recovery_hint"):
            _console.print(f"  [dim]{result['recovery_hint']}[/dim]")
    if result.get("scraper_error"):
        _console.print(f"  [red]✗[/red] Scraper-feil: {result['scraper_error']}")

    if llm_fallback:
        strategy = result.get("llm_strategy", {})
        engine = strategy.get("engine", "?")
        creds = strategy.get("credential_env_vars", [])
        cred_hint = f" (credentials: {', '.join(creds)})" if creds else ""
        _console.print(f"\n  [bold cyan]🤖 LLM-fallback tilgjengelig:[/bold cyan] {engine}{cred_hint}")
        nav = strategy.get("initial_navigation", [])
        if nav:
            _console.print(f"  Navigering ({len(nav)} steg):")
            for step in nav:
                cmd = step.get("cmd", "?")
                sel = step.get("selector", "")
                txt = step.get("text", "")
                if cmd == "note":
                    _console.print(f"    [dim]ℹ {txt}[/dim]")
                else:
                    _console.print(f"    → {cmd} [dim]{sel or txt}[/dim]")
        _console.print(f"\n  [dim]Kjør [bold]rvv-miniputt scrape-llm[/bold] for å skrape denne kilden med LLM.[/dim]")

    return 0



def _cache_events(work_dir: str, name: str, url: str, events: list[Any]) -> None:
    """Cache scraped events to the unified cache."""
    from datetime import datetime
    from ..pipeline.cache_manager import ScrapedDataCache
    cache = ScrapedDataCache(work_dir=work_dir)
    data = cache.read()
    if "sources" not in data:
        data["sources"] = {}
    data["sources"][name] = {
        "name": name,
        "url": url,
        "scrape_timestamp": datetime.now().isoformat(),
        "event_count": len(events),
        "blocked": False,
        "events": [
            {
                "date": e.date,
                "name": e.name,
                "datetime": e.datetime.isoformat(),
                "duration_hours": e.duration_hours,
            }
            for e in events
        ],
    }
    data["total_events"] = sum(
        s.get("event_count", 0) for s in data["sources"].values()
    )
    data["source_count"] = len(data["sources"])
    cache.write(data)




