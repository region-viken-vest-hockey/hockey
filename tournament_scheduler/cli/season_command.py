"""
Season command dispatcher for the RVV Miniputt CLI.

This module routes ``rvv-miniputt season`` subcommands to their owner modules.
"""

from __future__ import annotations

from rich.console import Console

_console = Console()


def _cmd_season(args) -> int:
    """Handle canonical Git-backed season-state commands."""
    from ..season_state import (
        SeasonStateError,
    )
    from .season_approval_command import (
        _cmd_season_approve,
        _cmd_season_unapprove,
        _cmd_season_banned_dates,
    )
    from .season_batch_command import (
        _cmd_season_batch,
    )
    from .season_export_command import (
        _cmd_season_export,
        _cmd_season_export_parity,
        _cmd_season_status,
    )
    from .season_booking_metadata_command import (
        _cmd_season_set_ice_time_minutes,
        _cmd_season_clear_ice_time_minutes,
        _cmd_season_ice_time_overrides,
        _cmd_season_set_manual_booking_assertion,
        _cmd_season_clear_manual_booking_assertion,
        _cmd_season_club_booking_sources,
        _cmd_season_change_protections,
        _cmd_season_release_protection,
    )
    from .season_reporting_command import (
        _cmd_season_history,
        _cmd_season_compact_history,
        _cmd_season_tourney_inspection,
        _cmd_season_placement_infeasibility,
        _cmd_season_decision_ledger,
        _cmd_season_guest_slot_report,
        _cmd_season_inspect_tournament,
        _cmd_season_inspect_constraints,
        _cmd_season_inspect_candidates,
    )
    from .season_normalization_command import (
        _cmd_season_normalize_placements,
        _cmd_season_normalize_arena_identities,
    )
    from .guest_slot_commands import (
        _cmd_season_guest_report,
        _cmd_season_guest_candidates,
        _cmd_season_guest_reserve,
        _cmd_season_guest_fill,
        _cmd_season_guest_release,
    )
    from .retire_team_command import _cmd_season_retire_team
    from .season_lifecycle_command import (
        _cmd_season_inventory,
        _cmd_season_lifecycle,
        _cmd_season_normalize_arenas,
        _cmd_season_publication_evidence,
        _cmd_season_reopen_planning,
        _cmd_season_seal_published,
    )
    from .baseline_command import _cmd_season_baseline
    from .repair_command import (
        _cmd_season_repair_options,
        _cmd_season_apply_repair,
        _cmd_season_accept_deviation,
    )
    from .date_management_command import (
        _cmd_season_ban_date,
        _cmd_season_unban_date,
        _cmd_season_allow_holiday_date,
        _cmd_season_disallow_holiday_date,
        _cmd_season_holiday_date_exceptions,
    )
    from .calendar_management_command import (
        _cmd_season_refresh_calendars,
        _cmd_season_reconcile_config,
    )
    from .issue_management_command import (
        _cmd_season_blockers,
        _cmd_season_findings,
        _cmd_season_audit,
        _cmd_season_record_infeasibility,
        _cmd_season_release_infeasibility,
        _cmd_season_infeasibility_report,
    )
    from .planning_command import (
        _cmd_season_promote,
        _cmd_season_plan,
        _cmd_season_replan,
        _cmd_season_diff_apply,
    )
    from .season_canonical_commands import (
        _cmd_season_move,
        _cmd_season_replace_participant,
        _cmd_season_remove_participant,
        _cmd_season_swap_participants,
        _cmd_season_withdrawal_report,
        _cmd_season_release_withdrawal,
        _cmd_season_rename_teams,
        _cmd_season_approvals,
        _cmd_season_changes,
        _cmd_season_constraints,
        _cmd_season_add_constraint,
        _cmd_season_release_constraint,
        _cmd_season_booking_set,
        _cmd_season_booking_clear,
        _cmd_season_booking_source_set,
        _cmd_season_booking_sources,
        _cmd_season_confirm_calendar_booking,
        _cmd_season_release_calendar_booking,
        _cmd_season_booking_status,
        _cmd_season_calendar_booking_candidates,
        _cmd_season_calendar_booking_findings,
        _cmd_season_booking_assessment,
        _cmd_season_reconcile_calendars,
    )

    try:
        if args.season_command == "promote":
            return _cmd_season_promote(args)

        if args.season_command == "plan":
            return _cmd_season_plan(args)

        if args.season_command == "replan":
            return _cmd_season_replan(args)

        if args.season_command in ("diff", "apply"):
            return _cmd_season_diff_apply(args)

        if args.season_command == "retire-team":
            return _cmd_season_retire_team(args)

        if args.season_command == "baseline":
            return _cmd_season_baseline(args)

        if args.season_command in ("repair-options", "search"):
            return _cmd_season_repair_options(args)

        if args.season_command == "apply-repair":
            return _cmd_season_apply_repair(args)

        if args.season_command == "accept-deviation":
            return _cmd_season_accept_deviation(args)

        if args.season_command == "revoke-acceptance":
            return _cmd_season_accept_deviation(args)

        if args.season_command == "ban-date":
            return _cmd_season_ban_date(args)

        if args.season_command == "unban-date":
            return _cmd_season_unban_date(args)

        if args.season_command == "allow-holiday-date":
            return _cmd_season_allow_holiday_date(args)

        if args.season_command == "disallow-holiday-date":
            return _cmd_season_disallow_holiday_date(args)

        if args.season_command == "holiday-date-exceptions":
            return _cmd_season_holiday_date_exceptions(args)

        if args.season_command == "refresh-calendars":
            return _cmd_season_refresh_calendars(args)

        if args.season_command == "reconcile-config":
            return _cmd_season_reconcile_config(args)

        if args.season_command == "blockers":
            return _cmd_season_blockers(args)

        if args.season_command == "findings":
            return _cmd_season_findings(args)

        if args.season_command == "audit":
            return _cmd_season_audit(args)

        if args.season_command == "record-infeasibility":
            return _cmd_season_record_infeasibility(args)

        if args.season_command == "release-infeasibility":
            return _cmd_season_release_infeasibility(args)

        if args.season_command == "infeasibility-report":
            return _cmd_season_infeasibility_report(args)

        if args.season_command == "guest-report":
            return _cmd_season_guest_report(args)

        if args.season_command == "guest-candidates":
            return _cmd_season_guest_candidates(args)

        if args.season_command == "guest-reserve":
            return _cmd_season_guest_reserve(args)

        if args.season_command == "guest-fill":
            return _cmd_season_guest_fill(args)

        if args.season_command == "guest-release":
            return _cmd_season_guest_release(args)

        if args.season_command == "move":
            return _cmd_season_move(args)

        if args.season_command == "replace-participant":
            return _cmd_season_replace_participant(args)

        if args.season_command == "swap-participants":
            return _cmd_season_swap_participants(args)

        if args.season_command == "remove-participant":
            return _cmd_season_remove_participant(args)

        if args.season_command == "withdrawals":
            return _cmd_season_withdrawal_report(args)

        if args.season_command == "rename-team":
            return _cmd_season_rename_teams(args)

        if args.season_command == "release-withdrawal":
            return _cmd_season_release_withdrawal(args)

        if args.season_command == "approve":
            return _cmd_season_approve(args)

        if args.season_command == "unapprove":
            return _cmd_season_unapprove(args)

        if args.season_command == "approvals":
            return _cmd_season_approvals(args)

        if args.season_command == "changes":
            return _cmd_season_changes(args)

        if args.season_command == "constraints":
            return _cmd_season_constraints(args)

        if args.season_command == "add-constraint":
            return _cmd_season_add_constraint(args)

        if args.season_command == "release-constraint":
            return _cmd_season_release_constraint(args)

        if args.season_command == "booking-set":
            return _cmd_season_booking_set(args)

        if args.season_command == "booking-clear":
            return _cmd_season_booking_clear(args)

        if args.season_command == "booking-source-set":
            return _cmd_season_booking_source_set(args)

        if args.season_command == "booking-sources":
            return _cmd_season_booking_sources(args)

        if args.season_command == "confirm-calendar-booking":
            return _cmd_season_confirm_calendar_booking(args)

        if args.season_command == "release-calendar-booking":
            return _cmd_season_release_calendar_booking(args)

        if args.season_command == "booking-status":
            return _cmd_season_booking_status(args)

        if args.season_command == "calendar-booking-candidates":
            return _cmd_season_calendar_booking_candidates(args)

        if args.season_command == "calendar-booking-findings":
            return _cmd_season_calendar_booking_findings(args)

        if args.season_command == "set-ice-time-minutes":
            return _cmd_season_set_ice_time_minutes(args)

        if args.season_command == "clear-ice-time-minutes":
            return _cmd_season_clear_ice_time_minutes(args)

        if args.season_command == "ice-time-overrides":
            return _cmd_season_ice_time_overrides(args)

        if args.season_command == "set-manual-booking-assertion":
            return _cmd_season_set_manual_booking_assertion(args)

        if args.season_command == "clear-manual-booking-assertion":
            return _cmd_season_clear_manual_booking_assertion(args)

        if args.season_command == "club-booking-sources":
            return _cmd_season_club_booking_sources(args)

        if args.season_command == "protections":
            return _cmd_season_change_protections(args)

        if args.season_command == "release-protection":
            return _cmd_season_release_protection(args)

        if args.season_command == "history":
            return _cmd_season_history(args)

        if args.season_command == "compact-history":
            return _cmd_season_compact_history(args)

        if args.season_command == "tourney-inspection":
            return _cmd_season_tourney_inspection(args)

        if args.season_command == "placement-infeasibility":
            return _cmd_season_placement_infeasibility(args)

        if args.season_command == "banned-dates":
            return _cmd_season_banned_dates(args)

        if args.season_command == "holiday-date-exceptions":
            return _cmd_season_holiday_date_exceptions(args)

        if args.season_command == "booking-assessment":
            return _cmd_season_booking_assessment(args)

        if args.season_command == "reconcile-calendar-bookings":
            return _cmd_season_reconcile_calendars(args)

        if args.season_command == "normalize-placements":
            return _cmd_season_normalize_placements(args)

        if args.season_command == "normalize-arena-identities":
            return _cmd_season_normalize_arena_identities(args)

        if args.season_command == "decision-ledger":
            return _cmd_season_decision_ledger(args)

        if args.season_command == "guest-slot-report":
            return _cmd_season_guest_slot_report(args)

        if args.season_command == "export":
            return _cmd_season_export(args)

        if args.season_command == "export-parity":
            return _cmd_season_export_parity(args)

        if args.season_command == "status":
            return _cmd_season_status(args)

        if args.season_command == "approve":
            return _cmd_season_approve(args)

        if args.season_command == "unapprove":
            return _cmd_season_unapprove(args)

        if args.season_command == "banned-dates":
            return _cmd_season_banned_dates(args)

        if args.season_command == "batch":
            return _cmd_season_batch(args)

        if args.season_command == "inspect":
            if args.inspect_command == "tournament":
                return _cmd_season_inspect_tournament(args)
            elif args.inspect_command == "constraints":
                return _cmd_season_inspect_constraints(args)
            elif args.inspect_command == "candidates":
                return _cmd_season_inspect_candidates(args)
            else:
                _console.print("[red]✗[/red] Missing inspect subcommand")
                return 1

        if args.season_command == "inventory":
            return _cmd_season_inventory(args)

        if args.season_command == "lifecycle":
            return _cmd_season_lifecycle(args)

        if args.season_command == "normalize-arenas":
            return _cmd_season_normalize_arenas(args)

        if args.season_command == "publication-evidence":
            return _cmd_season_publication_evidence(args)

        if args.season_command == "reopen-planning":
            return _cmd_season_reopen_planning(args)

        if args.season_command == "seal-published":
            return _cmd_season_seal_published(args)

        _console.print("[red]✗[/red] Missing season subcommand")
        return 1

    except SeasonStateError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1
