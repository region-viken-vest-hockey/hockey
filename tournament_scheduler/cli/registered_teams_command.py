"""
Registered teams command implementation for the RVV Miniputt CLI.

This module contains the implementation of the `rvv-miniputt registered-teams` command
for generating the Påmeldte lag page.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from rich.console import Console

_console = Console()


def _cmd_registered_teams(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt registered-teams`` — build the Påmeldte lag page from a registrations CSV."""
    import json as _json
    from pathlib import Path

    from ..pipeline.registered_teams import (
        RegisteredTeamsPublishError,
        RegisteredTeamsValidationError,
        prepare_registered_teams_latest_export,
    )

    if not args.csv:
        _console.print("[red]✗[/red] --csv er påkrevd: sti til påmeldingseksporten")
        return 1
    if args.publish:
        _console.print(
            "[red]✗[/red] Publisering av påmeldte lag er ikke koblet til denne kommandoen. "
            "Kjør uten --publish for lokal forhåndsvisning."
        )
        return 2

    config_path = args.config if args.config and Path(args.config).exists() else None
    try:
        result = prepare_registered_teams_latest_export(
            csv_path=args.csv,
            export_dir=args.export_dir,
            repo_dir=args.repo_dir,
            branch=args.branch,
            config_path=config_path,
            generated_at=args.generated_at,
            include_latest_base=args.base_latest,
            require_latest_base=args.base_latest,
        )
    except (RegisteredTeamsValidationError, RegisteredTeamsPublishError) as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    if args.json:
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        html_path = result["registered_team_files"]["registered_teams_html"]
        _console.print(f"[green]✓[/green] Generated {html_path}")
        _console.print("  Ikke publisert (lokal forhåndsvisning)")
    return 0