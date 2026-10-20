"""``rvv-miniputt season swap-*``: participant-swap transport.

``swap-candidates`` is read-only discovery over the shared swap preview.
``swap-participants`` is the canonical write; it takes the reviewed revision.
"""

from __future__ import annotations

import argparse

from ..application.canonical_season.swap_search import search_participant_swaps
from ..application.canonical_season_service import CanonicalSeasonService
from ..infrastructure.canonical_season_store import SeasonStateError
from .season_canonical_commands import (
    _console,
    _dry_run_label,
    _emit_json,
    _split_regression_acceptances,
    _verification_problem,
)


def _cmd_season_swap_participants(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season swap-participants``."""
    from ..application.canonical_season.store_facade import swap_participants

    result = swap_participants(
        season=args.season,
        tournament_a_id=args.tournament_a,
        problem=_verification_problem(args),
        team_a_label=args.team_a,
        tournament_b_id=args.tournament_b,
        team_b_label=args.team_b,
        root=args.root,
        actor=args.actor,
        note=args.note,
        dry_run=args.dry_run,
        request_id=args.request_id,
        accept_regressions=_split_regression_acceptances(args.accept_team_regressions),
        accept_regression_reason=args.accept_regression_reason,
        expected_revision=args.expected_revision,
    )
    if args.json:
        _emit_json(result)
    else:
        _console.print(
            f"[green]✓[/green] {_dry_run_label(args)} swap of {args.team_a} and {args.team_b} for {args.season}"
        )
    return 0


def _cmd_season_swap_candidates(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season swap-candidates``; never writes canonical state."""
    try:
        result = search_participant_swaps(
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
            f"swap candidates for {args.season}: {result['candidate_count']} evaluated, "
            f"{result['safe_candidate_count']} safe_to_apply (revision {result['canonical_state_revision']})"
        )
        if result["empty_reason"]:
            _console.print(f"  no candidates: {result['empty_reason']}")
        for item in result["candidates"]:
            _console.print(
                f"  {item['tournament_a_id']} {item['team_a_label']} <-> "
                f"{item['tournament_b_id']} {item['team_b_label']}: {item['verdict']['status']}"
            )
    return 0
