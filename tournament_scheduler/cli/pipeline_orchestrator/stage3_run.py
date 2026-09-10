"""Stage 3 (planning) runner."""

from __future__ import annotations

import argparse
from typing import Any

from ._shared import _console
from .judgment import _judge_stage

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
    shared_host_decisions: "list[dict[str, Any]] | None" = None,
) -> "tuple[dict[str, Any] | None, bool, bool]":
    """Run Stage 3 (planning) or skip it when resuming from a later stage.

    *shared_host_decisions* (issue #274) is the already-resolved list from
    :func:`_resolve_shared_host_decisions` — this only threads it into the
    config Stage 3 consumes (``stage3_planning.py`` never makes this
    decision itself, see that module's docstring).

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
    from ...llm_judge import get_judge_if_headless
    from ...llm_judge.harness import is_harness_active
    from ...pipeline.stage3_planning import run as stage3_run
    from ...pipeline.state import StageName

    try:
        judge_configured = get_judge_if_headless() is not None
    except ValueError:
        judge_configured = False
    # issue #310: a real `/rvv-miniputt:run` invocation is driven by an
    # interactive harness (Claude Code, Pi, OpenCode, ...), which means
    # `get_judge_if_headless()` is *always* None there -- a harness supplies
    # its own in-session judgment instead of a headless LLM judge. The
    # decision-driven canonical path is therefore active whenever *either*
    # a headless judge or a harness session is present, not only the former:
    # gating cheap_baseline on `judge_configured` alone meant every real
    # interactive run silently fell back to the expensive legacy/global
    # SeasonPlanner path this flag exists to skip.
    decision_driven_path = judge_configured or is_harness_active()

    if resume_from <= 3:
        _console.print("[bold]Stage 3:[/bold] Sesongplanlegging...")
        try:
            # Inject penalty hints from a previous failed attempt into config
            merged_cfg = dict(cfg)
            merged_cfg["allow_penalty_hint_relaxation"] = not judge_configured
            if judge_configured:
                log_fn("Stage 3: penalty-hint threshold relaxation disabled (headless judge configured)")
            # issue #265 P1 / issue #310: on the canonical decision-driven
            # path (headless judge OR interactive harness), the v2 optimizer
            # (stage3_optimizer) will search this baseline anyway, so
            # SeasonPlanner only needs to produce a cheap feasible seed --
            # not its own second, globally optimized date schedule. Only a
            # genuinely headless, judge-less run (no harness, no configured
            # judge -- cron/CI style) keeps the fuller legacy behavior.
            merged_cfg.setdefault("stage3_cheap_baseline", decision_driven_path)
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
            if shared_host_decisions:
                # Same list doubles as both the chosen_club lookup and the
                # provenance record — see
                # stage3_planning._shared_host_choices_from_config.
                merged_cfg["shared_host_decisions"] = list(shared_host_decisions)
                merged_cfg["shared_host_decision_provenance"] = list(shared_host_decisions)
            from time import perf_counter

            from ...pipeline.run_manifest import RunManifest

            _baseline_started = perf_counter()
            plan = stage3_run(merged_cfg, scraping, state, start, end, strict=strict, iterations=iterations or getattr(args, "iterations", 1))
            try:
                RunManifest(state.work_dir).record_timing(
                    "stage3_baseline_seconds", perf_counter() - _baseline_started
                )
            except Exception as exc:
                log_fn(f"Stage 3: could not record baseline timing: {exc}")
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
        # issue #290: a checkpoint file existing on disk is not proof it
        # belongs to *this* run's Stage 3 decision -- ``write_stage(...,
        # status=DONE)`` for Stage 1/2 already marks every downstream
        # checkpoint (including this one) ``stale`` via
        # ``PipelineState._invalidate_downstream`` whenever an earlier
        # stage actually (re)ran in this invocation/run. A harness/resume
        # sequencing mistake that reaches Stage 4 without Stage 3 ever
        # having run fresh in this run must not silently export that stale
        # data -- it must be treated the same as a missing checkpoint.
        # Once Stage 3 actually runs (the ``if resume_from <= 3`` branch
        # above), its ``write_stage(..., status=DONE)`` call replaces the
        # envelope wholesale and clears this flag, so a legitimate
        # multi-invocation resume within the same logical run is
        # unaffected.
        if state.is_stale(StageName.PLANNING):
            _console.print(
                "[red]✗[/red] Stage 3-checkpointet er foreldet (stale) -- det tilhører "
                "ikke denne kjørens fullførte planleggingsbeslutning. Gjenoppta fra "
                "Stage 3 (--resume-from 3) i stedet for å eksportere gammel tilstand."
            )
            log_fn("Stage 3 skip aborted: on-disk checkpoint is stale for this run")
            return None, True, False
        plan = state.read_stage(StageName.PLANNING)
        if not plan:
            _console.print("[red]✗[/red] Kan ikke gjenoppta: Stage 3-checkpoint mangler.")
            return None, True, False
        _console.print("[bold]Stage 3:[/bold] Hoppet over (gjenopptatt)")
        log_fn("Stage 3 skipped via --resume-from")

    return plan, False, False
