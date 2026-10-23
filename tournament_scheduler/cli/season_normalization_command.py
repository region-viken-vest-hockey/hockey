"""Season placement and arena identity normalization command handlers."""

from __future__ import annotations

import argparse

from rich.console import Console

_console = Console()


def _cmd_season_normalize_placements(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season normalize-placements`` — normalize placements."""
    from ..application.canonical_season.store_facade import normalize_placements

    schedule, _decisions = normalize_placements(
        season=args.season,
        root=args.root,
        actor=args.actor,
        note=args.note,
        dry_run=args.dry_run,
    )
    # A dry run returns the proposed report on the schedule and writes nothing;
    # a real run records the same facts under ``normalized_from`` when it changed.
    report = (
        schedule.get("placement_normalization")
        if args.dry_run
        else schedule.get("normalized_from")
    ) or {}
    summary = {
        "season": args.season,
        "dry_run": bool(args.dry_run),
        "changed": bool(report.get("removed_tournament_ids")),
        "removed_tournament_ids": list(report.get("removed_tournament_ids") or []),
        "obligation_count": report.get("obligation_count", 0),
        "revision": schedule.get("revision"),
    }
    if args.json:
        import json as _json
        print(_json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.dry_run:
        _console.print(
            f"[yellow]dry run[/yellow] for season {args.season}: "
            f"{len(summary['removed_tournament_ids'])} tournament(s) would be demoted; nothing written"
        )
    elif summary["changed"]:
        _console.print(
            f"[green]✓[/green] Normalized placements for season {args.season}: "
            f"{len(summary['removed_tournament_ids'])} tournament(s) demoted to obligations"
        )
    else:
        _console.print(f"[green]✓[/green] Placements for season {args.season} already normalized; nothing changed")
    return 0


def _cmd_season_normalize_arena_identities(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season normalize-arena-identities`` — normalize arena identities."""
    from ..application.canonical_season.store_facade import normalize_arena_identities

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
