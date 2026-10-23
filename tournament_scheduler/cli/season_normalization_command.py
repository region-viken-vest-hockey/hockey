"""Season placement and arena identity normalization command handlers."""

from __future__ import annotations

import argparse

from rich.console import Console

_console = Console()


def _cmd_season_normalize_placements(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season normalize-placements`` — normalize placements."""
    from ..application.canonical_season.store_facade import load_schedule, normalize_placements

    revision_before = load_schedule(args.season, root=args.root).get("revision")
    schedule, _decisions = normalize_placements(
        season=args.season,
        root=args.root,
        actor=args.actor,
        note=args.note,
        dry_run=args.dry_run,
    )
    # A real run changed the season only if the revision moved: an unchanged
    # season is returned as stored, which can still carry ``normalized_from``
    # from an earlier run. A dry run reports its proposal on the schedule.
    if args.dry_run:
        report = schedule.get("placement_normalization") or {}
        changed = bool(report.get("removed_tournament_ids"))
    else:
        changed = schedule.get("revision") != revision_before
        report = (schedule.get("normalized_from") or {}) if changed else {}
    summary = {
        "season": args.season,
        "dry_run": bool(args.dry_run),
        "changed": changed,
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
