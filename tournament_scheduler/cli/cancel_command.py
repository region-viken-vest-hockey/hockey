"""
Cancel command implementation for the RVV Miniputt CLI.

This module contains the implementation of the `rvv-miniputt cancel` command
for cancelling tournaments and suggesting/rescheduling makeup dates.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from ..pipeline.state import PipelineState

from rich.console import Console

_console = Console()


def _cmd_cancel(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt cancel`` — cancellation and rain-check workflow."""
    from ..pipeline.state import PipelineState
    from ..pipeline.cancellation_workflow import CancellationWorkflow

    work_dir = args.work_dir
    state = PipelineState(work_dir)
    wf = CancellationWorkflow(state)

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
            "\nBruk [bold]rvv-miniputt cancel --tournament-id <id> --reason \"...\"[/bold]"
        )
        return 0

    tid = args.tournament_id

    # --- Cancel the tournament ---
    try:
        tournament = wf._find_tournament(plan, tid)
    except ValueError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    if args.reason:
        reason = args.reason
    else:
        _console.print(
            f"[bold]Avlys turnering {tid}[/bold] "
            f"({tournament.age_group}, {tournament.arena}, {tournament.date.isoformat()})"
        )
        reason = _console.input("  Årsak: ").strip()
        if not reason:
            _console.print("[red]✗[/red] Avbrutt — ingen grunn oppgitt.")
            return 1

    cancel_result = wf.mark_cancelled(tid, reason, plan=plan)

    if not cancel_result.success:
        _console.print(f"[yellow]⚠[/yellow] {cancel_result.summary_nb}")
        return 1

    _console.print(f"[green]✓[/green] {cancel_result.summary_nb}")

    # --- Write the plan checkpoint ---
    wf.write_plan(plan, log_entry=cancel_result)
    wf.log_cancellation(cancel_result)

    # --- Handle makeup date ---
    if args.makeup_date:
        try:
            new_date = datetime.strptime(args.makeup_date, "%Y-%m-%d").date()
        except ValueError:
            _console.print(
                f"[red]✗[/red] Ugyldig datoformat '{args.makeup_date}'. Bruk YYYY-MM-DD."
            )
            return 1

        _console.print(f"\n[bold]Flytter til makeup-dato: {new_date.isoformat()}[/bold]")
        move_result = wf.apply_makeup(
            tid, new_date, plan=plan, force=args.force, cascade=True
        )

        if not move_result.success:
            _console.print(f"[red]✗[/red] {move_result.summary_nb}")
            return 1

        _console.print(f"[green]✓[/green] {move_result.summary_nb}")
        wf.write_plan(plan, log_entry=move_result)
    else:
        # Show suggested makeup dates
        _console.print("\n[bold]Foreslåtte makeup-datoer:[/bold]")
        suggestions = wf.suggest_makeup_dates(tournament, plan)

        if not suggestions:
            _console.print(
                "  [dim]Ingen ledige helger funnet i sesongvinduet.[/dim]"
            )
        else:
            for s in suggestions:
                day_nb = ["man", "tir", "ons", "tor", "fre", "lør", "søn"]
                day = day_nb[s.date.weekday()]
                delta = f"+{s.days_from_original}d" if s.days_from_original >= 0 else f"{s.days_from_original}d"
                _console.print(
                    f"  [cyan]{s.date.isoformat()}[/cyan] ({day}, {delta})"
                )
                for c in s.conflicts:
                    _console.print(f"    [dim]Advarsel: {c['reason']}[/dim]")

            _console.print(
                f"\nBruk [bold]rvv-miniputt cancel --tournament-id {tid} "
                f"--makeup-date <dato>[/bold] for å velge en makeup-dato."
            )

    # --- Re-export ---
    if not args.no_export:
        _console.print("\n[bold]Re-eksporterer...[/bold]")
        try:
            export_result = wf.re_export(
                export_dir=args.export_dir,
            )
            files = export_result.get("output_files", {})
            _console.print(f"  [green]✓[/green] {len(files)} fil(er) eksportert")
            for label, path in files.items():
                _console.print(f"    → {path}")
        except Exception as exc:
            _console.print(f"  [red]✗[/red] Eksport feilet: {exc}")
            return 1

    _console.print("\n[bold green]✓ Ferdig.[/bold green]")
    return 0