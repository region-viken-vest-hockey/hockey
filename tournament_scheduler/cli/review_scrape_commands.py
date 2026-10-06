"""
Review and scrape command implementations for the RVV Miniputt CLI.

This module contains the implementations of the `rvv-miniputt review` and
`rvv-miniputt scrape-llm` commands.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from ..pipeline.state import PipelineState

from rich.console import Console

_console = Console()


def _cmd_review(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt review`` — review tournament details."""
    from ..pipeline.state import PipelineState
    from ..review.main import run as run_review

    state = PipelineState(args.work_dir)
    result = run_review(
        season=args.season,
        root=args.root,
        out_dir=args.out_dir,
        template=args.template,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[green]✓[/green] Generated review/ status"
        )
        if result.get("review_file"):
            _console.print(f"  review: {result['review_file']}")
        if result.get("teams_evaluated"):
            _console.print(f"  teams evaluated: {result['teams_evaluated']}")
    return 0


def _cmd_scrape_llm(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt scrape-llm`` — scrape using LLM assistance."""
    from ..pipeline.state import PipelineState
    from ..pipeline.scrape_llm import run as run_scrape_llm

    state = PipelineState(args.work_dir)
    result = run_scrape_llm(
        season=args.season,
        root=args.root,
        out_dir=args.out_dir,
        model=args.model,
        prompt=args.prompt,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if result.get("success"):
            _console.print(f"[green]✓[/green] LLM scrape completed")
            if result.get("teams_found"):
                _console.print(f"  teams found: {result['teams_found']}")
        else:
            _console.print(f"[red]✗[/red] LLM scrape failed: {result.get('error')}")
    return 0