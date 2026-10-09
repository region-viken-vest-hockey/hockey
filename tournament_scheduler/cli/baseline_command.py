"""
Baseline command implementation for the RVV Miniputt CLI.

This module contains the implementation of the `rvv-miniputt season baseline` command.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from rich.console import Console

_console = Console()


def _cmd_season_baseline(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season baseline`` — baseline management commands."""
    from ..season_state import (
        season_baseline_advance,
        season_baseline_create,
        season_baseline_replace,
        season_baseline_show,
    )

    if args.baseline_command == "create":
        result = season_baseline_create(
            season=args.season, root=args.root, actor=args.actor, note=args.note
        )
    elif args.baseline_command == "replace":
        result = season_baseline_replace(
            season=args.season, root=args.root, actor=args.actor, note=args.note
        )
    elif args.baseline_command == "advance":
        result = season_baseline_advance(
            season=args.season, root=args.root, actor=args.actor, note=args.note
        )
    elif args.baseline_command == "show":
        result = season_baseline_show(season=args.season, root=args.root)
    else:
        _console.print("[red]✗[/red] Missing baseline command (create/show/advance/replace)")
        return 2
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        comparison = result.get("comparison") or {}
        baseline = result.get("baseline") or {}
        _console.print(
            f"[bold]Season baseline {args.season}[/bold] revision {str(result.get('revision') or result.get('canonical_state_revision') or '')[:12]}"
        )
        if baseline:
            _console.print(
                f"  accepted {baseline.get('created_at')} by {baseline.get('created_by')} "
                f"({baseline.get('finding_count', 0)} findings)"
            )
        summary = comparison.get("summary") or {}
        for status in ("NEW", "REGRESSED", "IMPROVED", "RESOLVED", "KNOWN"):
            _console.print(f"  {status:<9} {int(summary.get(status, 0))}")
        if comparison.get("ok_to_advance"):
            _console.print("[green]No NEW or REGRESSED findings relative to baseline.[/green]")
        else:
            _console.print("[yellow]Baseline has NEW or REGRESSED findings.[/yellow]")
    return 0