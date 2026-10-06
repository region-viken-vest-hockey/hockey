"""
Date management command implementations for the RVV Miniputt CLI.

This module contains the implementations of the `rvv-miniputt season` subcommands
for managing dates and holiday-date exceptions.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from ..pipeline.state import PipelineState

from rich.console import Console

_console = Console()


def _cmd_season_ban_date(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season ban-date`` — ban a date."""
    from ..pipeline.state import PipelineState
    from ..season_state import add_banned_date

    state = PipelineState(args.work_dir)
    result = add_banned_date(
        season=args.season,
        date=args.date,
        request_id=args.request_id,
        root=args.root,
        actor=args.actor,
        note=args.note,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        banned = result["banned_date"]
        verb = "Recorded" if result["created"] else "Already banned"
        _console.print(
            f"[green]✓[/green] {verb} {banned.get('date')} for {args.season} "
            f"(request {banned.get('request_id')})"
        )
        affected = banned.get("affected_tournament_ids") or []
        if affected:
            _console.print(
                "  [yellow]⚠[/yellow] current schedule uses this date: "
                + ", ".join(affected)
                + " — repair with a scoped atomic batch"
            )
        else:
            _console.print("  current schedule does not use this date")
        _console.print(
            f"  canonical revision: {result['canonical_state_revision']}"
        )
    return 0


def _cmd_season_unban_date(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season unban-date`` — unban a date."""
    from ..pipeline.state import PipelineState
    from ..season_state import release_banned_dates

    state = PipelineState(args.work_dir)
    result = release_banned_dates(
        season=args.season,
        root=args.root,
        date_ids=list(args.date_ids or []),
        dates=list(args.dates or []),
        request_id=args.request_id,
        actor=args.actor,
        note=args.note,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[green]✓[/green] Removed {len(result['released_banned_date_ids'])} "
            f"banned date(s); {result['active_count']} remain active"
        )
    return 0


def _cmd_season_allow_holiday_date(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season allow-holiday-date`` — allow a holiday date."""
    from ..pipeline.state import PipelineState
    from ..season_state import allow_holiday_date

    state = PipelineState(args.work_dir)
    result = allow_holiday_date(
        season=args.season,
        date=args.date,
        reason=args.reason,
        root=args.root,
        actor=args.actor,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        entry = result["holiday_date_exception"]
        verb = "Recorded" if result["created"] else "Already allowed"
        _console.print(
            f"[green]✓[/green] {verb} {entry.get('date')} for {args.season}: "
            f"{entry.get('reason')}"
        )
        _console.print(
            f"  derived policy reason: "
            f"{entry.get('derived_holiday_policy_reason') or '-'}"
        )
        _console.print(f"  canonical revision: {result['canonical_state_revision']}")
    return 0


def _cmd_season_disallow_holiday_date(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season disallow-holiday-date`` — disallow holiday date exceptions."""
    from ..pipeline.state import PipelineState
    from ..season_state import disallow_holiday_dates

    state = PipelineState(args.work_dir)
    result = disallow_holiday_dates(
        season=args.season,
        root=args.root,
        dates=list(args.dates or []),
        exception_ids=list(args.exception_ids or []),
        actor=args.actor,
        note=args.note,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[green]✓[/green] Removed {len(result['released_holiday_date_exception_ids'])} "
            f"holiday-date exception(s); {result['active_count']} remain active"
        )
    return 0


def _cmd_season_holiday_date_exceptions(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season holiday-date-exceptions`` — show holiday-date exceptions."""
    from ..pipeline.state import PipelineState
    from ..season_state import holiday_date_exception_report

    state = PipelineState(args.work_dir)
    report = holiday_date_exception_report(
        args.season, root=args.root, include_released=bool(args.all)
    )
    if args.json:
        import json as _json

        print(_json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[bold]Holiday-date exceptions {args.season}[/bold] "
            f"({report['active_count']} active)"
        )
        for entry in report["holiday_date_exceptions"]:
            _console.print(
                f"  {entry.get('date')} [{entry.get('status') or 'active'}] "
                f"reason={entry.get('reason') or '-'}; "
                f"policy={entry.get('derived_holiday_policy_reason') or '-'}"
            )
    return 0