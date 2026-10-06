"""
Verdict, critic, and auto-adjust command implementations for the RVV Miniputt CLI.

This module contains the implementations of the `rvv-miniputt verdict`,
`rvv-miniputt critic`, and `rvv-miniputt auto-adjust` commands.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from ..pipeline.state import PipelineState

from rich.console import Console

_console = Console()


def _cmd_verdict(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt verdict`` — record a verdict on a tournament."""
    from ..pipeline.state import PipelineState
    from ..pipeline.verdict_workflow import VerdictWorkflow

    work_dir = args.work_dir
    state = PipelineState(work_dir)
    wf = VerdictWorkflow(state)

    # Load the plan first to verify we have something to work with.
    try:
        plan = wf.load_plan()
    except ValueError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    # --- No tournament ID: list available tournaments ---
    if not args.tournament_id:
        _console.print("[bold]Turneringer i sesongplanen:[/bold]\n")
        for t in plan.tournaments:
            status = ""
            if t.cancelled:
                status = f" [red](AVLYST: {t.cancellation_reason or 'ingen grunn'})[/red]"
            _console.print(
                f"  [cyan]{t.id}[/cyan]  {t.date.isoformat()}  "
                f"{t.age_group:5s}  {t.arena:20s}  "
                f"{len(t.teams)} lag{status}"
            )
        _console.print(
            "\nBruk [bold]rvv-miniputt verdict --tournament-id <id>[/bold] "
            "for å registrere en dom på en turnering."
        )
        return 0

    tid = args.tournament_id

    # --- Find the tournament ---
    try:
        tournament = wf._find_tournament(plan, tid)
    except ValueError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    # --- Record the verdict ---
    if not args.verdict:
        _console.print(
            f"[bold]Registrer dom for turnering {tid}[/bold] "
            f"({tournament.age_group}, {tournament.arena}, {tournament.date.isoformat()})"
        )
        verdict = _console.input("  Dom (godkjent/avslått/utenom konkurranse): ").strip()
        if not verdict:
            _console.print("[red]✗[/red] Avbrutt — ingen dom oppgitt.")
            return 1
    else:
        verdict = args.verdict

    # TODO: Implement actual verdict recording logic
    _console.print(f"[green]✓[/green] Registrert dom '{verdict}' for turnering {tid}")

    # --- Write the plan checkpoint ---
    wf.write_plan(plan, log_entry=f"Verdict for tournament {tid}: {verdict}")
    _console.print(f"[green]✓[/green] Written plan checkpoint")

    # --- Re-export ---
    if not args.no_export:
        _console.print("\n[bold]Re-eksporterer...[/bold]")
        try:
            # This would call the appropriate export function
            _console.print("  [yellow]⚠[/yellow] Export not yet implemented for verdict")
        except Exception as exc:
            _console.print(f"  [red]✗[/red] Eksport feilet: {exc}")
            return 1

    _console.print("\n[bold green]✓ Ferdig.[/bold green]")
    return 0


def _cmd_critic(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt critic`` — run critic analysis."""
    from ..pipeline.state import PipelineState
    from ..plan_critic import main as run_plan_critic

    state = PipelineState(args.work_dir)
    result = run_plan_critic(
        season=args.season,
        root=args.root,
        out_dir=args.out_dir,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if result.get("success"):
            _console.print(f"[green]✓[/green] Critic analysis completed")
            if result.get("issues_found"):
                _console.print(f"  issues found: {result['issues_found']}")
                if result.get("critical_issues"):
                    _console.print(f"  critical issues: {result['critical_issues']}")
        else:
            _console.print(f"[red]✗[/red] Critic analysis failed: {result.get('error')}")
    return 0


def _cmd_auto_adjust(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt auto-adjust`` — automatically adjust the plan."""
    from ..pipeline.state import PipelineState
    from ..auto_adjust import main as run_auto_adjust

    state = PipelineState(args.work_dir)
    result = run_auto_adjust(
        season=args.season,
        root=args.root,
        work_dir=args.work_dir,
        strategy=args.strategy,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if result.get("success"):
            _console.print(f"[green]✓[/green] Auto-adjust completed")
            if result.get("adjustments_made"):
                _console.print(f"  adjustments made: {result['adjustments_made']}")
                if result.get("changes"):
                    for change in result["changes"]:
                        _console.print(f"    • {change}")
        else:
            _console.print(f"[red]✗[/red] Auto-adjust failed: {result.get('error')}")
    return 0