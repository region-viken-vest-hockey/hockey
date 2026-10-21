"""``rvv-miniputt season move-candidates``: read-only tournament-move discovery.

Discovery proposes same-host free-ice dates through the shared move preview.
It never writes; applying a candidate stays with ``season move``.
"""

from __future__ import annotations

import argparse

from ..application.canonical_season.move_search import search_tournament_moves
from ..application.canonical_season_service import CanonicalSeasonService
from ..infrastructure.canonical_season_store import SeasonStateError
from .season_canonical_commands import _console, _emit_json


def _cmd_season_move_candidates(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season move-candidates``; never writes canonical state."""
    try:
        result = search_tournament_moves(
            CanonicalSeasonService(root=args.root),
            season=args.season,
            tournament_ids=list(args.tournament_ids),
        )
    except (ValueError, SeasonStateError) as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 2
    if args.json:
        _emit_json(result)
    else:
        _console.print(
            f"move candidates for {args.season}: {result['candidate_count']} evaluated, "
            f"{result['safe_candidate_count']} safe_to_apply (revision {result['canonical_state_revision']})"
        )
        if result["empty_reason"]:
            _console.print(f"  no candidates: {result['empty_reason']}")
        for item in result["candidates"]:
            _console.print(
                f"  {item['tournament_id']} {item['date']} {item['start_time']} {item['arena']}: "
                f"{item['verdict']['status']}"
            )
    return 0
