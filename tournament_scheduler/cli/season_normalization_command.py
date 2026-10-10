"""Season placement and arena identity normalization command handlers."""

from __future__ import annotations

import argparse

from rich.console import Console

_console = Console()


def _cmd_season_normalize_placements(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season normalize-placements`` — normalize placements."""
    from ..season_state import normalize_placements

    result = normalize_placements(
        season=args.season,
        root=args.root,
        actor=args.actor,
        note=args.note,
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(f"[green]✓[/green] Normalized placements for season {args.season}")
        if result.get("changes_made"):
            _console.print(f"  Changes made: {result['changes_made']}")
    return 0


def _cmd_season_normalize_arena_identities(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season normalize-arena-identities`` — normalize arena identities."""
    from ..season_state import normalize_arena_identities

    result = normalize_arena_identities(
        season=args.season,
        root=args.root,
        actor=args.actor,
        note=args.note,
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(f"[green]✓[/green] Normalized arena identities for season {args.season}")
        if result.get("changes_made"):
            _console.print(f"  Changes made: {result['changes_made']}")
    return 0
