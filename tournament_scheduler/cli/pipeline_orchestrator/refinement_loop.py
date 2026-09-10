"""The post-Stage-4 skill-driven plan refinement loop."""

from __future__ import annotations

import argparse
from typing import Any

from ._shared import _console
from .judgment import _compute_verdict_tone
from .refinement_decisions import (
    _decide_continue_refinement,
    _decide_refinement_candidate,
    _refinement_decision_problem,
    _refinement_metrics,
)

_MAX_REFINEMENT_ITERATIONS = 3


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

    from ...pipeline.manual_adjustment_workflow import ManualAdjustmentWorkflow
    from ...pipeline.stage3_helpers import _resolve_plan_dict
    from ...pipeline.stage4_helpers import _dict_to_plan
    from ...pipeline.state import StageName
    from ...stage3_optimizer import optimize_candidate
    from ..plan_critic import generate_critic_findings, suggest_moves

    workflow = ManualAdjustmentWorkflow(state)
    current_checkpoint = plan_checkpoint
    try:
        from ...pipeline.run_manifest import RunManifest

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
