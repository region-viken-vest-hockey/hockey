"""
Calendar management command implementations for the RVV Miniputt CLI.

This module contains the implementations of the `rvv-miniputt season` subcommands
for managing calendar evidence and reconciliation.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from rich.console import Console

_console = Console()


def _cmd_season_refresh_calendars(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season refresh-calendars`` — refresh calendar evidence."""
    from ..pipeline.state import PipelineState
    from ..season_state import refresh_calendars

    state = PipelineState(args.work_dir)
    result = refresh_calendars(
        season=args.season,
        root=args.root,
        input_path=args.input,
        work_dir=args.work_dir,
        actor=args.actor,
        note=args.note,
        dry_run=args.dry_run,
        allow_missing_sources=args.allow_missing_sources,
        allow_source_policy_change=args.accept_source_policy_change,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    elif result.get("refused"):
        _console.print(
            f"[yellow]○[/yellow] Refused calendar evidence refresh for {args.season}: "
            "the configured source policy changed since promotion"
        )
        for change in result.get("source_policy_changes") or []:
            _console.print(f"  [yellow]- {change}[/yellow]")
        _console.print(
            "  Re-run with --accept-source-policy-change only after reviewing the diff, "
            "or restore the promoted source configuration."
        )
    else:
        action = "Previewed" if result.get("dry_run") else "Refreshed"
        marker = "[yellow]○[/yellow]" if result.get("dry_run") else "[green]✓[/green]"
        _console.print(
            f"{marker} {action} calendar evidence for {args.season}: "
            f"{str(result.get('previous_calendar_fingerprint') or '')[:12]} → "
            f"{str(result.get('calendar_fingerprint') or '')[:12]}"
        )
        _console.print(
            f"  sources: {result.get('source_count', 0)}, "
            f"blocked: {len(result.get('blocked_sources') or [])}, "
            f"verification_ok: {result.get('verification_ok')}"
        )
        if result.get("canonical_state_revision"):
            _console.print(f"  revision: {str(result.get('canonical_state_revision'))[:12]}")
        for club, entry in ((result.get("planned_tournament_reconciliation") or {}).get("clubs") or {}).items():
            requires_review = entry.get("requires_review_count", 0)
            marker = "[yellow]○[/yellow]" if requires_review else "[green]✓[/green]"
            _console.print(
                f"  {marker} {club}: {entry.get('count', 0)} planned tournament(s) checked against "
                "the fresh scrape, {requires_review} require review"
            )
        if not result.get("verification_ok"):
            _console.print("[yellow]New calendar conflicts/findings may require repair before export.[/yellow]")
    return 0


def _cmd_season_reconcile_config(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season reconcile-config`` — reconcile configuration."""
    from ..pipeline.state import PipelineState
    from ..season_state import reconcile_config

    state = PipelineState(args.work_dir)
    result = reconcile_config(
        season=args.season,
        root=args.root,
        input_path=args.input,
        actor=args.actor,
        note=args.note,
        dry_run=args.dry_run,
        # Note: The original function didn't have all these parameters, but I'm keeping the signature consistent
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        action = "Previewed" if result.get("dry_run") else "Reconciled"
        marker = "[yellow]○[/yellow]" if result.get("dry_run") else "[green]✓[/green]"
        migrations = result.get("semantic_migrations") or []
        change_count = sum(len(item.get("changes") or []) for item in migrations)
        _console.print(
            f"{marker} {action} config for {args.season}: "
            f"{change_count} tournament facts, verification_ok={result.get('verification_ok')}"
        )
        if result.get("canonical_state_revision"):
            _console.print(f"  revision: {str(result.get('canonical_state_revision'))[:12]}")
        if result.get("refused"):
            _console.print("  [yellow]⚠[/yellow] refused: " + "; ".join(result.get("refusal_reasons") or []))
        for migration in migrations:
            for age_group, change in (migration.get("age_group_changes") or {}).items():
                _console.print(
                    f"  {age_group}: {change.get('old_value')} → {change.get('migrated_value')} "
                    f"({migration.get('semantic_migration')})"
                )
    return 0