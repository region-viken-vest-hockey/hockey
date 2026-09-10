"""Stage 1 (config) runner and the post-Stage-3 approval gate."""

from __future__ import annotations

import argparse
from typing import Any

from rich.console import Console

from ._shared import _console
from .judgment import _judge_stage

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
    from ..plan_critic import generate_critic_summary

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
    from ...pipeline.stage1_config import load_effective_config, run as stage1_run
    from ...pipeline.state import StageName

    if resume_from <= 1:
        _console.print("[bold]Stage 1:[/bold] Konfigurasjon...")
        try:
            from time import perf_counter

            from ...pipeline.run_manifest import RunManifest

            _started = perf_counter()
            stage1_run(args.input, state, strict=strict)
            try:
                RunManifest(state.work_dir).record_timing("stage1_seconds", perf_counter() - _started)
            except Exception as exc:
                log_fn(f"Stage 1: could not record timing: {exc}")
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
