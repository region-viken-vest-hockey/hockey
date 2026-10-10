"""Season ice-time, manual booking assertion, club booking source and protection command handlers."""

from __future__ import annotations

import argparse

from rich.console import Console

_console = Console()


def _cmd_season_set_ice_time_minutes(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season set-ice-time-minutes`` — set ice time minutes for a tournament."""
    from ..season_state import set_ice_time_minutes

    result = set_ice_time_minutes(
        season=args.season,
        tournament_id=args.tournament_id,
        minutes=args.minutes,
        request_id=args.request_id,
        root=args.root,
        actor=args.actor,
        note=args.note,
        reference=args.reference,
        expected_revision=args.expected_revision,
        dry_run=args.dry_run,
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[green]✓[/green] Set ice time minutes to {args.minutes} for tournament {args.tournament_id} in season {args.season}"
        )
    return 0


def _cmd_season_clear_ice_time_minutes(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season clear-ice-time-minutes`` — clear ice time minutes for a tournament."""
    from ..season_state import clear_ice_time_minutes

    result = clear_ice_time_minutes(
        season=args.season,
        tournament_id=args.tournament_id,
        root=args.root,
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[green]✓[/green] Cleared ice time minutes for tournament {args.tournament_id} in season {args.season}"
        )
    return 0


def _cmd_season_ice_time_overrides(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season ice-time-overrides`` — show ice time overrides."""
    from ..season_state import ice_time_override_report

    result = ice_time_override_report(
        season=args.season,
        root=args.root,
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        overrides = result.get("overrides", {})
        if overrides:
            _console.print(f"[green]✓[/green] Ice time overrides for {args.season}:")
            for age_group, minutes in overrides.items():
                _console.print(f"  {age_group}: {minutes} minutes")
        else:
            _console.print(f"[green]✓[/green] No ice time overrides set for season {args.season}")
    return 0


def _cmd_season_set_manual_booking_assertion(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season set-manual-booking-assertion`` — set manual booking assertion."""
    from ..season_state import set_manual_booking_assertion

    set_manual_booking_assertion(
        season=args.season,
        club=args.club,
        label=args.label,
        date=args.date,
        root=args.root,
    )
    if args.json:
        import json as _json
        print(_json.dumps({"status": "ok"}, ensure_ascii=False, indent=2))
    else:
        _console.print(
            f"[green]✓[/green] Set manual booking assertion for {args.club} {args.label} on {args.date} in season {args.season}"
        )
    return 0


def _cmd_season_clear_manual_booking_assertion(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season clear-manual-booking-assertion`` — clear manual booking assertion."""
    from ..season_state import clear_manual_booking_assertion

    clear_manual_booking_assertion(
        season=args.season,
        club=args.club,
        label=args.label,
        date=args.date,
        root=args.root,
    )
    if args.json:
        import json as _json
        print(_json.dumps({"status": "ok"}, ensure_ascii=False, indent=2))
    else:
        _console.print(
            f"[green]✓[/green] Cleared manual booking assertion for {args.club} {args.label} on {args.date} in season {args.season}"
        )
    return 0


def _cmd_season_club_booking_sources(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season club-booking-sources`` — set or show club booking sources."""
    from ..season_state import club_booking_sources, set_club_booking_source

    if args.club is not None and args.source is not None:
        # Set club booking source
        set_club_booking_source(
            season=args.season,
            club=args.club,
            source=args.source,
            root=args.root,
        )
        if args.json:
            import json as _json
            print(_json.dumps({"status": "ok"}, ensure_ascii=False, indent=2))
        else:
            _console.print(
                f"[green]✓[/green] Set booking source for club {args.club} to {args.source} in season {args.season}"
            )
    else:
        # Show club booking sources
        sources = club_booking_sources(
            season=args.season,
            root=args.root,
        )
        if args.json:
            import json as _json
            print(_json.dumps(sources, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            if sources:
                _console.print(f"[green]✓[/green] Club booking sources for season {args.season}:")
                for club, source in sources.items():
                    _console.print(f"  {club}: {source}")
            else:
                _console.print(f"[green]✓[/green] No club booking sources set for season {args.season}")
    return 0


def _cmd_season_change_protections(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season change-protections`` — change protection status for clubs."""
    from ..season_state import change_protection_report

    result = change_protection_report(
        season=args.season,
        root=args.root,
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        protections = result.get("protections", [])
        if protections:
            _console.print(f"[green]✓[/green] Protections for season {args.season}:")
            for prot in protections:
                _console.print(f"  {prot.get('club')} ({prot.get('protected_until', 'indefinitely')})")
        else:
            _console.print(f"[green]✓[/green] No protections set for season {args.season}")
    return 0


def _cmd_season_release_protection(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season release-protection`` — release protection for a club."""
    from ..season_state import release_change_protections

    release_change_protections(
        season=args.season,
        club=args.club,
        root=args.root,
    )
    if args.json:
        import json as _json
        print(_json.dumps({"status": "ok"}, ensure_ascii=False, indent=2))
    else:
        _console.print(
            f"[green]✓[/green] Released protection for club {args.club} in season {args.season}"
        )
    return 0
