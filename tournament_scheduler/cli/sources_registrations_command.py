"""
Sources and registrations command implementations for the RVV Miniputt CLI.

This module contains the implementations of the `rvv-miniputt sources` and
`rvv-miniputt registrations` commands.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

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

    from ..registrations import (
        RegistrationImportError,
        export_registrations,
        format_registration_summary,
        validate_registrations,
    )

    if args.registrations_command == "validate":
        try:
            result = validate_registrations(source_path=args.source, input_path=args.input)
        except RegistrationImportError as exc:
            _console.print(f"[red]✗[/red] {exc}")
            return 1
    elif args.registrations_command == "export":
        try:
            result = export_registrations(
                args.source,
                input_path=args.input,
                output_path=args.output,
                dry_run=bool(getattr(args, "dry_run", False)),
            )
        except RegistrationImportError as exc:
            _console.print(f"[red]✗[/red] {exc}")
            return 1
    else:
        _console.print(f"[red]✗[/red] Unknown registrations subcommand: {args.registrations_command}")
        return 1

    if getattr(args, "json", False):
        import json as _json

        print(_json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(format_registration_summary(result))
    return 0