"""Stage judging, verdict-tone, and plan-attempt-quality logic."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._shared import _console

if TYPE_CHECKING:
    from ...pipeline.state import StageName

_STAGE_NAMES = {1: "stage1", 2: "stage2", 3: "stage3"}


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

    from ...application.decisions import DecisionAction, decide, record_llm_decision
    from ...llm_judge import build_decision_context, build_stage_prompt, get_judge_if_headless

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
    from ...html.renderers import judgment as _judgment
    from ...pipeline.stage4_helpers import _dict_to_plan

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
