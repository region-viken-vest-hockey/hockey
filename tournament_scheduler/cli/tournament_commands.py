"""
Tournament command implementations for the RVV Miniputt CLI.

This module contains the implementations of the `rvv-miniputt tournament` command
and its subcommands for managing tournaments.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from ..pipeline.state import PipelineState

from rich.console import Console

_console = Console()


def _cmd_tournament(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt tournament`` — tournament management subcommands."""
    if args.tournament_command == "list":
        return _cmd_tournament_list(args)
    elif args.tournament_command == "add":
        return _cmd_tournament_add(args)
    elif args.tournament_command == "remove":
        return _cmd_tournament_remove(args)
    else:
        _console.print("[red]✗[/red] Missing tournament subcommand")
        return 1


def _cmd_tournament_list(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt tournament list`` — list all tournaments."""
    from ..pipeline.state import PipelineState

    state = PipelineState(args.work_dir)
    try:
        plan = state.load_plan()
    except ValueError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    if args.json:
        import json as _json

        # Export tournaments as JSON
        tournaments_data = []
        for t in plan.tournaments:
            tournaments_data.append({
                "id": t.id,
                "date": t.date.isoformat(),
                "age_group": t.age_group,
                "arena": t.arena,
                "teams": [{"club": team.club, "label": team.label} for team in t.teams],
                "cancelled": t.cancelled,
                "cancellation_reason": t.cancellation_reason,
            })
        print(_json.dumps(tournaments_data, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print("[bold]Turneringer i sesongplanen:[/bold]")
        for t in plan.tournaments:
            status = ""
            if t.cancelled:
                status = f" [red](AVLYST: {t.cancellation_reason or 'ingen grunn'})[/red]"
            _console.print(
                f"  [cyan]{t.id}[/cyan]  {t.date.isoformat()}  "
                f"{t.age_group:5s}  {t.arena:20s}  "
                f"{len(t.teams)} lag{status}"
            )
    return 0


def _cmd_tournament_add(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt tournament add`` — add a new tournament."""
    from ..pipeline.state import PipelineState
    from ..pipeline.tournament_updater import TournamentAdder

    state = PipelineState(args.work_dir)
    adder = TournamentAdder(state)

    # Validate that the tournament doesn't already exist
    try:
        plan = state.load_plan()
        for t in plan.tournaments:
            if t.id == args.tournament_id:
                _console.print(f"[red]✗[/red] Tournament {args.tournament_id} already exists")
                return 1
    except ValueError:
        # If we can't load the plan, we'll create a new one
        plan = type('Plan', (), {'tournaments': []})()

    # Create the new tournament
    from ..schedule import Tournament
    new_tournament = Tournament(
        id=args.tournament_id,
        date=args.date,
        age_group=args.age_group,
        arena=args.arena,
        teams=[],
    )

    # Add the tournament to the plan
    plan.tournaments.append(new_tournament)

    # Save the updated plan
    state.write_plan(plan)
    _console.print(f"[green]✓[/green] Added tournament {args.tournament_id}")
    return 0


def _cmd_tournament_remove(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt tournament remove`` — remove a tournament."""
    from ..pipeline.state import PipelineState

    state = PipelineState(args.work_dir)
    try:
        plan = state.load_plan()
    except ValueError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    # Find and remove the tournament
    original_count = len(plan.tournaments)
    plan.tournaments = [t for t in plan.tournaments if t.id != args.tournament_id]
    removed_count = original_count - len(plan.tournaments)

    if removed_count == 0:
        _console.print(f"[red]✗[/red] Tournament {args.tournament_id} not found")
        return 1

    # Save the updated plan
    state.write_plan(plan)
    _console.print(f"[green]✓[/green] Removed tournament {args.tournament_id}")
    return 0