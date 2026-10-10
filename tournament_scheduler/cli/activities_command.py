"""
Activities command implementation for the RVV Miniputt CLI.

This module contains the implementation of the `rvv-miniputt activities` command
for regenerating activities from the Årshjul workbook.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from rich.console import Console

_console = Console()


def _cmd_activities(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt activities`` — regenerate activities/ from Årshjul workbook."""
    from ..pipeline.state import PipelineState
    from ..aktivitetskalender import main as run_aktivitetskalender

    state = PipelineState(args.work_dir)
    result = run_aktivitetskalender(
        workbook=args.workbook,
        out_dir=args.out_dir,
        season=args.season,
        root=args.root,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[green]✓[/green] Generated activities/ from {args.workbook}"
        )
        if result.get("activity_count"):
            _console.print(f"  activities: {result['activity_count']}")
    return 0