"""
Guest slot command implementations for the RVV Miniputt CLI.

This module contains the implementations of the `rvv-miniputt season` subcommands
for managing guest slots.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from ..pipeline.state import PipelineState

from rich.console import Console

_console = Console()


def _cmd_season_guest_report(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season guest-report`` — guest slot report."""
    from ..season_state import guest_slot_report

    report = guest_slot_report(args.season, root=args.root)
    if args.json:
        import json as _json

        print(_json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[bold]Gjesteplasser {args.season}[/bold] "
            f"({report['reserved_total']} reservert, {report['open_total']} ledige, "
            f"{report['filled_total']} fylt)"
        )
        for entry in report["tournaments"]:
            _console.print(
                f"  [green]•[/green] {entry['tournament_id']} ({entry['age_group']} "
                f"{entry['date']}): reservert {entry['reserved']}, ledig {entry['open']}, "
                f"fylt {entry['filled']}"
            )
    return 0


def _cmd_season_guest_candidates(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season guest-candidates`` — guest slot candidates."""
    from ..pipeline.state import PipelineState
    from ..season_state import guest_slot_candidates
    from .verification_problem import _canonical_verification_problem

    state = PipelineState(args.work_dir)
    age_groups = None
    if args.age_groups:
        age_groups = [part.strip() for part in str(args.age_groups).split(",") if part.strip()]
    report = guest_slot_candidates(
        season=args.season,
        root=args.root,
        age_groups=age_groups,
        max_per_tournament=int(args.max_per_tournament),
        problem=_canonical_verification_problem(args.work_dir, args.season, args.root),
    )
    if args.json:
        import json as _json

        print(_json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[bold]Gjestekandidater {args.season}[/bold] "
            f"({len(report['legal_candidates'])} av {len(report['candidates'])} lovlige)"
        )
        for candidate in report["candidates"]:
            marker = "[green]ok[/green]" if candidate["legal"] else "[dim]nei[/dim]"
            extra = (
                " (krever deltakerendring)"
                if candidate.get("reservation_requires_participant_change")
                else ""
            )
            _console.print(
                f"  #{candidate['rank']} {marker} {candidate['tournament_id']} "
                f"({candidate['age_group']} {candidate['date']}) "
                f"ledige plasser: {candidate['free_places']}{extra}"
            )
    return 0


def _cmd_season_guest_reserve(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season guest-reserve`` — reserve guest slots."""
    from ..pipeline.state import PipelineState
    from ..season_state import reserve_guest_slot
    from .verification_problem import _canonical_verification_problem

    state = PipelineState(args.work_dir)
    schedule = reserve_guest_slot(
        season=args.season,
        tournament_id=args.tournament_id,
        root=args.root,
        count=int(args.count),
        displaced_teams=list(args.displaced_teams or []),
        problem=_canonical_verification_problem(args.work_dir, args.season, args.root),
        actor=args.actor,
        note=args.note,
        dry_run=bool(args.dry_run),
    )
    if args.json:
        import json as _json

        print(_json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        action = "Validated reservation preview for" if schedule.get("dry_run") else "Reserved"
        _console.print(
            f"[green]✓[/green] {action} {args.tournament_id} in {args.season}; "
            f"revision {schedule.get('revision')}"
        )
    return 0


def _cmd_season_guest_fill(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season guest-fill`` — fill a guest slot."""
    from ..pipeline.state import PipelineState
    from ..season_state import fill_guest_slot
    from .verification_problem import _canonical_verification_problem

    state = PipelineState(args.work_dir)
    external_team = {
        "club": args.external_club,
        "label": args.external_label,
    }
    if args.external_age_group:
        external_team["age_group"] = args.external_age_group
    schedule = fill_guest_slot(
        season=args.season,
        tournament_id=args.tournament_id,
        slot_id=args.slot_id,
        external_team=external_team,
        root=args.root,
        problem=_canonical_verification_problem(args.work_dir, args.season, args.root),
        actor=args.actor,
        note=args.note,
    )
    if args.json:
        import json as _json

        print(_json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[green]✓[/green] Filled guest place in {args.tournament_id} ({args.season}) "
            f"with {args.external_label}; revision {schedule.get('revision')}"
        )
    return 0


def _cmd_season_guest_release(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season guest-release`` — release a guest slot."""
    from ..pipeline.state import PipelineState
    from ..season_state import release_guest_slot
    from .verification_problem import _canonical_verification_problem

    state = PipelineState(args.work_dir)
    replacement_team = None
    if args.replacement_label:
        replacement_team = {
            "club": args.replacement_club or "",
            "label": args.replacement_label,
        }
        if args.replacement_age_group:
            replacement_team["age_group"] = args.replacement_age_group
    schedule = release_guest_slot(
        season=args.season,
        tournament_id=args.tournament_id,
        slot_id=args.slot_id,
        replacement_team=replacement_team,
        root=args.root,
        problem=_canonical_verification_problem(args.work_dir, args.season, args.root),
        actor=args.actor,
        note=args.note,
        dry_run=bool(args.dry_run),
    )
    if args.json:
        import json as _json

        print(_json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        action = "Validated release preview for" if schedule.get("dry_run") else "Released"
        _console.print(
            f"[green]✓[/green] {action} guest place in {args.tournament_id} ({args.season}); "
            f"revision {schedule.get('revision')}"
        )
    return 0