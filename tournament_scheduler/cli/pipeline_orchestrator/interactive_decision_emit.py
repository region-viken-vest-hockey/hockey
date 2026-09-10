"""Compose and emit the interactive Stage 3 decision context."""

from __future__ import annotations

from typing import Any

from ...pipeline.run_log_paths import resolve_active_run_log_dir
from .interactive_state_io import (
    _MAX_INTERACTIVE_STAGE3_ATTEMPTS,
    _current_run_id,
    _read_stage3_interactive_state,
    _write_stage3_interactive_state,
)
from .judgment import _compute_verdict_tone
from .stage3_optimize_core import _maybe_run_stage3_cp_sat_shadow
from .verification import _baseline_hard_violations_for_plan, _mid_planning_decision_problem

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
            "source_details": checkpoint.get("sources", []),
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

    from ...llm_judge.prompts import build_decision_context
    from ...pipeline.state import StageName

    stage_name = list(StageName)[stage_num - 1]
    checkpoint = state.read_stage(stage_name) or {}
    effective_config = None
    if stage_num == 1:
        from ...pipeline.stage1_config import load_effective_config

        effective_config = load_effective_config(state, input_path=input_path)
    summary = _decision_summary_for_checkpoint(stage_num, checkpoint, effective_config=effective_config)
    context = build_decision_context(_INTERACTIVE_STAGE_KEYS[stage_num], summary)
    payload = context.to_dict()

    try:
        from ...pipeline.run_log_paths import resolve_active_run_log_dir

        log_dir = resolve_active_run_log_dir(work_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "decision_context.json", "w", encoding="utf-8") as fh:
            _json.dump(payload, fh, indent=2, ensure_ascii=False)
    except Exception:
        pass  # best-effort audit copy; stdout below is authoritative

    print(_json.dumps(payload, indent=2, ensure_ascii=False))
    return 2


def _emit_stage3_interactive_decision(
    state: "Any",
    work_dir: str,
    cfg: "dict[str, Any]",
    scraping: "dict[str, Any]",
    start: "Any",
    end: "Any",
    plan: "dict[str, Any]",
    log_fn: "Any",
) -> int:
    """Build, persist and print the Stage 3 :class:`DecisionContext` for the
    attempt that just ran (issue #260 P0).

    Unlike every other stage, which only ever offers the coarse
    proceed/abort-only context from :func:`_emit_interactive_decision_context`,
    Stage 3 gets the same nested ``optimize_plan``/``apply_candidate``/
    ``keep_baseline``/``request_operator`` decision loop the headless
    multi-seed path already drives via ``_decide_plan_adoption`` — the
    interactive harness itself is now the judge for this loop; deterministic
    validation and the bounded-attempt cap remain repo code.

    State (the running best attempt, and any not-yet-adopted candidate from
    the most recent rerun) is persisted to a small JSON side-file next to the
    stage checkpoints (:func:`_stage3_interactive_state_path`) so it survives
    across the separate CLI invocations an interactive harness makes between
    checkpoints.
    """
    import json as _json
    from dataclasses import replace as _dc_replace

    from ...application.decisions import DecisionContext
    from ...planning_contract import extract_candidate
    from ...stage3_ab import build_ab_report
    from ...stage3_decision import (
        STAGE3_DECISION_ACTIONS,
        _OPTIMIZE_PLAN_SCHEMAS,
        build_stage3_decision_context,
    )

    run_id = _current_run_id(state)
    interactive_state = _read_stage3_interactive_state(state, expected_run_id=run_id)
    attempts_used = int(interactive_state.get("attempts_used", 0))
    problem = _mid_planning_decision_problem(cfg, scraping, start, end)

    try:
        shadow_source_candidate = extract_candidate(plan)
    except (ValueError, KeyError):
        shadow_source_candidate = None
    cp_sat_shadow = _maybe_run_stage3_cp_sat_shadow(cfg, problem, shadow_source_candidate, log_fn)

    if attempts_used <= 0 or "best_plan" not in interactive_state:
        # First attempt this run: nothing to compare against yet — auto-
        # baseline, same as the headless multi-seed loop's
        # "best_plan is None -> adopt" first iteration.
        attempts_used = 1
        summary = _decision_summary_for_checkpoint(3, plan)
        if cp_sat_shadow is not None:
            summary = {**summary, "cp_sat_shadow": cp_sat_shadow}
        baseline_hard_violations = _baseline_hard_violations_for_plan(plan, problem)
        available = ["optimize_plan", "keep_baseline", "request_operator", "abort"]
        if attempts_used >= _MAX_INTERACTIVE_STAGE3_ATTEMPTS:
            available.remove("optimize_plan")
        context = DecisionContext(
            run_id=run_id,
            capability="stage3_interactive",
            stage="planning",
            objective=(
                "Decide whether this Stage 3 plan is good enough to finalize "
                "(keep_baseline), or another optimization attempt is worth "
                "the search budget (optimize_plan)."
            ),
            facts=summary,
            baseline_hard_violations=tuple(baseline_hard_violations),
            available_actions=tuple(available),
            # issue #262 P0: optimize_plan on this first attempt already
            # runs the Stage 3 v2 optimizer too (see _run_stage3_v2_optimize),
            # so it needs the same schema the else-branch below attaches.
            action_parameters=(
                {"optimize_plan": _OPTIMIZE_PLAN_SCHEMAS["v2_optimizer"]}
                if "optimize_plan" in available
                else {}
            ),
        )
        interactive_state = {
            "run_id": run_id,
            "attempts_used": attempts_used,
            "best_attempt": attempts_used,
            "best_plan": plan,
        }
    else:
        attempts_used += 1
        best_plan = interactive_state["best_plan"]
        best_attempt = interactive_state.get("best_attempt", 1)
        report = None
        try:
            report = build_ab_report(extract_candidate(best_plan), extract_candidate(plan), problem)
        except (ValueError, KeyError) as exc:
            log_fn(f"stage3_interactive attempt {attempts_used}: could not build A/B report: {exc}")

        available = list(STAGE3_DECISION_ACTIONS)
        if attempts_used >= _MAX_INTERACTIVE_STAGE3_ATTEMPTS:
            available.remove("optimize_plan")
        if report is not None:
            context = build_stage3_decision_context(
                report,
                run_id=run_id,
                baseline_ref=f"stage3_interactive:attempt_{best_attempt}",
                candidate_ref=f"stage3_interactive:attempt_{attempts_used}",
                objective=(
                    f"Decide whether Stage 3 attempt {attempts_used} should replace "
                    f"the current best attempt ({best_attempt}), request another "
                    "optimization attempt, ask the operator, or keep the current best."
                ),
                # issue #262 P0: optimize_plan now runs the Stage 3 v2
                # optimizer (see _run_stage3_v2_optimize), not a legacy
                # SeasonPlanner rerun -- attach the schema that matches what
                # will actually execute.
                optimize_plan_schema="v2_optimizer",
            )
            context = _dc_replace(context, available_actions=tuple(available) + ("abort",))
        else:
            # A/B report couldn't be built — fall back to keep/abort only,
            # never silently apply an uncompared candidate. Still
            # independently verify the current best plan so a hard-failing
            # baseline can't be finalized via keep_baseline just because the
            # A/B comparison itself broke.
            context = DecisionContext(
                run_id=run_id,
                capability="stage3_optimize",
                stage="planning",
                objective="Could not build an old-vs-new comparison report for this attempt.",
                baseline_hard_violations=tuple(_baseline_hard_violations_for_plan(best_plan, problem)),
                available_actions=("keep_baseline", "request_operator", "abort"),
            )
        if cp_sat_shadow is not None:
            context = _dc_replace(context, facts={**context.facts, "cp_sat_shadow": cp_sat_shadow})
        interactive_state["attempts_used"] = attempts_used
        interactive_state["pending_candidate"] = plan
        # A prior attempt may have been a Pareto search (issue #264 P1) --
        # drop its plural pending_candidates so a later apply_candidate
        # decision resolves against this attempt's single candidate, not a
        # stale multi-candidate list from two attempts ago.
        interactive_state.pop("pending_candidates", None)
        interactive_state["pending_attempt"] = attempts_used

    interactive_state["last_context"] = context.to_dict()
    _write_stage3_interactive_state(state, interactive_state)

    # issue #264 P0: append this attempt's independently-computed
    # verify/score evidence to the durable, run-scoped attempt log --
    # unlike interactive_state above, this survives past apply_candidate/
    # keep_baseline clearing the pending-decision side-state, so a later
    # Stage 4 evidence bundle can show every attempt that was tried, not
    # just the one ultimately selected.
    try:
        from ...planning_contract import extract_candidate
        from ...pipeline.evidence_bundle import append_stage3_attempt_log_entry, build_stage3_attempt_entry

        append_stage3_attempt_log_entry(
            state.work_dir,
            build_stage3_attempt_entry(attempt=attempts_used, candidate=extract_candidate(plan), problem=problem),
        )
    except Exception as exc:
        log_fn(f"stage3_interactive attempt {attempts_used}: could not append attempt-log entry: {exc}")

    if cp_sat_shadow is not None:
        # issue #288: the automatic CP-SAT shadow comparison is evidence
        # alongside the attempt that was actually decided on, not a
        # candidate of its own -- record it as its own entry so a reviewer
        # sees it was attempted even though it never became pending_candidate.
        try:
            append_stage3_attempt_log_entry(
                state.work_dir,
                {**cp_sat_shadow, "attempt": attempts_used, "engine": "cp_sat_shadow"},
            )
        except Exception as exc:
            log_fn(f"stage3_interactive attempt {attempts_used}: could not append cp_sat shadow attempt-log entry: {exc}")

    payload = context.to_dict()
    try:
        log_dir = resolve_active_run_log_dir(work_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "decision_context.json", "w", encoding="utf-8") as fh:
            _json.dump(payload, fh, indent=2, ensure_ascii=False)
    except Exception:
        pass  # best-effort audit copy; stdout below is authoritative

    print(_json.dumps(payload, indent=2, ensure_ascii=False))
    return 2
