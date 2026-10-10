"""
Season command implementation for the RVV Miniputt CLI.

This module contains the implementation of the `rvv-miniputt season` command
and its subcommands for managing the canonical season state.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from rich.console import Console

_console = Console()


def _cmd_season(args) -> int:
    """Handle canonical Git-backed season-state commands."""
    from ..season_state import (
        SeasonStateError,
    )
    from .guest_slot_commands import (
        _cmd_season_guest_report,
        _cmd_season_guest_candidates,
        _cmd_season_guest_reserve,
        _cmd_season_guest_fill,
        _cmd_season_guest_release,
    )
    from .retire_team_command import _cmd_season_retire_team
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

        if args.season_command == "repair-options":
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

        _console.print("[red]✗[/red] Missing season subcommand")
        return 1

    except SeasonStateError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

def _cmd_season_approve(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season approve`` — approve a tournament."""
    from ..season_state import approve_tournament
    from .verification_problem import _canonical_verification_problem

    result = approve_tournament(
        season=args.season,
        tournament_id=args.tournament_id,
        actor=args.actor,
        note=args.note,
        placement_locked=args.placement_locked,
        participants_locked=args.participants_lock,
        root=args.root,
        problem=_canonical_verification_problem(args.work_dir, args.season, args.root),
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[green]✓[/green] Approved tournament {args.tournament_id} for {args.season}"
            f" (request {result.get('request_id')})"
        )
    return 0


def _cmd_season_unapprove(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season unapprove`` — unapprove a tournament."""
    from ..season_state import unapprove_tournament

    result = unapprove_tournament(
        season=args.season,
        tournament_id=args.tournament_id,
        actor=args.actor,
        note=args.note,
        root=args.root,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[green]✓[/green] Unapproved tournament {args.tournament_id} for {args.season}"
            f" (request {result.get('request_id')})"
        )
    return 0


def _cmd_season_banned_dates(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season banned-dates`` — list banned dates."""
    from ..season_state import banned_date_report

    result = banned_date_report(
        season=args.season,
        root=args.root,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        banned_dates = result.get("banned_dates", [])
        violations = result.get("violations", [])
        if banned_dates:
            _console.print(f"[green]✓[/green] Found {len(banned_dates)} banned date(s) for {args.season}")
            for bd in banned_dates:
                _console.print(f"  {bd.get('date')} (request {bd.get('request_id')})")
        else:
            _console.print(f"[green]✓[/green] No banned dates found for {args.season}")
        if violations:
            _console.print(f"[yellow]⚠[/yellow] {len(violations)} tournament(s) violate banned dates:")
            for v in violations:
                _console.print(f"  {v.get('tournament_id')} on {v.get('date')}")
    return 0

def _cmd_season_batch(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season batch`` — atomically compose several scoped canonical mutations in one commit."""
    from ..season_state import batch_maintenance
    from .verification_problem import _canonical_verification_problem
    import json

    # Read operations from file
    try:
        with open(args.operations, 'r') as f:
            operations = json.load(f)
    except Exception as exc:
        _console.print(f"[red]✗[/red] Failed to read operations file '{args.operations}': {exc}")
        return 1

    # Process scope: split comma-separated values if provided
    scope = None
    if args.scope:
        scope = []
        for s in args.scope:
            scope.extend([part.strip() for part in s.split(',') if part.strip()])

    # Call batch_maintenance
    result = batch_maintenance(
        season=args.season,
        operations=operations,
        scope=scope,
        root=args.root,
        problem=_canonical_verification_problem(args.work_dir, args.season, args.root),
        actor=args.actor,
        note=args.note,
        dry_run=args.dry_run,
        request_id=args.request_id,
        allow_manual_placement=args.allow_manual_placement,
        allow_host_confirmation=args.allow_host_confirmation,
        accept_regressions=args.accept_team_regressions,
        accept_regression_reason=args.accept_regression_reason,
        accept_reviewed_consequences=args.accept_reviewed_consequences,
    )

    # Output
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if args.dry_run:
            if result.get("consequence_acceptable"):
                _console.print(f"[green]✓[/green] Batch dry-run successful for season {args.season} (consequence acceptable)")
            else:
                _console.print(f"[yellow]⚠[/yellow] Batch dry-run blocked for season {args.season} (consequence NOT acceptable)")
        else:
            _console.print(f"[green]✓[/green] Batch applied successfully for season {args.season}")

    # Handle --fail-on-blocked
    if args.dry_run and args.fail_on_blocked and not result.get("consequence_acceptable"):
        return 3
    return 0

def _write_canonical_export_evidence(schedule: dict, export_checkpoint: dict) -> None:
    """Write the immutable evidence bundle for a canonical ``season export``.

    Best-effort layered provenance: a bundle failure must never fail or roll
    back an otherwise-successful export, but when it succeeds the committed
    export directory can be reviewed without the original working directory.
    """
    from pathlib import Path

    import json as _json

    from ..pipeline.canonical_export_evidence import build_canonical_export_evidence

    export_dir = export_checkpoint.get("export_dir")
    if not export_dir:
        return
    try:
        bundle = build_canonical_export_evidence(
            schedule=schedule, export_checkpoint=export_checkpoint
        )
        target = Path(str(export_dir))
        target.mkdir(parents=True, exist_ok=True)
        (target / "evidence_bundle.json").write_text(
            _json.dumps(bundle, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001 - evidence is not an export dependency
        _console.print(f"  [yellow]⚠[/yellow] Kunne ikke skrive evidence-bundle: {exc}")


def _cmd_season_export(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season export`` — regenerate Stage 4 exports from canonical season state."""
    import json as _json

    from ..pipeline.stage4_export import run as run_export
    from ..pipeline.state import PipelineState
    from ..season_state import (
        SeasonStateError,
        change_request_ledger,
        effective_config_from_verification_problem,
        load_decisions,
        load_export_context,
        load_schedule,
        planning_checkpoint_from_schedule,
    )

    schedule = load_schedule(args.season, root=args.root)
    decisions = load_decisions(args.season, root=args.root)
    checkpoint = planning_checkpoint_from_schedule(schedule, decisions)
    state = PipelineState(args.work_dir)
    verification_context = schedule.get("verification_context") if isinstance(schedule.get("verification_context"), dict) else {}
    verification_problem = verification_context.get("problem") if isinstance(verification_context, dict) else None
    if not isinstance(verification_problem, dict):
        raise SeasonStateError(
            "Canonical season export requires a promoted verification-context problem; "
            "re-promote from a provenance-bound Stage 4 handoff."
        )
    # The provenance-bound problem is frozen at the season's original
    # promotion time; a later canonical decision (banned date, holiday
    # exception, calendar-booking association, participation
    # withdrawal) never mutates it. Project the *current* canonical
    # overlays into it here -- through the one shared facade -- so this
    # export gate verifies against the same live decisions that season
    # findings/repair-options already accepted, instead of re-litigating
    # against stale policy or the raw registered pool.
    from ..season_maintenance import project_canonical_overlays

    verification_problem = project_canonical_overlays(
        verification_problem,
        decisions=decisions,
        plan=schedule.get("plan") or {},
    )
    # The public/source presentation snapshot is carried with the
    # promoted handoff, so export never reads mutable `.pipeline`
    # scrape state. It is fingerprint-verified before use.
    from ..pipeline.public_export_context import (
        PublicExportContextError,
        resolve_promoted_public_export_context,
    )

    try:
        public_export_context = resolve_promoted_public_export_context(
            schedule,
            load_export_context(args.season, root=args.root),
        )
    except PublicExportContextError as exc:
        raise SeasonStateError(str(exc)) from exc

    from ..pipeline.export_lifecycle import find_published_exports_for_season

    published_exports = find_published_exports_for_season(
        args.season,
        season_root=args.root,
    )
    latest_published_export = published_exports[0] if published_exports else None
    from ..pipeline.export_projection_guard import ExportProjectionError

    # A published baseline that predates ``schedule_projection`` can only
    # be recovered by binding its rows to the canonical plan *at the
    # revision it published*. Resolve that publication-time snapshot
    # explicitly; if it is unavailable the guard fails closed rather
    # than infer stable ids from row order.
    published_canonical_plan = None
    published_canonical_problem = None
    if latest_published_export and not latest_published_export.get("schedule_projection"):
        from ..infrastructure.canonical_revision_history import (
            load_canonical_schedule_at_revision,
        )
        from ..published_baseline import projection_problem_from_schedule

        published_canonical_schedule = load_canonical_schedule_at_revision(
            str(latest_published_export.get("canonical_season") or args.season),
            str(latest_published_export.get("canonical_revision") or ""),
            season_root=args.root,
        )
        if published_canonical_schedule is not None:
            published_canonical_plan = published_canonical_schedule.get("plan")
            published_canonical_problem = projection_problem_from_schedule(published_canonical_schedule)

    try:
        result = run_export(
            checkpoint,
            state=state,
            export_dir=args.export_dir,
            strict=True,
            timestamped_export=args.timestamped_export,
            verification_problem=verification_problem,
            effective_config_override=effective_config_from_verification_problem(verification_problem),
            use_pipeline_metadata=False,
            public_export_context=public_export_context,
            allow_placement_normalization=latest_published_export is None,
            canonical_schedule_plan=schedule.get("plan") if latest_published_export else None,
            published_export_guard=latest_published_export,
            published_canonical_plan=published_canonical_plan,
            published_canonical_problem=published_canonical_problem,
        )
    except ExportProjectionError as exc:
        raise SeasonStateError(str(exc)) from exc
    result["canonical_season"] = args.season
    result["canonical_revision"] = checkpoint.get("canonical_state", {}).get("revision")
    result["season_baseline"] = decisions.get("season_baseline") or None
    # Distinct from the informational `canonical_season`/`canonical_revision`
    # fields above (which the ordinary Stage 4 exporter also sets whenever
    # Stage 3 adopted a promoted season as its baseline): this marker is
    # authoritative and set *only* here, because this code path converts
    # `season/<season>/schedule.json` directly into a Stage 4 export and
    # never touches the Stage3Session/candidate store. The audit/convergence
    # lifecycle uses it to avoid ever routing a promoted-season audit back
    # into an unrelated Stage 3 candidate (issue #397).
    result["is_canonical_season_export"] = True
    # A successful export covers the revision it was generated from, so
    # clear the one-way "fresh export required" latch that a calendar
    # refresh or config reconciliation set. The clearing is revision-
    # bound: if canonical state advanced while this export ran, the
    # newer state's freshness requirement survives and publication
    # keeps refusing the now-stale artifact.
    from ..season_state import mark_export_fresh

    result["export_freshness"] = mark_export_fresh(
        season=args.season,
        root=args.root,
        expected_revision=str(result.get("canonical_revision") or ""),
        export_dir=result.get("export_dir") or args.export_dir,
        note="canonical export",
    )
    # The freshness operation returns the revision it actually bound to.
    # Treat that value as authoritative: a cleared latch only makes this
    # export publishable when it covers the very revision the artifacts
    # were produced from.
    export_freshness = result["export_freshness"]
    bound_revision = str(export_freshness.get("canonical_state_revision") or "")
    result["stale_export"] = not (
        bool(export_freshness.get("cleared"))
        and bound_revision == str(result.get("canonical_revision") or "")
    )
    stale_export = bool(result["stale_export"])
    if stale_export:
        result["errors"] = [
            *(result.get("errors") or []),
            "stale_export: "
            + str(
                result["export_freshness"].get("reason")
                or "canonical state changed during export"
            ),
        ]
    from ..pipeline.state import StageName, StageStatus
    state.write_stage(
        StageName.EXPORT,
        result,
        status=StageStatus.FAILED if stale_export else StageStatus.DONE,
    )
    _write_canonical_export_evidence(schedule, result)
    if not stale_export:
        from ..application.canonical_season.changes import write_change_log_markdown

        ledger = change_request_ledger(args.season, root=args.root)
        result["change_log"] = str(write_change_log_markdown(ledger, root=args.root))
        # Mark audit-required explicitly rather than relying only on lazy
        # export-fingerprint reconciliation: a re-export of unchanged
        # canonical state produces the same content fingerprint, which
        # would otherwise leave a workflow recorded before this export
        # (e.g. one predating the canonical-season scoping above) stale
        # and un-rescoped forever.
        from ..application.audit_lifecycle import mark_audit_required

        mark_audit_required(args.work_dir)
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
    from ..season_state import load_schedule, SeasonStateError

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
    from ..season_state import load_schedule, load_decisions, SeasonStateError

    try:
        schedule = load_schedule(args.season, root=args.root)
        decisions = load_decisions(args.season, root=args.root)
    except SeasonStateError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    from ..season_state import approval_report

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


def _cmd_season_history(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season history`` — show season history."""
    from ..season_state import history_inventory

    result = history_inventory(
        season=args.season,
        root=args.root,
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        entries = result.get("entries", [])
        if entries:
            _console.print(f"[green]✓[/green] History for season {args.season}:")
            for entry in entries:
                _console.print(f"  {entry.get('timestamp')}: {entry.get('description')}")
        else:
            _console.print(f"[green]✓[/green] No history entries for season {args.season}")
    return 0


def _cmd_season_compact_history(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season compact-history`` — show compact season history."""
    from ..season_state import compact_history

    result = compact_history(
        season=args.season,
        root=args.root,
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        entries = result.get("entries", [])
        if entries:
            _console.print(f"[green]✓[/green] Compact history for season {args.season}:")
            for entry in entries:
                _console.print(f"  {entry.get('timestamp')}: {entry.get('description')}")
        else:
            _console.print(f"[green]✓[/green] No compact history entries for season {args.season}")
    return 0


def _cmd_season_tourney_inspection(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season tourney-inspection`` — inspect tournament details."""
    from ..season_state import tournament_inspection

    result = tournament_inspection(
        season=args.season,
        tournament_id=args.tournament_id,
        root=args.root,
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if result:
            _console.print(f"[green]✓[/green] Inspection for tournament {args.tournament_id} in season {args.season}:")
            for key, value in result.items():
                _console.print(f"  {key}: {value}")
        else:
            _console.print(f"[yellow]⚠[/yellow] No inspection data found for tournament {args.tournament_id}")
    return 0


def _cmd_season_placement_infeasibility(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season placement-infeasibility`` — show placement infeasibility details."""
    from ..season_state import placement_infeasibility_report

    result = placement_infeasibility_report(
        season=args.season,
        root=args.root,
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if result:
            _console.print(f"[green]✓[/green] Placement infeasibility report for season {args.season}:")
            for key, value in result.items():
                _console.print(f"  {key}: {value}")
        else:
            _console.print(f"[yellow]⚠[/yellow] No placement infeasibility data for season {args.season}")
    return 0


def _cmd_season_normalize_placements(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season normalize-placements`` — normalize placements."""
    from ..season_state import normalize_placements

    result = normalize_placements(
        season=args.season,
        root=args.root,
        actor=args.actor,
        note=args.note,
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(f"[green]✓[/green] Normalized placements for season {args.season}")
        if result.get("changes_made"):
            _console.print(f"  Changes made: {result['changes_made']}")
    return 0


def _cmd_season_normalize_arena_identities(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season normalize-arena-identities`` — normalize arena identities."""
    from ..season_state import normalize_arena_identities

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


def _cmd_season_decision_ledger(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season decision-ledger`` — show decision ledger."""
    from ..season_state import change_request_ledger

    result = change_request_ledger(
        season=args.season,
        root=args.root,
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        entries = result.get("entries", [])
        if entries:
            _console.print(f"[green]✓[/green] Decision ledger for season {args.season}:")
            for entry in entries:
                _console.print(f"  {entry.get('timestamp')}: {entry.get('description')}")
        else:
            _console.print(f"[green]✓[/green] No decision ledger entries for season {args.season}")
    return 0


def _cmd_season_guest_slot_report(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season guest-slot-report`` — show guest slot report."""
    from ..season_state import guest_slot_report

    result = guest_slot_report(
        season=args.season,
        root=args.root,
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        report = result.get("report", {})
        if report:
            _console.print(f"[green]✓[/green] Guest slot report for season {args.season}:")
            for key, value in report.items():
                _console.print(f"  {key}: {value}")
        else:
            _console.print(f"[yellow]⚠[/yellow] No guest slot data for season {args.season}")
    return 0


def _cmd_season_inspect_tournament(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season inspect tournament`` — inspect tournament details."""
    from ..season_state import tournament_inspection

    result = tournament_inspection(
        season=args.season,
        tournament_id=args.tournament_id,
        root=args.root,
        include_released_constraints=getattr(args, 'all', False),
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if result:
            _console.print(f"[green]✓[/green] Inspection for tournament {args.tournament_id} in season {args.season}:")
            for key, value in result.items():
                _console.print(f"  {key}: {value}")
        else:
            _console.print(f"[yellow]⚠[/yellow] No inspection data found for tournament {args.tournament_id}")
    return 0


def _cmd_season_inspect_constraints(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season inspect constraints`` — inspect request constraints."""
    from ..season_state import constraint_inspection

    result = constraint_inspection(
        season=args.season,
        team=args.team,
        tournament_id=args.tournament_id,
        date=args.date,
        include_released=getattr(args, 'all', False),
        root=args.root,
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if result:
            _console.print(f"[green]✓[/green] Constraint inspection for season {args.season}:")
            for key, value in result.items():
                _console.print(f"  {key}: {value}")
        else:
            _console.print(f"[yellow]⚠[/yellow] No constraint data found for season {args.season}")
    return 0


def _cmd_season_inspect_candidates(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season inspect candidates`` — list replacement candidates."""
    from ..season_state import replacement_candidates

    result = replacement_candidates(
        season=args.season,
        tournament_id=args.tournament_id,
        root=args.root,
        replace_team_label=getattr(args, 'replace_team', None),
        legal_only=getattr(args, 'legal_only', False),
        limit=getattr(args, 'limit', None),
    )
    if args.json:
        import json as _json
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if isinstance(result, list):
            _console.print(f"[green]✓[/green] Found {len(result)} replacement candidates for tournament {args.tournament_id} in season {args.season}:")
            for i, candidate in enumerate(result, 1):
                _console.print(f"  {i}. {candidate}")
        elif result:
            _console.print(f"[green]✓[/green] Replacement candidates for tournament {args.tournament_id} in season {args.season}:")
            for key, value in result.items():
                _console.print(f"  {key}: {value}")
        else:
            _console.print(f"[yellow]⚠[/yellow] No replacement candidates found for tournament {args.tournament_id} in season {args.season}")
    return 0


def _cmd_season_save_placeholder(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season save-placeholder`` — save a placeholder for missing data."""
    # TODO: Implement save-placeholder command
    _console.print("[yellow]⚠[/yellow] Save placeholder command not yet implemented")
    return 0


def _cmd_season_commit_placeholder(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season commit-placeholder`` — commit a placeholder to real data."""
    # TODO: Implement commit-placeholder command
    _console.print("[yellow]⚠[/yellow] Commit placeholder command not yet implemented")
    return 0