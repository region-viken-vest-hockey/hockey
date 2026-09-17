"""``rvv-miniputt run --interactive`` top-level command handler."""

from __future__ import annotations

import argparse
from datetime import datetime
from time import perf_counter
from typing import Any

from ._shared import _console
from .export_command import _run_stage4_export
from .stage3_capabilities import InteractiveStage3Capabilities
from .interactive_decision_emit import (
    _INTERACTIVE_STAGE_KEYS,
    _decision_summary_for_checkpoint,
    _emit_interactive_decision_context,
    _emit_stage3_interactive_decision,
)
from .interactive_state_io import (
    _current_run_id,
    _read_arena_conflict_state,
    _read_shared_host_state,
)
from .manifest import _manifest_start_run
from .run_log import _resolve_resume_stage
from .shared_host_decisions import _resolve_shared_host_decisions
from .stage1 import _run_stage1
from .stage2 import _run_stage2
from .stage3_optimize_variants import _run_stage3_pareto_optimize, _run_stage3_v2_optimize
from .stage3_pareto_decision import _emit_stage3_pareto_decision
from .stage3_run import _run_stage3
from .verification import (
    _assert_hard_verification_before_export,
    _mid_planning_decision_problem,
    _reconcile_verified_manual_state,
    _write_run_evidence_bundle,
)


def _render_decision_payload(payload: dict[str, Any], work_dir: str) -> int:
    """Best-effort audit copy + stdout render of a pending DecisionContext.

    Returns the canonical "paused for decision" exit code (2). The stdout
    render is authoritative; the run-log copy is a convenience for debugging.
    """
    import json as _json

    try:
        from ...pipeline.run_log_paths import resolve_active_run_log_dir

        log_dir = resolve_active_run_log_dir(work_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "decision_context.json", "w", encoding="utf-8") as fh:
            _json.dump(payload, fh, indent=2, ensure_ascii=False)
    except Exception:
        pass
    print(_json.dumps(payload, indent=2, ensure_ascii=False))
    return 2


def _emit_pending_stage3_subdecision_context(state: "Any", work_dir: str, resume_from: int) -> int | None:
    """Re-emit an unanswered in-Stage-3 sub-decision, if one is pending.

    Inspecting an interactive run with ``--resume-from 3`` and no fresh
    ``--decision-action`` must show the current pending shared-host or
    arena-conflict decision. It must not rebuild Stage 3 from scratch, because
    that can re-ask already answered sub-decisions and lose convergence.
    """
    if resume_from != 3:
        return None

    run_id = _current_run_id(state)
    for reader in (_read_shared_host_state, _read_arena_conflict_state):
        saved = reader(state, expected_run_id=run_id)
        if not saved.get("pending"):
            continue
        payload = saved.get("last_context")
        if not isinstance(payload, dict) or not payload:
            continue
        return _render_decision_payload(payload, work_dir)
    return None


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

    Lifecycle for this loop lives in the application-layer Stage 3 session
    (:mod:`tournament_scheduler.application.stage3_session`) and its explicit
    transition engine
    (:mod:`tournament_scheduler.application.stage3_controller`). This module
    only validates the submitted action against the session (stale
    revision/fingerprint rejection), invokes the deterministic domain work,
    and records the finalized candidate revision/fingerprint Stage 4 must
    consume. It does not decide whether an answer reruns the planner or which
    side file to clear. Inspect the session with ``rvv-miniputt stage3
    session``.
    """
    import json as _json

    from ...application.decisions import DecisionAction, DecisionContext, decide, record_llm_decision
    from ...llm_judge.prompts import build_decision_context
    from ...pipeline.state import PipelineState, StageName

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
        from ...application.stage3_session_store import Stage3SessionStore

        # One canonical clear for the whole interactive Stage 3 state: a
        # genuinely new run must not inherit candidate revisions, pending
        # decisions or run-scoped choices from a superseded run.
        Stage3SessionStore(state.work_dir).clear()
        from ...pipeline.evidence_bundle import clear_stage3_attempt_log
        from .stage3_cpsat_cache import clear_cp_sat_cache

        clear_stage3_attempt_log(state.work_dir)
        clear_cp_sat_cache(state)

    if decision_payload is None:
        pending_subdecision_code = _emit_pending_stage3_subdecision_context(state, args.work_dir, resume_from)
        if pending_subdecision_code is not None:
            return pending_subdecision_code

    # One persisted session is the authority for the whole interactive Stage 3
    # lifecycle. The CLI only loads it, submits the typed action to the
    # controller with deterministic domain capabilities, and renders the
    # result; it no longer coordinates side files or decides whether an answer
    # replans, repairs or advances.
    from ...application.stage3_controller import Stage3Controller
    from ...application.stage3_session_store import Stage3SessionStore

    session_store = Stage3SessionStore(state.work_dir)
    session = session_store.load(expected_run_id=_current_run_id(state))
    pending_capability = str((session.pending_decision or {}).get("capability") or "")
    capabilities = InteractiveStage3Capabilities(
        state,
        args,
        _log,
        run_id=_current_run_id(state),
        problem_fn=_mid_planning_decision_problem,
    )
    controller = Stage3Controller()

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

    # A shared/joint-club hosting sub-decision is run-scoped pre-plan session
    # data: the harness answers it with the same --resume-from 3 it reached the
    # pause with, and it is validated/applied through the session transition
    # engine. Resolved choices stay in the session so a later Stage 3 re-entry
    # reuses the same pre-plan decision instead of asking it again.
    if decision_payload is not None and pending_capability == "shared_host_assignment":
        try:
            shared_host_action = DecisionAction.from_dict(decision_payload)
        except Exception as exc:
            _console.print(f"[red]✗[/red] Ugyldig DecisionAction: {exc}")
            return 1

        shared_host_context = DecisionContext.from_dict((session.pending_decision or {}).get("context") or {})
        session_reason = controller.validate(session, shared_host_action)
        if session_reason:
            _console.print(f"[red]✗[/red] Delt vertskap-avgjørelse avvist: {session_reason}.")
            return 1
        shared_host_result = decide(shared_host_context, shared_host_action)
        try:
            record_llm_decision(str(state.work_dir), shared_host_context, shared_host_action, shared_host_result)
        except Exception as exc:
            _log(f"record_llm_decision failed: {exc}")
        if not shared_host_result.accepted:
            _console.print(f"[red]✗[/red] Avgjørelse avvist: {shared_host_result.rejection_reason}")
            return 1

        outcome = controller.handle(session, shared_host_action, capabilities)
        if not outcome.accepted:
            _console.print(f"[red]✗[/red] Delt vertskap-avgjørelse avvist: {outcome.reason}.")
            return 1
        session_store.save(session)
        # Answered — continue as if this invocation carried no decision payload
        # at all, so execution below re-checks for another pending shared-host
        # decision (pauses again if one remains) or proceeds straight into
        # Stage 3 once none remain, without an extra harness round trip.
        decision_payload = None
        pending_capability = ""

    # An internal arena/time double-booking is a candidate-scoped post-plan
    # sub-decision: the harness answers it with the same --resume-from 3, and
    # the transition engine applies it to the exact persisted candidate and
    # either binds the next collision or clears the pending decision so the
    # existing candidate can be adopted. It never rebuilds the season.
    if decision_payload is not None and pending_capability == "arena_conflict_resolution":
        try:
            arena_action = DecisionAction.from_dict(decision_payload)
        except Exception as exc:
            _console.print(f"[red]✗[/red] Ugyldig DecisionAction: {exc}")
            return 1

        arena_context = DecisionContext.from_dict((session.pending_decision or {}).get("context") or {})
        session_reason = controller.validate(session, arena_action)
        if session_reason:
            _console.print(f"[red]✗[/red] Arena-avgjørelse avvist: {session_reason}.")
            return 1
        arena_result = decide(arena_context, arena_action)
        try:
            record_llm_decision(str(state.work_dir), arena_context, arena_action, arena_result)
        except Exception as exc:
            _log(f"record_llm_decision failed: {exc}")
        if not arena_result.accepted:
            _console.print(f"[red]✗[/red] Avgjørelse avvist: {arena_result.rejection_reason}")
            return 1

        outcome = controller.handle(session, arena_action, capabilities)
        if not outcome.accepted:
            _console.print(f"[red]✗[/red] Arena-avgjørelse avvist: {outcome.reason}.")
            return 1
        session_store.save(session)
        if outcome.context is not None:
            return _render_decision_payload(outcome.context, args.work_dir)

        # No collision remains: offer the Stage 3 adoption decision on the
        # exact persisted candidate. Do not rebuild the season merely because
        # one local conflict was answered.
        from ...pipeline.state import StageName

        try:
            cfg, scraping, start, end = capabilities.resolved_problem()
        except Exception as exc:
            _console.print(f"[red]✗[/red] Kunne ikke lese Stage 1-datoer for lokal arena-reparasjon: {exc}")
            return 1
        checkpoint = dict(state.read_stage(StageName.PLANNING) or {})
        return _emit_stage3_interactive_decision(
            state,
            args.work_dir,
            cfg,
            scraping,
            start,
            end,
            checkpoint,
            _log,
            suppress_auto_cp_sat_shadow=True,
        )

    if decision_payload is not None:
        prev_stage_num = resume_from - 1
        if prev_stage_num < 1:
            _console.print("[red]✗[/red] --decision-action krever --resume-from > 1 (ingen forrige stage å avgjøre).")
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

        if prev_stage_num == 3:
            last_context_payload = (session.pending_decision or {}).get("context")
            if not last_context_payload:
                _console.print("[red]✗[/red] Fant ingen Stage 3-avgjørelseskontekst å avgjøre.")
                return 1
            prev_context = DecisionContext.from_dict(last_context_payload)
            session_reason = controller.validate(session, decision_action)
            if session_reason:
                _console.print(f"[red]✗[/red] Stage 3-avgjørelse avvist: {session_reason}.")
                return 1
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
            _console.print(f"[red]✗[/red] Avgjørelse avvist: {decision_result.rejection_reason}")
            return 1
        if decision_action.action_id == "abort":
            _console.print("[yellow]Avbrutt etter operatørens avgjørelse.[/yellow]")
            return 1

        if prev_stage_num == 3:
            if decision_action.action_id == "optimize_plan":
                # ``run_search`` is an explicit transition, but executing the
                # optimizer is itself a Stage 3 entry point: record the
                # requested search and let the stage orchestration below run
                # it, then the emission binds the resulting revision.
                resume_from = 3
                optimize_plan_arguments = dict(decision_action.arguments or {})
                if optimize_plan_arguments.get("mode") == "pareto":
                    use_pareto_for_stage3 = True
                else:
                    use_v2_optimizer_for_stage3 = True
            else:
                # apply_repair_option / apply_candidate / keep_baseline /
                # request_operator: one explicit candidate transition, applied
                # by the session controller with the domain capabilities.
                outcome = controller.handle(session, decision_action, capabilities)
                if not outcome.accepted:
                    _console.print(f"[red]✗[/red] Stage 3-avgjørelse avvist: {outcome.reason}.")
                    return 1
                session_store.save(session)
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
        portfolio, abort = _run_stage3_pareto_optimize(state, cfg, scraping, start, end, optimize_plan_arguments, _log)
        if abort:
            return 1
        return _emit_stage3_pareto_decision(state, args.work_dir, cfg, scraping, start, end, portfolio, _log)

    if resume_from == 3 and use_v2_optimizer_for_stage3:
        _stage3_started = perf_counter()
        plan, abort = _run_stage3_v2_optimize(state, cfg, scraping, start, end, optimize_plan_arguments, _log)
        if abort:
            return 1
        # issue #310: an explicit optimize_plan(engine="cp_sat") pass must
        # not be followed by an automatic shadow re-solving the candidate it
        # just produced -- see _emit_stage3_interactive_decision's
        # skip_auto_cp_sat_shadow docstring.
        engine_used = str((optimize_plan_arguments or {}).get("engine") or "local_search").replace("-", "_")
        return _emit_stage3_interactive_decision(
            state, args.work_dir, cfg, scraping, start, end, plan, _log,
            skip_auto_cp_sat_shadow=(engine_used == "cp_sat"),
            stage3_elapsed_seconds=perf_counter() - _stage3_started,
            candidate_transition="run_search",
        )

    shared_host_decisions: list[dict[str, Any]] = []
    if resume_from == 3:
        # issue #355: a promoted canonical season already encodes every
        # shared/joint-club hosting decision in its published placements, so
        # do not pause the harness to re-decide one before Stage 3 adopts
        # the canonical baseline.
        from ...canonical_baseline import resolve_canonical_state

        if resolve_canonical_state(cfg, start.date(), end.date()) is not None:
            shared_host_decisions = []
        else:
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

    _stage3_started = perf_counter()
    plan, abort, _stage3_failed = _run_stage3(
        args, cfg, scraping, state, start, end, strict, resume_from, _log, stage3_search_iterations,
        shared_host_decisions=shared_host_decisions,
    )
    if abort:
        return 1
    if resume_from == 3:
        return _emit_stage3_interactive_decision(
            state, args.work_dir, cfg, scraping, start, end, plan, _log,
            stage3_elapsed_seconds=perf_counter() - _stage3_started,
        )

    if resume_from <= 4:
        # Stage 3 finalization records the exact candidate revision/fingerprint
        # Stage 4 must consume. Refuse to export a checkpoint that no longer
        # matches it, so no other side-state can silently replace the
        # reviewed result between finalization and export.
        from ...application.stage3_session_store import Stage3SessionStore
        from ...pipeline.state import StageName

        if not Stage3SessionStore(state.work_dir).finalized_candidate_matches(
            state.read_stage(StageName.PLANNING)
        ):
            _console.print(
                "[red]✗[/red] Stage 4 nektet: Stage 3-checkpointet stemmer ikke med "
                "den ferdigstilte kandidatrevisjonen."
            )
            return 1
        _verify_started = perf_counter()
        verification_ok = _assert_hard_verification_before_export(
            plan, _mid_planning_decision_problem(cfg, scraping, start, end, state.work_dir), strict, _console, _log
        )
        try:
            from ...pipeline.run_manifest import RunManifest

            RunManifest(state.work_dir).record_timing(
                "stage3_verification_seconds", perf_counter() - _verify_started
            )
        except Exception as exc:
            _log(f"Could not record verification timing: {exc}")
        if not verification_ok:
            return 1
        _reconcile_verified_manual_state(
            plan, _mid_planning_decision_problem(cfg, scraping, start, end, state.work_dir), _log
        )

    _generated_calendars, abort, _stage4_failed = _run_stage4_export(
        args, plan, state, strict, _log, resume_from
    )
    if abort:
        return 1
    if resume_from <= 4:
        _write_run_evidence_bundle(args, state, cfg, scraping, start, end, plan, _log)
    return _emit_interactive_decision_context(4, state, args.work_dir)
