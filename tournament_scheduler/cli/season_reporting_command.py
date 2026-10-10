"""Read-only season history, inspection, placement, decision-ledger and guest-slot report handlers."""

from __future__ import annotations

import argparse

from rich.console import Console

_console = Console()


def _cmd_season_history(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season history`` — show season history."""
    from ..season_state import history_inventory

    result = history_inventory(
        season=args.season,
        root=args.root,
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        entries = result.get("entries", [])
        if entries:
            _console.print(f"[green]✓[/green] History for season {args.season}:")
            for entry in entries:
                _console.print(f"  {entry.get('timestamp')}: {entry.get('description')}")
        else:
            _console.print(f"[green]✓[/green] No history entries for season {args.season}")
    return 0


def _cmd_season_compact_history(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season compact-history`` — show compact season history."""
    from ..season_state import compact_history

    result = compact_history(
        season=args.season,
        root=args.root,
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        entries = result.get("entries", [])
        if entries:
            _console.print(f"[green]✓[/green] Compact history for season {args.season}:")
            for entry in entries:
                _console.print(f"  {entry.get('timestamp')}: {entry.get('description')}")
        else:
            _console.print(f"[green]✓[/green] No compact history entries for season {args.season}")
    return 0


def _cmd_season_tourney_inspection(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season tourney-inspection`` — inspect tournament details."""
    from ..season_state import tournament_inspection

    result = tournament_inspection(
        season=args.season,
        tournament_id=args.tournament_id,
        root=args.root,
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if result:
            _console.print(f"[green]✓[/green] Inspection for tournament {args.tournament_id} in season {args.season}:")
            for key, value in result.items():
                _console.print(f"  {key}: {value}")
        else:
            _console.print(f"[yellow]⚠[/yellow] No inspection data found for tournament {args.tournament_id}")
    return 0


def _cmd_season_placement_infeasibility(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season placement-infeasibility`` — show placement infeasibility details."""
    from ..season_state import placement_infeasibility_report

    result = placement_infeasibility_report(
        season=args.season,
        root=args.root,
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if result:
            _console.print(f"[green]✓[/green] Placement infeasibility report for season {args.season}:")
            for key, value in result.items():
                _console.print(f"  {key}: {value}")
        else:
            _console.print(f"[yellow]⚠[/yellow] No placement infeasibility data for season {args.season}")
    return 0


def _cmd_season_decision_ledger(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season decision-ledger`` — show decision ledger."""
    from ..season_state import change_request_ledger

    result = change_request_ledger(
        season=args.season,
        root=args.root,
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        entries = result.get("entries", [])
        if entries:
            _console.print(f"[green]✓[/green] Decision ledger for season {args.season}:")
            for entry in entries:
                _console.print(f"  {entry.get('timestamp')}: {entry.get('description')}")
        else:
            _console.print(f"[green]✓[/green] No decision ledger entries for season {args.season}")
    return 0


def _cmd_season_guest_slot_report(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season guest-slot-report`` — show guest slot report."""
    from ..season_state import guest_slot_report

    result = guest_slot_report(
        season=args.season,
        root=args.root,
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        report = result.get("report", {})
        if report:
            _console.print(f"[green]✓[/green] Guest slot report for season {args.season}:")
            for key, value in report.items():
                _console.print(f"  {key}: {value}")
        else:
            _console.print(f"[yellow]⚠[/yellow] No guest slot data for season {args.season}")
    return 0


def _cmd_season_inspect_tournament(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season inspect tournament`` — inspect tournament details."""
    from ..season_state import tournament_inspection

    result = tournament_inspection(
        season=args.season,
        tournament_id=args.tournament_id,
        root=args.root,
        include_released_constraints=getattr(args, 'all', False),
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if result:
            _console.print(f"[green]✓[/green] Inspection for tournament {args.tournament_id} in season {args.season}:")
            for key, value in result.items():
                _console.print(f"  {key}: {value}")
        else:
            _console.print(f"[yellow]⚠[/yellow] No inspection data found for tournament {args.tournament_id}")
    return 0


def _cmd_season_inspect_constraints(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season inspect constraints`` — inspect request constraints."""
    from ..season_state import constraint_inspection

    result = constraint_inspection(
        season=args.season,
        team=args.team,
        tournament_id=args.tournament_id,
        date=args.date,
        include_released=getattr(args, 'all', False),
        root=args.root,
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if result:
            _console.print(f"[green]✓[/green] Constraint inspection for season {args.season}:")
            for key, value in result.items():
                _console.print(f"  {key}: {value}")
        else:
            _console.print(f"[yellow]⚠[/yellow] No constraint data found for season {args.season}")
    return 0


def _cmd_season_inspect_candidates(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season inspect candidates`` — list replacement candidates."""
    from ..season_state import replacement_candidates

    result = replacement_candidates(
        season=args.season,
        tournament_id=args.tournament_id,
        root=args.root,
        replace_team_label=getattr(args, 'replace_team', None),
        legal_only=getattr(args, 'legal_only', False),
        limit=getattr(args, 'limit', None),
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if isinstance(result, list):
            _console.print(f"[green]✓[/green] Found {len(result)} replacement candidates for tournament {args.tournament_id} in season {args.season}:")
            for i, candidate in enumerate(result, 1):
                _console.print(f"  {i}. {candidate}")
        elif result:
            _console.print(f"[green]✓[/green] Replacement candidates for tournament {args.tournament_id} in season {args.season}:")
            for key, value in result.items():
                _console.print(f"  {key}: {value}")
        else:
            _console.print(f"[yellow]⚠[/yellow] No replacement candidates found for tournament {args.tournament_id} in season {args.season}")
    return 0
