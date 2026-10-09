"""
Canonical season maintenance command handlers for the RVV Miniputt CLI.

Transport only: each handler parses argparse input, delegates to the
``tournament_scheduler.season_state`` facade, and renders the result. Domain
verdicts, validation and persistence live in the canonical season application
services; ``SeasonStateError`` raised there is handled by ``_cmd_season``.
"""

from __future__ import annotations

import argparse
import json as _json
from typing import Any

from rich.console import Console

_console = Console()


def _emit_json(result: Any) -> None:
    print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


def _dry_run_label(args: argparse.Namespace) -> str:
    return "Preview" if getattr(args, "dry_run", False) else "Applied"


def _split_regression_acceptances(raw: list[str] | None) -> list[str] | None:
    return list(raw) if raw else None


def _verification_problem(args: argparse.Namespace) -> dict | None:
    """Canonical planning problem used by mutation gates (same contract as approve)."""
    from .verification_problem import _canonical_verification_problem

    return _canonical_verification_problem(args.work_dir, args.season, args.root)


def _cmd_season_move(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season move`` — move one tournament to a new date/arena/host/time."""
    from ..season_state import move_tournament

    result = move_tournament(
        season=args.season,
        tournament_id=args.tournament_id,
        root=args.root,
        date=args.date,
        arena=args.arena,
        host_club=args.host_club,
        start_time=args.start_time,
        actor=args.actor,
        note=args.note,
        dry_run=args.dry_run,
        problem=_verification_problem(args),
        allow_cross_half=args.allow_cross_half,
        request_id=args.request_id,
        allow_manual_placement=args.allow_manual_placement,
        allow_host_confirmation=args.allow_host_confirmation,
    )
    if args.json:
        _emit_json(result)
    else:
        _console.print(
            f"[green]✓[/green] {_dry_run_label(args)} move of {args.tournament_id} for {args.season}"
        )
    return 0


def _cmd_season_replace_participant(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season replace-participant``."""
    from ..season_state import replace_participant

    result = replace_participant(
        season=args.season,
        tournament_id=args.tournament_id,
        remove_team_label=args.remove_team,
        add_team_label=args.add_team,
        problem=_verification_problem(args),
        root=args.root,
        actor=args.actor,
        note=args.note,
        dry_run=args.dry_run,
        request_id=args.request_id,
    )
    if args.json:
        _emit_json(result)
    else:
        _console.print(
            f"[green]✓[/green] {_dry_run_label(args)} replacement in {args.tournament_id}: "
            f"{args.remove_team} -> {args.add_team}"
        )
    blocked = (result.get("verdict") or {}).get("status") == "blocked"
    if args.dry_run and args.fail_on_blocked and blocked:
        return 3
    return 0


def _cmd_season_remove_participant(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season remove-participant``."""
    from ..season_state import remove_participant

    result = remove_participant(
        season=args.season,
        tournament_ids=list(args.tournament_ids or []),
        remove_team_label=args.remove_team,
        reconcile_withdrawal=args.reconcile_withdrawal,
        problem=_verification_problem(args),
        root=args.root,
        actor=args.actor,
        note=args.note,
        dry_run=args.dry_run,
        request_id=args.request_id,
        accept_regressions=_split_regression_acceptances(args.accept_team_regressions),
        accept_regression_reason=args.accept_regression_reason,
    )
    if args.json:
        _emit_json(result)
    else:
        _console.print(
            f"[green]✓[/green] {_dry_run_label(args)} removal of {args.remove_team} for {args.season}"
        )
    return 0


def _cmd_season_swap_participants(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season swap-participants``."""
    from ..season_state import swap_participants

    result = swap_participants(
        season=args.season,
        tournament_a_id=args.tournament_a,
        problem=_verification_problem(args),
        team_a_label=args.team_a,
        tournament_b_id=args.tournament_b,
        team_b_label=args.team_b,
        root=args.root,
        actor=args.actor,
        note=args.note,
        dry_run=args.dry_run,
        request_id=args.request_id,
        accept_regressions=_split_regression_acceptances(args.accept_team_regressions),
        accept_regression_reason=args.accept_regression_reason,
    )
    if args.json:
        _emit_json(result)
    else:
        _console.print(
            f"[green]✓[/green] {_dry_run_label(args)} swap of {args.team_a} and {args.team_b} for {args.season}"
        )
    return 0


def _cmd_season_withdrawal_report(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season withdrawals`` — read-only withdrawal ledger."""
    from ..season_state import withdrawal_report

    result = withdrawal_report(args.season, root=args.root, include_released=args.all)
    _emit_json(result)
    return 0


def _cmd_season_release_withdrawal(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season release-withdrawal``."""
    from ..season_state import release_participation_withdrawals

    result = release_participation_withdrawals(
        season=args.season,
        root=args.root,
        withdrawal_ids=list(args.withdrawal_ids or []) or None,
        request_id=args.request_id,
        actor=args.actor,
        note=args.note,
        restore_participants=args.restore_participant,
    )
    if args.json:
        _emit_json(result)
    else:
        _console.print(f"[green]✓[/green] Released participation withdrawals for {args.season}")
    return 0


def _cmd_season_rename_teams(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season rename-team`` — rename canonical team identities."""
    from ..season_state import rename_teams

    mappings: list[dict[str, str]] = []
    for raw in args.mapping or []:
        parts = [part.strip() for part in raw.split(",")]
        if len(parts) != 4:
            _console.print(
                f"[red]✗[/red] Invalid --mapping {raw!r}: expected club,age_group,from_label,to_label"
            )
            return 1
        club, age_group, from_label, to_label = parts
        mappings.append({"club": club, "age_group": age_group, "from_label": from_label, "to_label": to_label})

    club_values = args.club or []
    age_values = args.age_group or []
    from_values = args.from_label or []
    to_values = args.to_label or []
    if not (len(club_values) == len(age_values) == len(from_values) == len(to_values)):
        if club_values or age_values or from_values or to_values:
            _console.print("[red]✗[/red] --club, --age-group, --from and --to must be given the same number of times")
            return 1
    for club, age_group, from_label, to_label in zip(club_values, age_values, from_values, to_values):
        mappings.append({"club": club, "age_group": age_group, "from_label": from_label, "to_label": to_label})

    result = rename_teams(
        season=args.season,
        mappings=mappings,
        problem=_verification_problem(args),
        root=args.root,
        actor=args.actor,
        note=args.note,
        dry_run=args.dry_run,
        request_id=args.request_id,
        input_path=None if args.no_input_update else args.input,
    )
    if args.json:
        _emit_json(result)
    else:
        _console.print(f"[green]✓[/green] {_dry_run_label(args)} team rename for {args.season}")
    return 0


def _cmd_season_changes(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season changes`` — request-grouped change ledger (optionally written)."""
    from ..application.canonical_season.changes import render_change_log_markdown, write_change_log_markdown
    from ..season_state import change_request_ledger

    ledger = change_request_ledger(args.season, root=args.root)
    if args.write:
        path = write_change_log_markdown(ledger, root=args.root)
    if args.json:
        payload = dict(ledger)
        if args.write:
            payload["markdown_path"] = str(path)
        _emit_json(payload)
    elif args.markdown:
        print(render_change_log_markdown(ledger))
    else:
        _console.print(f"[green]✓[/green] Change ledger for {args.season}")
        if args.write:
            _console.print(f"  Wrote {path}")
    return 0


def _cmd_season_approvals(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season approvals`` — read-only approval/lock status."""
    from ..season_state import approval_report

    _emit_json(approval_report(args.season, root=args.root))
    return 0


def _cmd_season_constraints(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season constraints`` — read-only request constraint report."""
    from ..season_state import request_constraint_report

    _emit_json(request_constraint_report(args.season, root=args.root, include_released=args.all))
    return 0


def _cmd_season_add_constraint(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season add-constraint``."""
    from ..season_state import add_request_constraint

    teams: list[dict[str, str]] = []
    if args.team_club or args.team_label:
        teams.append({"club": args.team_club, "label": args.team_label, "age_group": args.team_age_group})
    if args.team2_club or args.team2_label:
        teams.append({"club": args.team2_club, "label": args.team2_label, "age_group": args.team2_age_group})

    result = add_request_constraint(
        season=args.season,
        type=args.type,
        request_id=args.request_id,
        teams=teams or None,
        date_from=args.date_from,
        date_to=args.date_to,
        min_days=args.min_days,
        root=args.root,
        actor=args.actor,
        note=args.note,
    )
    if args.json:
        _emit_json(result)
    else:
        _console.print(f"[green]✓[/green] Recorded {args.type} constraint for {args.season}")
    return 0


def _cmd_season_release_constraint(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season release-constraint``."""
    from ..season_state import release_request_constraints

    result = release_request_constraints(
        season=args.season,
        root=args.root,
        constraint_ids=list(args.constraint_ids or []) or None,
        request_id=args.request_id,
        actor=args.actor,
        note=args.note,
    )
    if args.json:
        _emit_json(result)
    else:
        _console.print(f"[green]✓[/green] Released request constraints for {args.season}")
    return 0


def _cmd_season_booking_set(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season booking-set`` — record a manual booking assertion."""
    from ..season_state import set_manual_booking_assertion

    result = set_manual_booking_assertion(
        season=args.season,
        tournament_id=args.tournament_id,
        booking_status=args.status,
        root=args.root,
        actor=args.actor,
        note=args.note,
        reference=args.reference or "",
        source_scope=args.source_scope or "tournament",
        source_assertion_id=args.source_assertion_id,
        stated_date=args.stated_date,
        stated_start=args.stated_start,
        stated_end=args.stated_end,
        expected_revision=args.expected_revision,
        supersede=args.supersede,
        dry_run=args.dry_run,
    )
    if args.json:
        _emit_json(result)
    else:
        _console.print(
            f"[green]✓[/green] {_dry_run_label(args)} booking status {args.status} for {args.tournament_id}"
        )
    return 0


def _cmd_season_booking_clear(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season booking-clear``."""
    from ..season_state import clear_manual_booking_assertion

    result = clear_manual_booking_assertion(
        season=args.season,
        tournament_id=args.tournament_id,
        root=args.root,
        actor=args.actor,
        note=args.note,
        dry_run=args.dry_run,
    )
    if args.json:
        _emit_json(result)
    else:
        _console.print(f"[green]✓[/green] {_dry_run_label(args)} booking clear for {args.tournament_id}")
    return 0


def _cmd_season_booking_source_set(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season booking-source-set``."""
    from ..season_state import set_club_booking_source

    result = set_club_booking_source(
        season=args.season,
        club=args.club,
        source_document=args.source_document,
        source_version=args.source_version,
        root=args.root,
        actor=args.actor,
        note=args.note,
        reference=args.reference or "",
        source_fingerprint=args.source_fingerprint,
        expected_revision=args.expected_revision,
        dry_run=args.dry_run,
    )
    if args.json:
        _emit_json(result)
    else:
        _console.print(f"[green]✓[/green] {_dry_run_label(args)} booking source for {args.club}")
    return 0


def _cmd_season_booking_sources(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season booking-sources`` — read-only club booking sources."""
    from ..season_state import club_booking_sources

    _emit_json(club_booking_sources(season=args.season, club=args.club, root=args.root))
    return 0


def _cmd_season_confirm_calendar_booking(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season confirm-calendar-booking``."""
    from ..season_state import confirm_calendar_booking

    result = confirm_calendar_booking(
        season=args.season,
        event_fingerprint=args.event_fingerprint,
        tournament_id=args.tournament_id,
        root=args.root,
        actor=args.actor,
        note=args.note,
        dry_run=args.dry_run,
    )
    if args.json:
        _emit_json(result)
    else:
        _console.print(f"[green]✓[/green] {_dry_run_label(args)} calendar booking confirmation for {args.tournament_id}")
    return 0


def _cmd_season_release_calendar_booking(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season release-calendar-booking``."""
    from ..season_state import release_calendar_booking

    result = release_calendar_booking(
        season=args.season,
        event_fingerprint=args.event_fingerprint,
        tournament_id=args.tournament_id,
        root=args.root,
        actor=args.actor,
        note=args.note,
        dry_run=args.dry_run,
    )
    if args.json:
        _emit_json(result)
    else:
        _console.print(f"[green]✓[/green] {_dry_run_label(args)} calendar booking release")
    return 0


def _cmd_season_booking_status(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season booking-status`` — read-only booking status."""
    from ..season_state import booking_status_report

    _emit_json(booking_status_report(season=args.season, root=args.root))
    return 0


def _cmd_season_calendar_booking_candidates(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season calendar-booking-candidates`` — read-only candidates."""
    from ..season_state import calendar_booking_candidates

    _emit_json(calendar_booking_candidates(season=args.season, club=args.club, root=args.root))
    return 0


def _cmd_season_calendar_booking_findings(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season calendar-booking-findings`` — read-only findings."""
    from ..season_state import calendar_booking_findings

    _emit_json(calendar_booking_findings(season=args.season, root=args.root))
    return 0


def _cmd_season_booking_assessment(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season booking-assessment`` — read-only calendar booking assessment."""
    from ..season_state import calendar_booking_assessment

    _emit_json(
        calendar_booking_assessment(
            season=args.season,
            club=args.club,
            root=args.root,
            date_window_days=args.date_window_days,
        )
    )
    return 0


def _cmd_season_reconcile_calendars(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season reconcile-calendar-bookings``."""
    from ..season_state import reconcile_calendar_bookings

    result = reconcile_calendar_bookings(
        season=args.season,
        club=args.club,
        root=args.root,
        actor=args.actor,
        note=args.note,
        dry_run=args.dry_run,
    )
    if args.json:
        _emit_json(result)
    else:
        _console.print(f"[green]✓[/green] {_dry_run_label(args)} calendar reconciliation for {args.club}")
    return 0
