"""Season export, export-parity and status command handlers."""

from __future__ import annotations

import argparse

from rich.console import Console

from .season_export_core import run_canonical_season_export

_console = Console()


def _cmd_season_export(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season export`` — regenerate Stage 4 exports from canonical season state."""
    import json as _json

    if getattr(args, "presentation_only", False):
        from .season_presentation_export import cmd_season_presentation_export

        return cmd_season_presentation_export(args)

    result = run_canonical_season_export(args)
    stale_export = bool(result["stale_export"])
    if args.json:
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if stale_export:
            _console.print(
                f"[yellow]⚠[/yellow] Exported canonical season {args.season} "
                f"revision {result.get('canonical_revision')}, but the canonical "
                "state advanced during the export "
                f"({result['export_freshness'].get('reason')}). This artifact is "
                "NOT publishable; re-export from the current revision."
            )
        else:
            _console.print(
                f"[green]✓[/green] Exported canonical season {args.season} "
                f"revision {result.get('canonical_revision')}"
            )
        for label, path in result.get("output_files", {}).items():
            _console.print(f"  {label}: {path}")
    return 1 if stale_export else 0


def _cmd_season_export_parity(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season export-parity`` — verify export parity between canonical and pipeline exports."""
    from ..application.canonical_season.store_facade import load_schedule, SeasonStateError

    try:
        schedule = load_schedule(args.season, root=args.root)
    except SeasonStateError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    plan_dict = schedule.get("plan", {})
    if not plan_dict:
        _console.print(f"[red]✗[/red] No plan found in canonical schedule for {args.season}")
        return 1

    # TODO: Implement export parity comparison
    _console.print("[yellow]⚠[/yellow] Export parity check not fully implemented yet")
    return 0


def _cmd_season_status(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season status`` — show canonical season state metadata."""
    from ..application.canonical_season.store_facade import load_schedule, load_decisions, SeasonStateError

    try:
        schedule = load_schedule(args.season, root=args.root)
        decisions = load_decisions(args.season, root=args.root)
    except SeasonStateError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    from ..application.canonical_season.store_facade import approval_report

    revision = schedule.get("revision")
    plan = schedule.get("plan", {})
    tournaments = plan.get("tournaments", [])
    decisions_count = len(decisions.get("decisions", {}))
    approval_counts = approval_report(args.season, root=args.root)["counts"]

    result = {
        "season": args.season,
        "revision": revision,
        "tournament_count": len(tournaments),
        "decision_count": decisions_count,
        "approved_count": approval_counts["approved"],
        "stale_approval_count": approval_counts["stale"],
        "schedule_schema_version": schedule.get("schema_version"),
        "decisions_schema_version": decisions.get("schema_version"),
    }

    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(f"[bold]Canonical Season State: {args.season}[/bold]")
        _console.print(f"  Revision: {revision}")
        _console.print(f"  Tournaments: {len(tournaments)}")
        _console.print(f"  Decisions: {decisions_count}")
        _console.print(f"  Schedule schema: {schedule.get('schema_version')}")
        _console.print(f"  Decisions schema: {decisions.get('schema_version')}")
    return 0
