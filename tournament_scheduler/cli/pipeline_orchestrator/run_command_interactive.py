"""``rvv-miniputt run --interactive`` top-level command handler."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any

from ._shared import _console
from .export_command import _run_stage4_export
from .interactive_decision_emit import (
    _INTERACTIVE_STAGE_KEYS,
    _decision_summary_for_checkpoint,
    _emit_interactive_decision_context,
    _emit_stage3_interactive_decision,
)
from .interactive_state_io import (
    _clear_shared_host_state,
    _clear_stage3_interactive_state,
    _current_run_id,
    _read_shared_host_state,
    _read_stage3_interactive_state,
    _write_shared_host_state,
)
from .manifest import _manifest_start_run
from .run_log import _resolve_resume_stage
from .shared_host_decisions import _resolve_shared_host_decisions
from .stage1 import _run_stage1
from .stage2 import _run_stage2
from .stage3_optimize_variants import _run_stage3_pareto_optimize, _run_stage3_v2_optimize
from .stage3_pareto_decision import _emit_stage3_pareto_decision
from .stage3_run import _run_stage3
from .verification import _assert_hard_verification_before_export, _mid_planning_decision_problem, _reconcile_verified_manual_state, _write_run_evidence_bundle

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

    Stage 3 is a nested decision loop rather than a single-attempt gate
    (issue #260 P0): the context offered after Stage 3 is
    :func:`_emit_stage3_interactive_decision`'s ``optimize_plan``/
    ``apply_candidate``/``keep_baseline``/``request_operator`` — the same
    action vocabulary the headless multi-seed loop uses via
    ``_decide_plan_adoption`` — not the generic proceed/abort context. Passing
    ``optimize_plan`` (optionally with ``arguments.iterations`` as a bounded
    search-budget override) re-runs Stage 3 for another attempt and emits a
    new decision comparing it against the current best, instead of advancing;
    ``apply_candidate``/``keep_baseline`` resolve the loop and advance to
    Stage 4. The loop is capped at
    :data:`_MAX_INTERACTIVE_STAGE3_ATTEMPTS` attempts — ``optimize_plan`` is
    no longer offered past the cap. There is no longer a need to fall back to
    the non-interactive ``run --resume-from 3`` for multi-attempt refinement.
    """
    import json as _json

    from ...application.decisions import DecisionAction, DecisionContext, decide, record_llm_decision
    from ...llm_judge.prompts import build_decision_context
    from ...pipeline.state import PipelineState, StageName, StageStatus

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

    if resume_from == 1 and decision_payload is None:
        # issue #264 P0: this is the very first invocation of a new logical
        # run (no decision to answer yet, starting at Stage 1) -- resolve
        # this run's run_id here, before anything else, so every Stage 3
        # DecisionContext this run emits is scoped to a run_id that's
        # actually unique to it, and explicitly drop any Stage 3 interactive
        # side-state left behind by a prior/superseded run in this same work
        # directory rather than letting it silently resurface as this run's
        # "best plan so far" / attempt count. A `retry_stage` decision that
        # happens to reset resume_from back to 1 always carries a
        # decision_payload, so it never re-triggers this branch.
        _manifest_start_run(args.work_dir, args.input, getattr(args, "objective", None))
        _clear_stage3_interactive_state(state)
        _clear_shared_host_state(state)
        from ...pipeline.evidence_bundle import clear_stage3_attempt_log

        clear_stage3_attempt_log(state.work_dir)

    stage3_search_iterations: int | None = None
    # issue #262 P0: optimize_plan must invoke the generic Stage 3 v2
    # optimizer (stage3_optimizer.optimize_candidate) rather than rerunning
    # legacy SeasonPlanner -- see _run_stage3_v2_optimize below.
    use_v2_optimizer_for_stage3 = False
    # issue #264 P1 / issue #265 P1: optimize_plan(arguments.mode == "pareto")
    # runs the shared multi-objective search instead -- see
    # _run_stage3_pareto_optimize/_emit_stage3_pareto_decision.
    use_pareto_for_stage3 = False
    optimize_plan_arguments: dict[str, Any] | None = None

    # issue #274: a pending shared/joint-club hosting decision
    # (_resolve_shared_host_decisions) is a distinct in-Stage-3 sub-decision
    # that happens *before* Stage 3 has produced a candidate at all, so it
    # cannot reuse the prev_stage_num = resume_from - 1 contract below (that
    # contract assumes the decision belongs to a just-completed stage/Stage-3
    # attempt). The harness answers it with the same --resume-from it used
    # to reach here (still 3 — we have not logically advanced past Stage 3
    # yet) plus --decision-action; detected here by checking for
    # shared_host_decision_state.json's "pending" entry rather than by
    # resume_from's value.
    pending_shared_host = (
        _read_shared_host_state(state, expected_run_id=_current_run_id(state)).get("pending")
        if decision_payload is not None
        else None
    )
    if decision_payload is not None and pending_shared_host is not None:
        try:
            shared_host_action = DecisionAction.from_dict(decision_payload)
        except Exception as exc:
            _console.print(f"[red]✗[/red] Ugyldig DecisionAction: {exc}")
            return 1

        shared_host_state = _read_shared_host_state(state, expected_run_id=_current_run_id(state))
        shared_host_context = DecisionContext.from_dict(shared_host_state.get("last_context") or {})
        shared_host_result = decide(shared_host_context, shared_host_action)
        try:
            record_llm_decision(str(state.work_dir), shared_host_context, shared_host_action, shared_host_result)
        except Exception as exc:
            _log(f"record_llm_decision failed: {exc}")
        if not shared_host_result.accepted:
            _console.print(f"[red]✗[/red] Avgjørelse avvist: {shared_host_result.rejection_reason}")
            return 1

        decisions_list = list(shared_host_state.get("decisions") or [])
        unresolved_list = list(shared_host_state.get("unresolved") or [])
        if shared_host_action.action_id == "assign_shared_host":
            from ...shared_host_decision import shared_host_decision_record

            decisions_list.append(
                shared_host_decision_record(
                    pending_shared_host["registration"],
                    pending_shared_host["age_group"],
                    str(shared_host_action.arguments.get("chosen_club", "")),
                    str(shared_host_action.rationale or ""),
                    decided_by="harness",
                    decided_at=datetime.now(timezone.utc).isoformat(),
                )
            )
        else:
            unresolved_list.append(dict(pending_shared_host))
        _write_shared_host_state(
            state,
            {
                "run_id": _current_run_id(state),
                "decisions": decisions_list,
                "unresolved": unresolved_list,
                "pending": None,
                "last_context": None,
            },
        )
        # Answered — fall through as if this invocation carried no decision
        # payload at all, so execution below re-checks for another pending
        # shared-host decision (pauses again if one remains) or proceeds
        # straight into Stage 3 once none remain, without an extra harness
        # round trip once everything is resolved.
        decision_payload = None

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

        try:
            decision_action = DecisionAction.from_dict(decision_payload)
        except Exception as exc:
            _console.print(f"[red]✗[/red] Ugyldig DecisionAction: {exc}")
            return 1

        stage3_interactive_state: dict[str, Any] | None = None
        if prev_stage_num == 3:
            stage3_interactive_state = _read_stage3_interactive_state(state, expected_run_id=_current_run_id(state))
            last_context_payload = stage3_interactive_state.get("last_context")
            if not last_context_payload:
                _console.print("[red]✗[/red] Fant ingen Stage 3-avgjørelseskontekst å avgjøre.")
                return 1
            prev_context = DecisionContext.from_dict(last_context_payload)
        else:
            prev_checkpoint = state.read_stage(prev_stage_name)
            prev_effective_config = None
            if prev_stage_num == 1:
                from ...pipeline.stage1_config import load_effective_config

                prev_effective_config = load_effective_config(state, input_path=args.input)
            prev_summary = _decision_summary_for_checkpoint(
                prev_stage_num, prev_checkpoint, effective_config=prev_effective_config
            )
            prev_context = build_decision_context(_INTERACTIVE_STAGE_KEYS[prev_stage_num], prev_summary)

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

        if prev_stage_num == 3 and stage3_interactive_state is not None:
            if decision_action.action_id == "optimize_plan":
                resume_from = 3
                optimize_plan_arguments = dict(decision_action.arguments or {})
                if optimize_plan_arguments.get("mode") == "pareto":
                    use_pareto_for_stage3 = True
                else:
                    use_v2_optimizer_for_stage3 = True
            elif decision_action.action_id == "apply_candidate":
                pending_candidates = stage3_interactive_state.get("pending_candidates")
                if pending_candidates:
                    # issue #264 P1: a Pareto attempt left several
                    # candidates pending, not one -- the on-disk checkpoint
                    # still holds the pre-search baseline, so the chosen
                    # candidate_ref has to be written explicitly here.
                    candidate_ref = (decision_action.arguments or {}).get("candidate_ref")
                    chosen = next(
                        (entry for entry in pending_candidates if entry.get("candidate_ref") == candidate_ref),
                        None,
                    )
                    if chosen is not None:
                        checkpoint = dict(state.read_stage(StageName.PLANNING) or {})
                        checkpoint["plan"] = chosen["candidate"]
                        state.write_stage(StageName.PLANNING, checkpoint, status=StageStatus.DONE)
                # else: the on-disk Stage 3 checkpoint already holds the
                # candidate that was just rerun (the v2-optimizer path's
                # single pending attempt), so no checkpoint rewrite is
                # needed here.
                _clear_stage3_interactive_state(state)
            else:
                # keep_baseline (or any other accepted action): the on-disk
                # checkpoint currently holds the just-rejected rerun attempt,
                # so restore the persisted best plan before advancing —
                # mirrors the headless loop's re-persist-selected-attempt step.
                best_plan = stage3_interactive_state.get("best_plan")
                if best_plan is not None:
                    state.write_stage(StageName.PLANNING, best_plan, status=StageStatus.DONE)
                _clear_stage3_interactive_state(state)
        elif decision_action.action_id == "retry_stage":
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

    if resume_from == 3 and use_pareto_for_stage3:
        portfolio, abort = _run_stage3_pareto_optimize(
            state, cfg, scraping, start, end, optimize_plan_arguments, _log
        )
        if abort:
            return 1
        return _emit_stage3_pareto_decision(state, args.work_dir, cfg, scraping, start, end, portfolio, _log)

    if resume_from == 3 and use_v2_optimizer_for_stage3:
        plan, abort = _run_stage3_v2_optimize(
            state, cfg, scraping, start, end, optimize_plan_arguments, _log
        )
        if abort:
            return 1
        return _emit_stage3_interactive_decision(state, args.work_dir, cfg, scraping, start, end, plan, _log)

    shared_host_decisions: list[dict[str, Any]] = []
    if resume_from == 3:
        # issue #274: resolve every shared/joint-club hosting decision
        # before Stage 3 actually builds a plan — pauses (returns here) for
        # the harness to answer one at a time if no headless judge is
        # configured, exactly like every other interactive decision point
        # in this function. Only reachable for the plain (non-Pareto,
        # non-v2-optimizer) first-time Stage 3 pass — those two branches
        # above already returned, and a resumed `--resume-from 4` run skips
        # this entirely (shared-host decisions were already resolved and
        # baked into the checkpoint the first time Stage 3 ran).
        pause_code, shared_host_decisions = _resolve_shared_host_decisions(
            state, cfg, scraping, start, end, _log, interactive=True,
        )
        if pause_code is not None:
            return pause_code

    plan, abort, _stage3_failed = _run_stage3(
        args, cfg, scraping, state, start, end, strict, resume_from, _log, stage3_search_iterations,
        shared_host_decisions=shared_host_decisions,
    )
    if abort:
        return 1
    if resume_from == 3:
        return _emit_stage3_interactive_decision(state, args.work_dir, cfg, scraping, start, end, plan, _log)

    if resume_from <= 4 and not _assert_hard_verification_before_export(
        plan, _mid_planning_decision_problem(cfg, scraping, start, end), strict, _console, _log
    ):
        return 1
    if resume_from <= 4:
        _reconcile_verified_manual_state(
            plan, _mid_planning_decision_problem(cfg, scraping, start, end), _log
        )

    _generated_calendars, abort, _stage4_failed = _run_stage4_export(
        args, plan, state, strict, _log, resume_from
    )
    if abort:
        return 1
    if resume_from <= 4:
        _write_run_evidence_bundle(args, state, cfg, scraping, start, end, plan, _log)
    return _emit_interactive_decision_context(4, state, args.work_dir)
