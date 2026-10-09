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
    # If no subcommand provided, return error
    if not getattr(args, 'sources_command', None):
        return 1
    
    # Ensure required attributes exist with defaults
    if not hasattr(args, 'work_dir'):
        args.work_dir = '.pipeline'
    
    from .rvv_cli import _cmd_sources_status
    return _cmd_sources_status(args)


def _cmd_registrations(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt registrations`` — handle registrations subcommands."""
    # If no subcommand provided, return error
    if not getattr(args, 'registrations_command', None):
        return 1

    if args.registrations_command == "validate":
        from ..registrations import validate_registrations
        result = validate_registrations(
            source_path=args.source,
            input_path=args.input,
        )
        if args.json:
            import json as _json
            print(_json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            if result.get("ok"):
                _console.print(f"[green]✓[/green] Validation passed")
                if result.get("teams_file"):
                    _console.print(f"  teams file: {result['teams_file']}")
                if result.get("registered_count") is not None:
                    _console.print(f"  registered teams: {result['registered_count']}")
            else:
                _console.print(f"[red]✗[/red] Validation failed")
                if result.get("error"):
                    _console.print(f"  error: {result['error']}")
        return 0

    elif args.registrations_command == "export":
        from ..registrations import export_registrations
        result = export_registrations(
            source_path=args.source,
            input_path=args.input,
            output_path=args.output,
            dry_run=getattr(args, 'dry_run', False),
        )
        if args.json:
            import json as _json
            print(_json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            if result.get("ok"):
                _console.print(f"[green]✓[/green] Export successful")
                if result.get("teams_file"):
                    _console.print(f"  teams file: {result['teams_file']}")
                if result.get("registered_count") is not None:
                    _console.print(f"  registered teams: {result['registered_count']}")
                if result.get("changes_made"):
                    _console.print(f"  changes made: {result['changes_made']}")
            else:
                _console.print(f"[red]✗[/red] Export failed")
                if result.get("error"):
                    _console.print(f"  error: {result['error']}")
        return 0

    else:
        _console.print(f"[red]✗[/red] Unknown registrations subcommand: {args.registrations_command}")
        return 1