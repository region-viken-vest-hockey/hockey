"""
Registered teams command implementation for the RVV Miniputt CLI.

This module contains the implementation of the `rvv-miniputt registered-teams` command
for generating the Påmeldte lag page.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from ..pipeline.state import PipelineState

from rich.console import Console

_console = Console()


def _cmd_registered_teams(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt registered-teams`` — Påmeldte lag page."""
    from ..pipeline.state import PipelineState
    from ..pipeline.registered_teams import run as run_registered_teams

    state = PipelineState(args.work_dir)
    result = run_registered_teams(
        season=args.season,
        root=args.root,
        out_dir=args.out_dir,
        template=args.template,
        wage_cost_per_hour=args.wage_cost_per_hour,
        num_workers=args.num_workers,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[green]✓[/green] Generated {args.out_dir}/index.html"
        )
        if result.get("teams_file"):
            _console.print(f"  teams: {result['teams_file']}")
    return 0