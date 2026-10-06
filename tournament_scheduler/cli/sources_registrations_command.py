"""
Sources and registrations command implementations for the RVV Miniputt CLI.

This module contains the implementations of the `rvv-miniputt sources` and
`rvv-miniputt registrations` commands.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from ..pipeline.state import PipelineState

from rich.console import Console

_console = Console()


def _cmd_sources(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt sources`` — show source status."""
    from ..pipeline.state import PipelineState
    from ..sources_status import main as run_sources_status

    state = PipelineState(args.work_dir)
    result = run_sources_status(
        season=args.season,
        root=args.root,
        verbose=args.verbose,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if result.get("sources"):
            _console.print("[bold]Source status[/bold]")
            for source in result["sources"]:
                status = "✓" if source.get("ok") else "✗"
                _console.print(
                    f"  [{status}] {source.get('name', source.get('url'))}"
                )
                if not source.get("ok") and source.get("error"):
                    _console.print(f"    [red]Error:[/red] {source['error']}")
        else:
            _console.print("[dim]No sources configured[/dim]")
    return 0


def _cmd_registrations(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt registrations`` — show registration status."""
    from ..pipeline.state import PipelineState
    from ..registrations import main as run_registrations

    state = PipelineState(args.work_dir)
    result = run_registrations(
        season=args.season,
        root=args.root,
        out_dir=args.out_dir,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[green]✓[/green] Generated registrations/ status"
        )
        if result.get("teams_file"):
            _console.print(f"  teams: {result['teams_file']}")
        if result.get("registered_count"):
            _console.print(f"  registered teams: {result['registered_count']}")
    return 0