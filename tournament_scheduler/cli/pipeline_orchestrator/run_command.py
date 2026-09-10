"""``rvv-miniputt run`` top-level command handler."""

from __future__ import annotations

import argparse
from datetime import datetime
from typing import Any

from ._shared import _console
from .export_command import _regenerate_calendar, _run_stage4_export
from .interactive_state_io import _clear_shared_host_state, _clear_stage3_interactive_state
from .judgment import _compute_verdict_tone, _format_plan_attempt_quality, _plan_attempt_quality, _plan_attempt_quality_adopts
from .manifest import _manifest_finalize, _manifest_record, _manifest_set_active, _manifest_start_run
from .plan_adoption import _decide_plan_adoption, _run_mid_planning_critic_loop
from .refinement_reexport import _run_refinement_and_reexport
from .run_command_interactive import _cmd_run_interactive
from .run_log import _resolve_resume_stage, _write_run_log
from .shared_host_decisions import _resolve_shared_host_decisions
from .stage1 import _run_approval_gate, _run_stage1
from .stage2 import _run_stage2
from .stage3_run import _run_stage3
from .verification import _assert_hard_verification_before_export, _mid_planning_decision_problem, _reconcile_verified_manual_state, _write_run_evidence_bundle

def _cmd_run(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt run`` — full pipeline stages 1→4 + HTML."""
    if getattr(args, "interactive", False):
        return _cmd_run_interactive(args)

    from ...pipeline.state import PipelineState

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
    _clear_stage3_interactive_state(state)
    _clear_shared_host_state(state)
    from ...pipeline.evidence_bundle import clear_stage3_attempt_log

    clear_stage3_attempt_log(state.work_dir)

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
        from ...pipeline.source_health import compute_source_health

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
        from ...pipeline.run_manifest import RunManifest

        multi_seed_run_id = str(RunManifest(state.work_dir).read().get("run_id") or "")
    except Exception:
        multi_seed_run_id = ""

    # issue #274: resolve shared/joint-club hosting decisions once, before
    # any Stage 3 attempt, so every attempt in the retry loop below sees the
    # same decision (never re-asked/re-decided per attempt). This is the
    # non-interactive `run` path (never `--interactive`, see the top of this
    # function) — `interactive=False` means a missing headless judge falls
    # back to the deterministic legacy order rather than pausing, matching
    # every other decision point's no-judge behavior here.
    _, shared_host_decisions = _resolve_shared_host_decisions(
        state, cfg, scraping, start, end, _log, interactive=False,
    )

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
            shared_host_decisions=shared_host_decisions,
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
                from ...pipeline.state import StageName, StageStatus

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

    if not _assert_hard_verification_before_export(
        plan, _mid_planning_decision_problem(cfg, scraping, start, end), strict, _console, _log
    ):
        _manifest_record(
            args.work_dir, "export", "blocked", "Hard-verification gate rejected the plan before export."
        )
        _manifest_finalize(args.work_dir, "blocked")
        _write_run_log(args, state, log_start, log_lines, success=False)
        return 1
    _reconcile_verified_manual_state(
        plan, _mid_planning_decision_problem(cfg, scraping, start, end), _log
    )

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
    #
    # issue #265 P1 ("Stage 4 should export, not plan") flagged this loop as
    # a candidate for removal from the canonical decision-driven path, but
    # it is already judge-consulted here (_decide_continue_refinement calls
    # the same headless judge before falling back to the tone-bucket gate —
    # issue #260 Phase 4) and tests assert the judge is called for exactly
    # this continue-decision on a headless run
    # (test_judge_called_three_times_when_all_proceed et al.). Disabling it
    # outright on the judge-configured path would remove a decision the
    # canonical architecture already routes through the judge, not bypass
    # it, so this is left as a scoped follow-up rather than changed here.
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

    _write_run_evidence_bundle(args, state, cfg, scraping, start, end, plan, _log)

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
