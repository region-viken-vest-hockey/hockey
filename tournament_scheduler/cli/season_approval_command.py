"""Season approval and banned-date command handlers (``season approve``, ``unapprove``, ``banned-dates``)."""

from __future__ import annotations

import argparse

from rich.console import Console

_console = Console()


def _cmd_season_approve(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season approve`` — approve a tournament."""
    from ..season_state import approve_tournament
    from .verification_problem import _canonical_verification_problem

    result = approve_tournament(
        season=args.season,
        tournament_id=args.tournament_id,
        actor=args.actor,
        note=args.note,
        placement_locked=args.placement_locked,
        participants_locked=args.participants_lock,
        root=args.root,
        problem=_canonical_verification_problem(args.work_dir, args.season, args.root),
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[green]✓[/green] Approved tournament {args.tournament_id} for {args.season}"
            f" (request {result.get('request_id')})"
        )
    return 0


def _cmd_season_unapprove(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season unapprove`` — unapprove a tournament."""
    from ..season_state import unapprove_tournament

    result = unapprove_tournament(
        season=args.season,
        tournament_id=args.tournament_id,
        actor=args.actor,
        note=args.note,
        root=args.root,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[green]✓[/green] Unapproved tournament {args.tournament_id} for {args.season}"
            f" (request {result.get('request_id')})"
        )
    return 0


def _cmd_season_banned_dates(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season banned-dates`` — list banned dates."""
    from ..season_state import banned_date_report

    result = banned_date_report(
        season=args.season,
        root=args.root,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        banned_dates = result.get("banned_dates", [])
        violations = result.get("violations", [])
        if banned_dates:
            _console.print(f"[green]✓[/green] Found {len(banned_dates)} banned date(s) for {args.season}")
            for bd in banned_dates:
                _console.print(f"  {bd.get('date')} (request {bd.get('request_id')})")
        else:
            _console.print(f"[green]✓[/green] No banned dates found for {args.season}")
        if violations:
            _console.print(f"[yellow]⚠[/yellow] {len(violations)} tournament(s) violate banned dates:")
            for v in violations:
                _console.print(f"  {v.get('tournament_id')} on {v.get('date')}")
    return 0
