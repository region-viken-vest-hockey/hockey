"""Transport for ``season club-booking-acceptance-preview|apply``."""

from __future__ import annotations

import argparse

from .season_canonical_commands import _console, _dry_run_label, _emit_json


def _cmd_season_club_booking_acceptance(args: argparse.Namespace) -> int:
    from ..application.canonical_season import acceptance_facade

    common = {
        "season": args.season,
        "source_id": args.source_id,
        "root": args.root,
        "actor": args.actor,
        "note": args.note,
        "reference": args.reference or "",
        "expected_revision": args.expected_revision,
    }
    if args.season_command == "club-booking-acceptance-preview":
        result = acceptance_facade.club_booking_acceptance_preview(**common)
        message = f"Preview of {result['count']} interpretation(s) for source {args.source_id}"
    else:
        result = acceptance_facade.club_booking_acceptance_apply(
            **common,
            reviewed_token=args.reviewed_token,
            dry_run=args.dry_run,
        )
        message = f"{_dry_run_label(args)} club booking acceptance for source {args.source_id}"
    if args.json:
        _emit_json(result)
    else:
        _console.print(f"[green]✓[/green] {message}")
        for conflict in result.get("conflicts") or []:
            _console.print(f"[yellow]![/yellow] {conflict.get('kind')}: {conflict.get('message', '')}")
        if args.season_command == "club-booking-acceptance-preview":
            _console.print(f"reviewed token: {result['reviewed_token']}")
    return 0
