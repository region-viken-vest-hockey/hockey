"""
``rvv-miniputt`` — unified CLI for the RVV Miniputt tournament scheduler pipeline.

Provides the commands referenced by the HTML calendar viewer, scraper tools,
and pipeline logs::

    rvv-miniputt status                 Show checkpoint/log status
    rvv-miniputt calendars              Regenerate calendar HTML from cache
    rvv-miniputt calendars --refresh    Full re-scrape: clear caches, scrape, regenerate
    rvv-miniputt run                    Full pipeline: stages 1→4 + HTML views
    rvv-miniputt logs                   Show structured pipeline run logs
    rvv-miniputt cancel                 Cancel a tournament and suggest/reschedule makeup dates
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from ..pipeline.state import PipelineState

from rich.console import Console

from ..application.operator_state import (
    check_operator_health,
    list_operator_questions,
    promote_operator_question,
    record_operator_answer,
)
from .args import build_parser as _build_parser
from .pipeline_orchestrator import (
    _cmd_calendars, _cmd_operator_audit_context, _cmd_operator_audit_evidence,
    _cmd_operator_audit_run, _cmd_operator_audit_submit,
    _cmd_operator_publish,
    _cmd_operator_publish_history,
    _cmd_operator_rollback,
    _cmd_operator_run,
    _cmd_operator_verify,
    _execute_operator_publish,
    _print_pages_result,
    _cmd_run,
    _cmd_scrape,
)
from .plan_command import _cmd_plan
from .recovery_cli import _cmd_recovery_inject, _cmd_recovery_targets, _cmd_scrape_merge
from .waiver_command import _cmd_waiver
from .reporting import _cmd_candidates, _cmd_logs, _cmd_sources_status, _cmd_status

_console = Console()

# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


def _cmd_cancel(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt cancel`` — cancellation and rain-check workflow."""
    from ..pipeline.state import PipelineState
    from ..pipeline.cancellation_workflow import CancellationWorkflow

    work_dir = args.work_dir
    state = PipelineState(work_dir)
    wf = CancellationWorkflow(state)

    # Load the plan first to verify we have something to work with.
    try:
        plan = wf.load_plan()
    except ValueError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    # --- No tournament ID: list available tournaments ---
    if not args.tournament_id:
        _console.print("[bold]Turneringer i sesongplanen:[/bold]\n")
        for t in plan.tournaments:
            status = ""
            if t.cancelled:
                status = f" [red](AVLYST: {t.cancellation_reason or 'ingen grunn'})[/red]"
            _console.print(
                f"  [cyan]{t.id}[/cyan]  {t.date.isoformat()}  "
                f"{t.age_group:5s}  {t.arena:20s}  "
                f"{len(t.teams)} lag{status}"
            )
        _console.print(
            "\nBruk [bold]rvv-miniputt cancel --tournament-id <id> --reason \"...\"[/bold]"
        )
        return 0

    tid = args.tournament_id

    # --- Cancel the tournament ---
    try:
        tournament = wf._find_tournament(plan, tid)
    except ValueError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    if args.reason:
        reason = args.reason
    else:
        _console.print(
            f"[bold]Avlys turnering {tid}[/bold] "
            f"({tournament.age_group}, {tournament.arena}, {tournament.date.isoformat()})"
        )
        reason = _console.input("  Årsak: ").strip()
        if not reason:
            _console.print("[red]✗[/red] Avbrutt — ingen grunn oppgitt.")
            return 1

    cancel_result = wf.mark_cancelled(tid, reason, plan=plan)

    if not cancel_result.success:
        _console.print(f"[yellow]⚠[/yellow] {cancel_result.summary_nb}")
        return 1

    _console.print(f"[green]✓[/green] {cancel_result.summary_nb}")

    # --- Write the plan checkpoint ---
    wf.write_plan(plan, log_entry=cancel_result)
    wf.log_cancellation(cancel_result)

    # --- Handle makeup date ---
    if args.makeup_date:
        try:
            new_date = datetime.strptime(args.makeup_date, "%Y-%m-%d").date()
        except ValueError:
            _console.print(
                f"[red]✗[/red] Ugyldig datoformat '{args.makeup_date}'. Bruk YYYY-MM-DD."
            )
            return 1

        _console.print(f"\n[bold]Flytter til makeup-dato: {new_date.isoformat()}[/bold]")
        move_result = wf.apply_makeup(
            tid, new_date, plan=plan, force=args.force, cascade=True
        )

        if not move_result.success:
            _console.print(f"[red]✗[/red] {move_result.summary_nb}")
            return 1

        _console.print(f"[green]✓[/green] {move_result.summary_nb}")
        wf.write_plan(plan, log_entry=move_result)
    else:
        # Show suggested makeup dates
        _console.print("\n[bold]Foreslåtte makeup-datoer:[/bold]")
        suggestions = wf.suggest_makeup_dates(tournament, plan)

        if not suggestions:
            _console.print(
                "  [dim]Ingen ledige helger funnet i sesongvinduet.[/dim]"
            )
        else:
            for s in suggestions:
                day_nb = ["man", "tir", "ons", "tor", "fre", "lør", "søn"]
                day = day_nb[s.date.weekday()]
                delta = f"+{s.days_from_original}d" if s.days_from_original >= 0 else f"{s.days_from_original}d"
                _console.print(
                    f"  [cyan]{s.date.isoformat()}[/cyan] ({day}, {delta})"
                )
                for c in s.conflicts:
                    _console.print(f"    [dim]Advarsel: {c['reason']}[/dim]")

            _console.print(
                f"\nBruk [bold]rvv-miniputt cancel --tournament-id {tid} "
                f"--makeup-date <dato>[/bold] for å velge en makeup-dato."
            )

    # --- Re-export ---
    if not args.no_export:
        _console.print("\n[bold]Re-eksporterer...[/bold]")
        try:
            export_result = wf.re_export(
                export_dir=args.export_dir,
            )
            files = export_result.get("output_files", {})
            _console.print(f"  [green]✓[/green] {len(files)} fil(er) eksportert")
            for label, path in files.items():
                _console.print(f"    → {path}")
        except Exception as exc:
            _console.print(f"  [red]✗[/red] Eksport feilet: {exc}")
            return 1

    _console.print("\n[bold green]✓ Ferdig.[/bold green]")
    return 0


def _do_re_export(work_dir: str, export_dir: str, *, timestamped_export: bool = False) -> int:
    """Re-export Stage 4 from the current plan checkpoint. Returns exit code."""
    from ..pipeline.state import PipelineState, StageName
    from ..pipeline.stage4_export import run as run_export

    state = PipelineState(work_dir)
    plan_checkpoint = state.read_stage(StageName.PLANNING)
    if not plan_checkpoint:
        _console.print("[red]✗[/red] Ingen Stage 3-plan funnet.")
        return 1

    try:
        result = run_export(plan_checkpoint, state=state, export_dir=export_dir, strict=True, timestamped_export=timestamped_export)
        files = result.get("output_files", {})
        _console.print(f"  [green]✓[/green] {len(files)} fil(er) eksportert")
        for label, path in files.items():
            _console.print(f"    → {path}")
        return 0
    except Exception as exc:
        _console.print(f"  [red]✗[/red] Eksport feilet: {exc}")
        return 1


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


def _load_plan_and_updater(work_dir: str):
    """Load the season plan and return (plan, updater, state). Raises SystemExit on error."""
    from ..pipeline.state import PipelineState
    from ..pipeline.tournament_updater import TournamentUpdater

    state = PipelineState(work_dir)
    updater = TournamentUpdater(state=state)
    try:
        plan = updater.load_plan()
    except ValueError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        sys.exit(1)
    return plan, updater, state


def _cmd_registered_teams(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt registered-teams`` — refresh/publish Påmeldte lag."""
    from pathlib import Path

    from ..pipeline.activity_publish import fetch_pages_branch
    from ..pipeline.registered_teams import (
        RegisteredTeamsPublishError,
        RegisteredTeamsValidationError,
        default_registered_teams_run_id,
        prepare_registered_teams_latest_export,
    )

    _console.print("[bold]🏒 RVV Påmeldte lag[/bold]")

    try:
        if getattr(args, "publish", False) and getattr(args, "push", True):
            fetch_pages_branch(repo_dir=args.repo_dir, remote=args.remote, branch=args.branch)
        config_path = Path(args.config) if getattr(args, "config", None) and Path(args.config).exists() else None
        prepared = prepare_registered_teams_latest_export(
            csv_path=args.csv,
            export_dir=args.export_dir,
            repo_dir=args.repo_dir,
            branch=args.branch,
            config_path=config_path,
            generated_at=getattr(args, "generated_at", None),
            include_latest_base=getattr(args, "base_latest", True),
            require_latest_base=getattr(args, "base_latest", True),
        )
    except RegisteredTeamsValidationError as exc:
        _console.print("[red]✗[/red] Påmeldte lag-validering feilet:")
        for error in exc.errors:
            _console.print(f"  [red]•[/red] {error}")
        return 1
    except RegisteredTeamsPublishError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        _console.print(f"[red]✗[/red] Påmeldte lag feilet: {exc}")
        return 1

    _console.print(
        f"  [green]✓[/green] Staget komplett Pages-snapshot i {prepared['export_dir']} "
        f"({prepared['base_file_count']} eksisterende /latest/-fil(er) kopiert)"
    )
    for _label, path in prepared["registered_team_files"].items():
        _console.print(f"    → {path}")

    if not getattr(args, "publish", False):
        _console.print("  [dim]Ikke publisert. Legg til --publish --confirm-public for å oppdatere GitHub Pages.[/dim]")
        return 0

    args.operator_command = "publish"
    args.export_dir = prepared["export_dir"]
    args.run_id = getattr(args, "run_id", None) or default_registered_teams_run_id()
    args.extra_public_files = getattr(args, "extra_public_files", []) or []
    args.allow_findings = getattr(args, "allow_findings", []) or []

    publish_result = _execute_operator_publish(args)
    if publish_result is None:
        return 1
    return _print_pages_result(publish_result, as_json=getattr(args, "json", False))


def _cmd_activities(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt activities`` — refresh/publish the public activity calendar."""
    from ..pipeline.activity_publish import (
        ActivityPublishError,
        default_activity_run_id,
        fetch_pages_branch,
        prepare_activity_latest_export,
    )
    from ..pipeline.input_workbook import WorkbookInputError

    _console.print("[bold]📅 RVV aktivitetskalender[/bold]")

    try:
        if getattr(args, "publish", False) and getattr(args, "push", True):
            fetch_pages_branch(repo_dir=args.repo_dir, remote=args.remote, branch=args.branch)
        prepared = prepare_activity_latest_export(
            input_path=args.input,
            export_dir=args.export_dir,
            repo_dir=args.repo_dir,
            branch=args.branch,
            default_year=getattr(args, "year", None),
            include_latest_base=getattr(args, "base_latest", True),
            require_latest_base=getattr(args, "base_latest", True),
        )
    except (ActivityPublishError, WorkbookInputError) as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        _console.print(f"[red]✗[/red] Aktivitetskalender feilet: {exc}")
        return 1

    _console.print(
        f"  [green]✓[/green] Staget komplett Pages-snapshot i {prepared['export_dir']} "
        f"({prepared['base_file_count']} eksisterende /latest/-fil(er) kopiert)"
    )
    for label, path in prepared["activity_files"].items():
        _console.print(f"    → {path}")

    if not getattr(args, "publish", False):
        _console.print("  [dim]Ikke publisert. Legg til --publish --confirm-public for å oppdatere GitHub Pages.[/dim]")
        return 0

    # Reuse the canonical operator publish path so the staged snapshot still
    # gets sanitized, approval-gated, fingerprinted, committed/pushed and
    # verified exactly like a normal Pages publication.
    args.operator_command = "publish"
    args.export_dir = prepared["export_dir"]
    args.run_id = getattr(args, "run_id", None) or default_activity_run_id()
    args.extra_public_files = getattr(args, "extra_public_files", []) or []
    args.allow_findings = getattr(args, "allow_findings", []) or []

    publish_result = _execute_operator_publish(args)
    if publish_result is None:
        return 1
    return _print_pages_result(publish_result, as_json=getattr(args, "json", False))


# ---------------------------------------------------------------------------
# operator subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_operator(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator ...`` — dispatches to sub-subcommands."""
    handlers = {
        "run": _cmd_operator_run,
        "questions": _cmd_operator_questions,
        "answer": _cmd_operator_answer,
        "promote": _cmd_operator_promote,
        "health": _cmd_operator_health,
        "publish": _cmd_operator_publish,
        "verify": _cmd_operator_verify,
        "rollback": _cmd_operator_rollback,
        "publish-history": _cmd_operator_publish_history, "audit-context": _cmd_operator_audit_context, "audit-evidence": _cmd_operator_audit_evidence, "audit-submit": _cmd_operator_audit_submit, "audit-run": _cmd_operator_audit_run,
    }
    handler = handlers.get(args.operator_command)
    if handler is not None:
        return handler(args)
    _console.print(
        "[yellow]Bruk: rvv-miniputt operator run|questions|answer|promote|health|publish|verify|"
        "rollback|publish-history[/yellow]"
    )
    return 1


def _cmd_operator_questions(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator questions`` — list escalation questions.

    Defaults to unanswered questions only, matching the pre-#12 behavior;
    ``--all`` also includes answered and stale ones (the full audit trail).
    """
    questions = list_operator_questions(args.work_dir, include_all=getattr(args, "all", False))

    if getattr(args, "json", False):
        import json as _json

        print(_json.dumps([question.to_dict() for question in questions], indent=2, ensure_ascii=False))
        return 0

    if not questions:
        _console.print("Ingen ubesvarte spørsmål.")
        return 0

    _console.print(f"[bold]Spørsmål[/bold] ({len(questions)})\n")
    for question in questions:
        marker = "[yellow]?[/yellow]" if not question.answered else "[green]✓[/green]"
        _console.print(f"{marker} ({question.type}) {question.summary}")
        _console.print(f"    id: [dim]{question.id}[/dim]")
        if question.scope != "workspace":
            _console.print(f"    [dim]scope: {question.scope} ({question.scope_key})[/dim]")
        if question.stale:
            _console.print(f"    [red]FORELDET:[/red] {question.stale_reason}")
        if question.answered:
            _console.print(f"    [green]Svar: {question.answer}[/green]")
        if question.context:
            _console.print(f"    Kontekst: {question.context}")
        if question.recommendation:
            _console.print(f"    [cyan]Anbefaling: {question.recommendation}[/cyan]")
        if question.impact:
            _console.print(f"    Konsekvens: {question.impact}")
        for alt in question.alternatives:
            _console.print(f"    · {alt}")
        if not question.answered:
            _console.print(f"    [dim]Svar med: rvv-miniputt operator answer {question.id} \"<svar>\"[/dim]")
        _console.print("")
    return 0


def _cmd_operator_answer(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator answer <id> <answer>`` — record a durable decision."""
    try:
        entry = record_operator_answer(
            args.work_dir, args.question_id, args.answer, decided_by=getattr(args, "decided_by", None)
        )
    except ValueError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    _console.print(f"[green]✓[/green] Registrert svar på spørsmål {entry.id}: {entry.answer}")
    _console.print(
        "[dim]Kjør 'rvv-miniputt operator run' på nytt for å fortsette der pipelinen stoppet.[/dim]"
    )
    return 0


def _cmd_operator_promote(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator promote <id> <scope>`` — broaden a decision's scope (issue #12)."""
    try:
        entry = promote_operator_question(
            args.work_dir,
            args.question_id,
            args.scope,
            scope_key=getattr(args, "scope_key", "") or "",
            decided_by=getattr(args, "decided_by", None),
        )
    except ValueError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    _console.print(
        f"[green]✓[/green] Forfremmet spørsmål {args.question_id} til scope '{entry.scope}' (ny id: {entry.id})"
    )
    return 0


def _cmd_operator_health(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator health`` — the operator-state health check (issue #14)."""
    result = check_operator_health(args.work_dir)

    if getattr(args, "json", False):
        import json as _json

        print(_json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return result.exit_code

    if result.healthy:
        _console.print("[green]✓[/green] Run manifest er sunn og skrivbar.")
        return 0
    if result.writable:
        _console.print(f"[yellow]⚠[/yellow] Run manifest ble gjenopprettet: {result.detail}")
        recovery = result.manifest_recovery or {}
        if recovery.get("backup_path"):
            _console.print(f"    [dim]Sikkerhetskopi av skadet fil: {recovery['backup_path']}[/dim]")
        return 1
    _console.print(f"[red]✗[/red] Run manifest kan ikke skrives: {result.detail}")
    return 1


# ---------------------------------------------------------------------------
# sources subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_sources(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt sources ...`` — dispatches to sub-subcommands."""
    if args.sources_command == "status":
        return _cmd_sources_status(args)
    _console.print("[yellow]Bruk: rvv-miniputt sources status[/yellow]")
    return 1


def _cmd_registrations(args: argparse.Namespace) -> int:
    """Handle reviewed SharePoint registration import commands."""
    from ..registrations import (
        RegistrationImportError,
        export_registrations,
        format_registration_summary,
        validate_registrations,
    )

    try:
        if args.registrations_command == "validate":
            result = validate_registrations(args.source, input_path=args.input)
        elif args.registrations_command == "export":
            result = export_registrations(
                args.source,
                input_path=args.input,
                output_path=args.output,
                dry_run=getattr(args, "dry_run", False),
            )
        else:
            _console.print("[yellow]Bruk: rvv-miniputt registrations validate|export[/yellow]")
            return 1
    except RegistrationImportError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    _console.print(f"[green]✓[/green] {format_registration_summary(result)}")
    return 0


# ---------------------------------------------------------------------------
# tournament subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_tournament(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt tournament ...`` — dispatches to sub-subcommands."""
    if args.t_command == "list":
        return _cmd_tournament_list(args)
    elif args.t_command == "add":
        return _cmd_tournament_add(args)
    elif args.t_command == "remove":
        return _cmd_tournament_remove(args)
    elif args.t_command == "cancel":
        return _cmd_cancel(args)  # reuse existing cancel handler
    else:
        _console.print("[yellow]Bruk: rvv-miniputt tournament {list|add|remove|cancel}[/yellow]")
        return 1


def _cmd_tournament_list(args: argparse.Namespace) -> int:
    """List all tournaments in the season plan."""
    plan, _updater, _state = _load_plan_and_updater(args.work_dir)

    _console.print("[bold]Turneringer i sesongplanen[/bold]")
    if not plan.tournaments:
        _console.print("  [dim]Ingen turneringer i planen.[/dim]")
        return 0

    _console.print(f"  {len(plan.tournaments)} turneringer")
    if plan.start_date and plan.end_date:
        _console.print(f"  Sesong: {plan.start_date.isoformat()} → {plan.end_date.isoformat()}")
    _console.print()

    for t in plan.tournaments:
        status = ""
        if t.cancelled:
            status = f" [red](AVLYST: {t.cancellation_reason or 'ingen grunn'})[/red]"
        _console.print(
            f"  [cyan]{t.id}[/cyan]  {t.date.isoformat()}  "
            f"{t.age_group:5s}  {t.arena:20s}  "
            f"{len(t.teams)} lag  ({len(t.games)} kamper){status}"
        )
        _console.print(f"       Lag: {', '.join(t.label for t in t.teams)}")

    return 0


def _cmd_tournament_add(args: argparse.Namespace) -> int:
    """Add a new tournament to the season plan."""
    from datetime import date

    plan, updater, _state = _load_plan_and_updater(args.work_dir)

    # Parse date
    try:
        tournament_date = date.fromisoformat(args.date)
    except ValueError:
        _console.print(f"[red]✗[/red] Ugyldig datoformat '{args.date}'. Bruk YYYY-MM-DD.")
        return 1

    # Parse teams
    team_labels = [t.strip() for t in args.teams.split(",") if t.strip()]
    if len(team_labels) < 2:
        _console.print(f"[red]✗[/red] Trenger minst 2 lag. Fikk: {team_labels}")
        return 1

    _console.print(
        f"[bold]Legger til turnering:[/bold] {args.age_group} "
        f"på {tournament_date.isoformat()} i {args.arena}"
    )
    _console.print(f"  Lag ({len(team_labels)}): {', '.join(team_labels)}")

    result = updater.add_tournament(
        plan=plan,
        age_group=args.age_group,
        team_labels=team_labels,
        tournament_date=tournament_date,
        arena=args.arena,
        host_club=args.host_club,
        force=args.force,
    )

    if not result.success:
        _console.print(f"[red]✗[/red] {result.summary_nb}")
        return 1

    updater.write_updated_checkpoint(plan, log_entry=result)
    updater.log_update(result)

    _console.print(f"[green]✓[/green] {result.summary_nb}")

    # Re-export
    _console.print("\n[bold]Re-eksporterer...[/bold]")
    return _do_re_export(args.work_dir, args.export_dir, timestamped_export=getattr(args, 'timestamped_export', False))


def _cmd_tournament_remove(args: argparse.Namespace) -> int:
    """Remove a tournament entirely from the season plan."""
    plan, updater, _state = _load_plan_and_updater(args.work_dir)

    tournament_id = args.tournament_id
    _console.print(f"[bold]Fjerner turnering {tournament_id}...[/bold]")

    try:
        result = updater.remove_tournament(plan, tournament_id)
    except ValueError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    updater.write_updated_checkpoint(plan, log_entry=result)
    updater.log_update(result)

    _console.print(f"[green]✓[/green] {result.summary_nb}")

    # Re-export
    _console.print("\n[bold]Re-eksporterer...[/bold]")
    return _do_re_export(args.work_dir, args.export_dir, timestamped_export=getattr(args, 'timestamped_export', False))


def _cmd_replan(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt replan`` — one-shot cancel + move + re-export."""
    from datetime import date
    from ..pipeline.cancellation_workflow import CancellationWorkflow

    if not args.new_date and not args.suggest:
        _console.print("[red]✗[/red] Angi --new-date <YYYY-MM-DD> eller --suggest.")
        return 1

    plan, _updater, state = _load_plan_and_updater(args.work_dir)
    wf = CancellationWorkflow(state)

    tid = args.tournament_id

    # Find and describe the tournament
    try:
        tournament = wf._find_tournament(plan, tid)
    except ValueError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    _console.print(
        f"[bold]Replan:[/bold] {tid} ({tournament.age_group}, {tournament.arena}, "
        f"{tournament.date.isoformat()})"
    )

    # --- Suggest mode ---
    if args.suggest:
        _console.print("\n[bold]Foreslåtte datoer:[/bold]")
        suggestions = wf.suggest_makeup_dates(tournament, plan)
        if not suggestions:
            _console.print("  [dim]Ingen ledige helger funnet.[/dim]")
        else:
            for s in suggestions:
                day_nb = ["man", "tir", "ons", "tor", "fre", "lør", "søn"]
                day = day_nb[s.date.weekday()]
                delta = f"+{s.days_from_original}d" if s.days_from_original >= 0 else f"{s.days_from_original}d"
                _console.print(f"  [cyan]{s.date.isoformat()}[/cyan] ({day}, {delta})")
                for c in s.conflicts:
                    _console.print(f"    [dim]Advarsel: {c['reason']}[/dim]")
        _console.print("\nBruk --new-date <dato> for å velge en dato.")
        return 0

    # --- Apply move mode ---
    try:
        new_date_obj = date.fromisoformat(args.new_date)
    except ValueError:
        _console.print(f"[red]✗[/red] Ugyldig datoformat '{args.new_date}'. Bruk YYYY-MM-DD.")
        return 1

    _console.print(f"  Ny dato: {new_date_obj.isoformat()}")

    reason = args.reason or "Replan via rvv-miniputt replan"

    # Apply the date move directly (does not require cancellation first —
    # just moves the tournament to the new date with conflict checking).
    move_result = wf.apply_makeup(
        tid, new_date_obj, plan=plan, force=args.force, cascade=True
    )

    if not move_result.success:
        _console.print(f"[red]✗[/red] {move_result.summary_nb}")
        return 1

    _console.print(f"[green]✓[/green] {move_result.summary_nb}")

    wf.write_plan(plan, log_entry=move_result)

    # Re-export
    _console.print("\n[bold]Re-eksporterer...[/bold]")
    return _do_re_export(args.work_dir, args.export_dir, timestamped_export=getattr(args, 'timestamped_export', False))


def _cmd_adjust(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt adjust`` — manual organizer adjustment loop."""
    from ..pipeline.manual_adjustment_workflow import ManualAdjustmentWorkflow

    plan, updater, state = _load_plan_and_updater(args.work_dir)
    requested = {
        "locked_dates": args.lock_date or [],
        "banned_dates": args.ban_date or [],
        "pinned_tournament_ids": args.pin_tournament or [],
        "forced_host_clubs": args.force_host_club or [],
        "excluded_host_clubs": args.exclude_host_club or [],
    }
    plan.manual_adjustments = ManualAdjustmentWorkflow.merge_manual_adjustments(
        plan.manual_adjustments,
        requested,
    )

    workflow = ManualAdjustmentWorkflow(state=state, updater=updater)
    try:
        result = workflow.apply(plan)
    except ValueError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1
    if not result.success:
        _console.print(f"[red]✗[/red] {result.summary_nb}")
        return 1

    updater.persist_update(plan, result)
    _console.print(f"[green]✓[/green] {result.summary_nb}")
    for warning in result.post_patch_warnings:
        _console.print(f"[yellow]⚠[/yellow] {warning}")

    _console.print("\n[bold]Re-eksporterer...[/bold]")
    return _do_re_export(
        args.work_dir,
        args.export_dir,
        timestamped_export=getattr(args, "timestamped_export", False),
    )


def _cmd_review(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt review`` — apply club responses and re-export."""
    from .review_command import ReviewCommand

    cmd = ReviewCommand()
    return cmd.run(
        args.response,
        work_dir=args.work_dir,
        export_dir=args.export_dir,
        timestamped_export=args.timestamped_export,
    )


def _cmd_scrape_llm(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt scrape-llm`` — browser-tool capability guidance."""
    from ..club_registry import club_for_source_name
    from ..pipeline.scraper_strategies import STRATEGIES, get_strategy, needs_llm_agent

    requested_club = args.club.strip()
    club_name = club_for_source_name(requested_club) or requested_club
    if club_name != requested_club:
        _console.print(f"[dim]Alias resolved:[/dim] {requested_club} → {club_name}")

    strategy = get_strategy(club_name)
    if strategy is None:
        _console.print(f"[red]✗[/red] Ukjent klubb: '{requested_club}'")
        _console.print("\n[bold]Kjente skrapestrategier:[/bold]")
        for name in sorted(STRATEGIES):
            _console.print(f"  [cyan]{name}[/cyan]")
        return 1

    _console.print(f"[bold]LLM-guidet recovery:[/bold] {club_name}")
    _console.print(f"  URL: [dim]{strategy.url}[/dim]")
    _console.print(f"  Engine: [dim]{strategy.engine.value}[/dim]")
    if strategy.note:
        _console.print(f"  [dim]{strategy.note}[/dim]")

    if not needs_llm_agent(strategy):
        _console.print("\n[yellow]![/yellow] Denne kilden har allerede en deterministisk skraper.")
        _console.print(
            f"  Bruk [bold]rvv-miniputt scrape --club \"{requested_club}\"[/bold] i stedet."
        )
        _console.print(
            "  Hvis du bare har terminal og trenger å fylle cache på nytt, bruk [bold]rvv-miniputt recovery-targets[/bold] for å finne blokkerte kilder og [bold]rvv-miniputt recovery-inject --source \"<navn>\"[/bold] når du har event-JSON."
        )
        return 1

    _console.print(
        "\n[yellow]![/yellow] Denne kommandoen krever browser-verktøy: Pi ScraperAgent + Playwright browser_worker, "
        "eller et annet allerede browser-aktivert harness."
    )
    _console.print(
        "  Pi: bruk [bold]/rvv-miniputt scrape-llm[/bold] i en Pi-session med [bold]rvv_miniputt_scrape_llm[/bold]."
    )
    _console.print(
        "  Browser-aktivert Claude/OpenCode/Codex: fungerer bare hvis sesjonen allerede har browser-kontroll."
    )
    _console.print(
        "  Rent terminal/CI: kan ikke drive siden direkte; bruk [bold]rvv-miniputt recovery-targets[/bold] for å liste blokkede kilder, og [bold]rvv-miniputt recovery-inject --source \"<navn>\"[/bold] når du har event-JSON fra et eget script eller WebFetch."
    )
    if strategy.credential_env_vars:
        _console.print(
            f"  Krever miljøvariabler: {', '.join(strategy.credential_env_vars)}"
        )
    if strategy.initial_navigation:
        _console.print(
            f"  Oppstartssekvens: {len(strategy.initial_navigation)} steg før agent-løkken."
        )
    _console.print(
        f"  For strategi-JSON: [bold]python3 -m tournament_scheduler.pipeline.scraper_strategies --name \"{club_name}\"[/bold]"
    )
    return 1


def _cmd_verdict(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt verdict`` — print tone and key scores from Stage 3 checkpoint."""
    from ..html.data_computation import (
        compute_club_stats,
        compute_team_game_counts,
        compute_team_travel_info,
    )
    from ..html.renderers.judgment import analyze_opinionated_judgment

    season_plan, _updater, _state = _load_plan_and_updater(args.work_dir)

    team_game_counts = compute_team_game_counts(season_plan)
    team_travel_tuple = compute_team_travel_info(season_plan)
    team_travel: dict[str, int] = team_travel_tuple[0]
    club_stats, _missing_hosts = compute_club_stats(season_plan, team_travel)

    result = analyze_opinionated_judgment(
        season_plan,
        team_game_counts=team_game_counts,
        club_stats=club_stats,
        team_travel=team_travel,
    )

    tone: str = result.get("tone", "unknown")
    tone_label: str = result.get("tone_label", tone.upper())
    verdict: str = result.get("verdict", "")
    action_text: str = result.get("action_text", "")

    # Machine-parseable key=value lines on stdout for skill consumption
    _console.print(f"tone={tone}")
    _console.print(f"tone_label={tone_label}")

    # Extract pairwise/diversity/month_balance from the plan attributes directly
    pairwise = float(getattr(season_plan, "pairwise_matchup_score", 0.0) or 0.0)
    diversity = float(getattr(season_plan, "diversity_score", 0.0) or 0.0)
    month_balance = float(getattr(season_plan, "month_balance_score", 0.0) or 0.0)
    fairness_gate = (
        getattr(season_plan, "fairness_gate", {})
        if isinstance(getattr(season_plan, "fairness_gate", {}), dict)
        else {}
    )
    gate_score = int(fairness_gate.get("score", 0) or 0)
    gate_status = str(fairness_gate.get("status", "pass")).lower()

    _console.print(f"pairwise_matchup_score={pairwise:.4f}")
    _console.print(f"diversity_score={diversity:.4f}")
    _console.print(f"month_balance_score={month_balance:.4f}")
    _console.print(f"fairness_gate_score={gate_score}")
    _console.print(f"fairness_gate_status={gate_status}")
    _console.print(f"verdict={verdict}")
    _console.print(f"action_text={action_text}")

    return 0


def _cmd_critic(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt critic`` — print plan critic issues for existing Stage 3 checkpoint."""
    from ..pipeline.state import PipelineState
    from ..pipeline.tournament_updater import TournamentUpdater
    from .plan_critic import generate_critic_summary

    state = PipelineState(args.work_dir)
    try:
        season_plan = TournamentUpdater(state=state).load_plan()
    except ValueError:
        _console.print(
            f"[red]✗[/red] Ingen Stage 3-checkpoint funnet i '{args.work_dir}'. "
            "Kjør ``rvv-miniputt run`` først."
        )
        return 1

    issues = generate_critic_summary(season_plan)
    if issues:
        _console.print("[bold cyan]Plan critic — problemer funnet:[/bold cyan]")
        for issue in issues:
            _console.print(f"  [cyan]•[/cyan] {issue}")
    else:
        _console.print("[bold cyan]Plan critic:[/bold cyan] [green]Ingen problemer oppdaget.[/green]")
    return 0


def _load_critic_state(
    state: "PipelineState",
    work_dir: str,
) -> "tuple[object | None, list[str]]":
    """Reload the Stage 3 checkpoint and return (season_plan, issues).

    Returns (None, []) when no checkpoint exists so callers can detect and abort.
    """
    from .plan_critic import generate_critic_summary
    from ..pipeline.tournament_updater import TournamentUpdater

    try:
        season_plan = TournamentUpdater(state=state).load_plan()
    except ValueError:
        return None, []
    issues = generate_critic_summary(season_plan)
    return season_plan, issues


def _cmd_auto_adjust(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt auto-adjust`` — automated adjustment loop.

    Each iteration:
      1. Reload the Stage 3 checkpoint and re-run the plan critic.
      2. Break early if ``count_critic_issues_from_dict`` returns 0.
      3. Translate the first auto-fixable issue to a concrete move via ``suggest_moves``.
      4. Apply the move by calling ``_cmd_replan`` internally.
      5. Reload checkpoint and re-evaluate before the next iteration.

    Repeats until all auto-fixable issues are resolved or ``--max-iterations``
    is reached.  Non-auto-fixable issues are collected and printed at the end.
    """
    from ..pipeline.state import PipelineState
    from .plan_critic import count_issues_from_plan, suggest_moves

    state = PipelineState(args.work_dir)
    max_iter = getattr(args, "max_iterations", 3)

    _console.print(
        f"[bold cyan]Auto-adjust:[/bold cyan] starter justeringsløkke "
        f"(max {max_iter} iterasjoner)…"
    )

    applied_total = 0
    manual_issues: list = []
    iteration = 0
    # Track recently-moved IDs (window=2) to break A↔B cascade cycles
    recently_moved: list[str] = []
    _CYCLE_WINDOW = 2

    for iteration in range(1, max_iter + 1):
        # Reload checkpoint and re-run critic at the start of every iteration
        season_plan, issues = _load_critic_state(state, args.work_dir)
        if season_plan is None:
            _console.print(
                f"[red]✗[/red] Ingen Stage 3-checkpoint funnet i '{args.work_dir}'. "
                "Kjør ``rvv-miniputt run`` først."
            )
            return 1

        # Use count_issues_from_plan as the fast early-exit check
        from ..pipeline.state import StageName
        raw_checkpoint = state.read_stage(StageName.PLANNING)
        plan_raw = (raw_checkpoint or {}).get("plan") if isinstance(raw_checkpoint, dict) else None
        issue_count = count_issues_from_plan(plan_raw) if plan_raw is not None else len(issues)

        if issue_count == 0:
            _console.print(
                f"[green]✓[/green] Ingen problemer funnet etter {iteration - 1} iterasjon(er)."
            )
            break

        moves = suggest_moves(season_plan, issues)
        auto_moves = [m for m in moves if m["can_auto_fix"] and m["tournament_id"]]
        manual_moves = [m for m in moves if not m["can_auto_fix"]]

        # Collect manual-review issues (deduplicated across iterations)
        for m in manual_moves:
            if m["issue"] not in [mi["issue"] for mi in manual_issues]:
                manual_issues.append(m)

        # Skip tournament IDs cascade-placed in recent iterations to break A↔B cycles
        fresh_moves = [m for m in auto_moves if m["tournament_id"] not in recently_moved]
        if not fresh_moves:
            # All candidates were recently moved — cycle detected, clear window and retry
            recently_moved.clear()
            fresh_moves = auto_moves

        if not fresh_moves:
            _console.print(
                f"[yellow]![/yellow] Iterasjon {iteration}: ingen auto-fikserbare problemer "
                f"gjenstår ({issue_count} problem(er) krever manuell behandling)."
            )
            break

        _console.print(
            f"\n[bold]Iterasjon {iteration}/{max_iter}[/bold] — "
            f"{issue_count} problem(er), {len(auto_moves)} auto-fikserbar(e):"
        )

        # Apply ONE move per iteration, then reload and re-evaluate
        move = fresh_moves[0]
        tid = move["tournament_id"]
        new_date = move["new_date"]
        reason = move["reason"]

        _console.print(f"  [cyan]→[/cyan] Turneringsid {tid}: flyttes til {new_date}")
        _console.print(f"    [dim]{reason}[/dim]")

        replan_args = argparse.Namespace(
            tournament_id=tid,
            new_date=new_date,
            suggest=False,
            reason=reason,
            force=True,
            work_dir=args.work_dir,
            export_dir=args.export_dir,
            timestamped_export=getattr(args, "timestamped_export", False),
        )
        # Snapshot dates before replan so we can detect cascade victims afterward
        pre_dates = {t.id: t.date for t in season_plan.tournaments}
        rc = _cmd_replan(replan_args)
        if rc == 0:
            applied_total += 1
            # Detect all tournaments whose dates changed (both the moved one and
            # any cascade victims) and add them to the cycle-detection window.
            post_plan, _ = _load_critic_state(state, args.work_dir)
            if post_plan is not None:
                for t in getattr(post_plan, "tournaments", []):
                    if pre_dates.get(t.id) != t.date:
                        if t.id not in recently_moved:
                            recently_moved.append(t.id)
            if len(recently_moved) > _CYCLE_WINDOW * 4:
                recently_moved = recently_moved[-(_CYCLE_WINDOW * 4):]
            # Reload and re-evaluate immediately so the next iteration starts fresh
            _, refreshed_issues = _load_critic_state(state, args.work_dir)
            remaining = len(refreshed_issues)
            _console.print(
                f"  [green]✓[/green] Endring brukt — "
                f"{remaining} problem(er) gjenstår etter reload."
            )
        else:
            _console.print(
                f"  [red]✗[/red] Kunne ikke flytte {tid} — avbryter løkken."
            )
            break
    else:
        _console.print(
            f"[yellow]![/yellow] Maks iterasjoner ({max_iter}) nådd — "
            "noen problemer kan gjenstå."
        )

    # Summary
    _console.print(
        f"\n[bold]Auto-adjust ferdig:[/bold] {applied_total} endring(er) brukt "
        f"over {iteration} iterasjon(er)."
    )

    # Collect any remaining unresolved issues after the loop
    _, remaining_issues = _load_critic_state(state, args.work_dir)
    if remaining_issues:
        remaining_moves = []
        if remaining_issues:
            # We need a plan object for suggest_moves — reload once more
            from ..pipeline.state import StageName as _SN
            _chk = state.read_stage(_SN.PLANNING)
            _sp = _chk.get("plan") if isinstance(_chk, dict) else None
            if _sp is not None:
                from .plan_critic import suggest_moves as _sm
                remaining_moves = _sm(_sp, remaining_issues)

        _print_escalation_table(remaining_issues, remaining_moves, manual_issues)

    elif manual_issues:
        # No remaining auto-fixable issues but there are known manual ones
        _print_escalation_table([], [], manual_issues)

    return 0


def _print_escalation_table(
    remaining_issues: list,
    remaining_moves: list,
    manual_issues: list,
) -> None:
    """Print a Rich-formatted escalation table for issues that could not be auto-fixed.

    ``remaining_issues`` are issues still present after the loop.
    ``remaining_moves`` are the move proposals for those issues (may be empty).
    ``manual_issues`` are issues collected during the loop that were flagged as
    non-auto-fixable from the start.
    """
    from rich import box
    from rich.panel import Panel
    from rich.table import Table

    # Merge remaining + manual, deduplicated by issue string
    seen: set = set()
    rows: list = []

    move_by_issue: dict = {m["issue"]: m for m in remaining_moves}

    for issue in remaining_issues:
        if issue not in seen:
            seen.add(issue)
            m = move_by_issue.get(issue)
            rows.append(
                (
                    issue,
                    m["reason"] if m else "Ikke analysert",
                    "Ja" if (m and m["can_auto_fix"]) else "Nei",
                )
            )

    for mi in manual_issues:
        if mi["issue"] not in seen:
            seen.add(mi["issue"])
            rows.append((mi["issue"], mi["reason"], "Nei"))

    if not rows:
        return

    table = Table(
        title="Uløste problemer — manuell gjennomgang nødvendig",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold yellow",
        expand=True,
    )
    table.add_column("Problem", style="yellow", ratio=4)
    table.add_column("Foreslått tiltak", style="dim", ratio=5)
    table.add_column("Auto-fikserbar?", style="cyan", ratio=1, justify="center")

    for problem, action, auto in rows:
        table.add_row(problem, action, auto)

    panel = Panel(
        table,
        title="[bold red]Eskalering — disse problemene krever manuell handling[/bold red]",
        border_style="red",
        box=box.ROUNDED,
    )
    _console.print()
    _console.print(panel)


def _canonical_verification_problem(
    work_dir: str, season: str | None = None, root: str | None = None
) -> dict | None:
    """Best-effort full ``planning_problem`` for canonical approval/move gates.

    Reconstructs the same problem contract Stage 3 was given (including any
    canonical baseline locks and active operator waivers) so approving or
    moving a canonical tournament is checked against the real hard
    invariants, not only self-consistency.  Returns ``None`` when the inputs
    cannot be reconstructed, degrading to self-consistency verification.  When
    *season*/*root* are given, an unrelated ``--work-dir`` pipeline (a
    different season's config) is ignored rather than applied to this season.
    """
    from datetime import date as _date

    from ..pipeline.stage1_config import load_effective_config
    from ..pipeline.stage4_export_verification import _build_export_verification_problem
    from ..pipeline.state import PipelineState

    state = PipelineState(work_dir)
    try:
        effective_config = load_effective_config(state)
    except Exception:
        effective_config = {}
    if not effective_config:
        return None
    if season:
        start_raw = effective_config.get("start_date")
        end_raw = effective_config.get("end_date")
        if not start_raw or not end_raw:
            return None
        try:
            from ..canonical_baseline import resolve_canonical_season

            resolved = resolve_canonical_season(
                effective_config,
                _date.fromisoformat(str(start_raw)),
                _date.fromisoformat(str(end_raw)),
                root=root,
            )
        except Exception:
            return None
        if resolved != season:
            return None
    return _build_export_verification_problem(effective_config, state)


def _format_delta(delta: dict | None) -> str:
    """Compact before/after summary for a season maintenance action."""
    if not delta:
        return ""
    parts = [
        f"endrede turneringer: {delta.get('changed_tournament_count', 0)}",
        f"hard-feil: {delta.get('hard_violations_before', 0)} -> {delta.get('hard_violations_after', 0)}",
        (
            "uoppfylte hostingkrav: "
            f"{delta.get('unresolved_hosting_obligations_before', 0)} -> "
            f"{delta.get('unresolved_hosting_obligations_after', 0)}"
        ),
        (
            "hostingbalanse-avvik: "
            f"{delta.get('hosting_balance_imbalances_before', 0)} -> "
            f"{delta.get('hosting_balance_imbalances_after', 0)}"
        ),
        (
            "deltakelsesavvik: "
            f"{delta.get('participation_deviations_before', 0)} -> "
            f"{delta.get('participation_deviations_after', 0)}"
        ),
        (
            "reise (km): "
            f"{delta.get('total_travel_km_before', 0):.0f} -> "
            f"{delta.get('total_travel_km_after', 0):.0f} "
            f"({delta.get('total_travel_km_delta', 0):+.0f})"
        ),
        (
            "kvalitetsregresjoner: "
            f"{len(delta.get('quality_regressions') or [])}"
        ),
    ]
    return "; ".join(parts)


def _cmd_season(args: argparse.Namespace) -> int:
    """Handle canonical Git-backed season-state commands."""
    import json as _json

    from ..pipeline.stage4_export import run as run_export
    from ..pipeline.state import PipelineState
    from ..season_state import (
        SeasonStateError,
        approval_report,
        approve_tournament,
        decisions_path,
        fill_guest_slot,
        guest_slot_candidates,
        guest_slot_report,
        move_tournament,
        load_decisions,
        load_export_context,
        load_schedule,
        normalize_placements,
        effective_config_from_verification_problem,
        planning_checkpoint_from_schedule,
        promote_from_stage3,
        release_guest_slot,
        reserve_guest_slot,
        schedule_path,
        unapprove_tournament,
    )
    from ..season_maintenance import SeasonMaintenanceError

    try:
        if args.season_command == "promote":
            schedule, decisions = promote_from_stage3(
                work_dir=args.work_dir,
                season=args.season,
                root=args.root,
                actor=args.actor,
                force=args.force,
            )
            summary = {
                "season": schedule["season"],
                "schedule_path": str(schedule_path(schedule["season"], root=args.root)),
                "decisions_path": str(decisions_path(schedule["season"], root=args.root)),
                "fingerprint": schedule["fingerprint"],
                "tournament_count": len(schedule["plan"].get("tournaments", [])),
                "decision_count": len(decisions.get("decisions", {})),
            }
            if args.json:
                print(_json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _console.print(
                    f"[green]✓[/green] Promoted canonical season {summary['season']} "
                    f"({summary['tournament_count']} tournaments)"
                )
                _console.print(f"  schedule: {summary['schedule_path']}")
                _console.print(f"  decisions: {summary['decisions_path']}")
                _console.print(f"  revision: {summary['fingerprint']}")
            return 0

        if args.season_command == "export":
            schedule = load_schedule(args.season, root=args.root)
            checkpoint = planning_checkpoint_from_schedule(schedule)
            state = PipelineState(args.work_dir)
            verification_context = schedule.get("verification_context") if isinstance(schedule.get("verification_context"), dict) else {}
            verification_problem = verification_context.get("problem") if isinstance(verification_context, dict) else None
            if not isinstance(verification_problem, dict):
                raise SeasonStateError(
                    "Canonical season export requires a promoted verification-context problem; "
                    "re-promote from a provenance-bound Stage 4 handoff."
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
            )
            result["canonical_season"] = args.season
            result["canonical_revision"] = schedule.get("revision")
            # Distinct from the informational `canonical_season`/`canonical_revision`
            # fields above (which the ordinary Stage 4 exporter also sets whenever
            # Stage 3 adopted a promoted season as its baseline): this marker is
            # authoritative and set *only* here, because this code path converts
            # `season/<season>/schedule.json` directly into a Stage 4 export and
            # never touches the Stage3Session/candidate store. The audit/convergence
            # lifecycle uses it to avoid ever routing a promoted-season audit back
            # into an unrelated Stage 3 candidate (issue #397).
            result["is_canonical_season_export"] = True
            from ..pipeline.state import StageName, StageStatus
            state.write_stage(StageName.EXPORT, result, status=StageStatus.DONE)
            _write_canonical_export_evidence(schedule, result)
            if args.json:
                print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _console.print(
                    f"[green]✓[/green] Exported canonical season {args.season} "
                    f"revision {schedule.get('revision')}"
                )
                for label, path in result.get("output_files", {}).items():
                    _console.print(f"  {label}: {path}")
            return 0

        if args.season_command == "status":
            schedule = load_schedule(args.season, root=args.root)
            decisions = load_decisions(args.season, root=args.root)
            report = approval_report(args.season, root=args.root)
            counts = report["counts"]
            records = decisions.get("decisions", {})
            summary = {
                "season": args.season,
                "schedule_path": str(schedule_path(args.season, root=args.root)),
                "decisions_path": str(decisions_path(args.season, root=args.root)),
                "revision": schedule.get("revision"),
                "fingerprint": schedule.get("fingerprint"),
                "tournament_count": len(schedule.get("plan", {}).get("tournaments", [])),
                "decision_count": len(records),
                "approved_count": counts["approved"],
                "locked_count": counts["locked"],
                "stale_approval_count": counts["stale"],
                "orphaned_approval_count": counts["orphaned"],
                "stale_approvals": report["stale_approvals"],
            }
            if args.json:
                print(_json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _console.print(f"[bold]Canonical season {args.season}[/bold]")
                _console.print(f"  revision: {summary['revision']}")
                _console.print(f"  tournaments: {summary['tournament_count']}")
                _console.print(
                    f"  decisions: {summary['decision_count']} "
                    f"({counts['approved']} approved, {counts['locked']} locked, "
                    f"{counts['stale']} stale)"
                )
                if counts["stale"]:
                    _console.print(
                        "  [yellow]⚠[/yellow] stale approvals (reapprove or unapprove): "
                        + ", ".join(entry["tournament_id"] for entry in report["stale_approvals"])
                    )
            return 0

        if args.season_command == "normalize-placements":
            schedule, decisions = normalize_placements(
                season=args.season,
                root=args.root,
                actor=args.actor,
                note=args.note,
                dry_run=args.dry_run,
            )
            report = schedule.get("placement_normalization") or {}
            summary = {
                "season": args.season,
                "dry_run": bool(args.dry_run),
                "changed": bool(report.get("changed", schedule.get("normalized_from") is not None)),
                "tournament_count": len(schedule.get("plan", {}).get("tournaments", [])),
                "removed_tournament_ids": (
                    report.get("removed_tournament_ids")
                    or (schedule.get("normalized_from") or {}).get("removed_tournament_ids", [])
                ),
                "obligation_count": (
                    report.get("obligation_count")
                    or len(schedule.get("plan", {}).get("unresolved_tournament_placements", []))
                ),
                "revision": schedule.get("revision"),
            }
            if args.json:
                print(_json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                action = "would remove" if args.dry_run else "removed"
                _console.print(
                    f"[green]✓[/green] Placement normalization for {args.season}: "
                    f"{action} {len(summary['removed_tournament_ids'])} unplaced tournament(s)"
                )
                _console.print(
                    f"  tournaments: {summary['tournament_count']} · "
                    f"obligations: {summary['obligation_count']}"
                )
                if summary["removed_tournament_ids"]:
                    _console.print(
                        "  unplaced ids: " + ", ".join(summary["removed_tournament_ids"])
                    )
            return 0

        if args.season_command == "approvals":
            report = approval_report(args.season, root=args.root)
            if args.json:
                print(_json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                counts = report["counts"]
                _console.print(f"[bold]Godkjenninger {args.season}[/bold]")
                _console.print(
                    f"  {counts['approved']} godkjent, {counts['stale']} utdatert, "
                    f"{counts['pending_review']} til gjennomgang, "
                    f"{counts['locked']} låst"
                )
                for entry in report["tournaments"]:
                    marker = {
                        "approved": "[green]✓[/green]",
                        "stale_approval": "[yellow]⚠[/yellow]",
                    }.get(entry["status"], "[dim]·[/dim]")
                    locks = ""
                    if entry["placement_locked"]:
                        locks += " placement"
                    if entry["participants_locked"]:
                        locks += " participants"
                    _console.print(
                        f"  {marker} {entry['tournament_id']}: {entry['status']}{locks or ''}"
                    )
            return 0

        if args.season_command == "approve":
            decisions = approve_tournament(
                season=args.season,
                tournament_id=args.tournament_id,
                root=args.root,
                actor=args.actor,
                note=args.note,
                placement_locked=args.placement_locked,
                participants_locked=args.participants_lock,
                problem=_canonical_verification_problem(args.work_dir, args.season, args.root),
            )
            if args.json:
                print(_json.dumps(decisions, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _console.print(f"[green]✓[/green] Approved {args.tournament_id} in {args.season}")
            return 0

        if args.season_command == "unapprove":
            decisions = unapprove_tournament(
                season=args.season,
                tournament_id=args.tournament_id,
                root=args.root,
                actor=args.actor,
                note=args.note,
            )
            if args.json:
                print(_json.dumps(decisions, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _console.print(
                    f"[green]✓[/green] Unapproved {args.tournament_id} in {args.season}; "
                    "placement is editable again"
                )
            return 0

        if args.season_command == "move":
            try:
                from ..pipeline.run_manifest import RunManifest

                move_run_id = RunManifest(args.work_dir).read().get("run_id")
            except Exception:
                move_run_id = None
            schedule = move_tournament(
                season=args.season,
                tournament_id=args.tournament_id,
                root=args.root,
                date=args.date,
                arena=args.arena,
                host_club=args.host_club,
                start_time=args.start_time,
                problem=_canonical_verification_problem(args.work_dir, args.season, args.root),
                actor=args.actor,
                note=args.note,
                dry_run=args.dry_run,
                allow_cross_half=args.allow_cross_half,
                run_id=move_run_id,
            )
            if args.json:
                print(_json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                action = "Validated move preview for" if schedule.get("dry_run") else "Updated"
                _console.print(
                    f"[green]✓[/green] {action} {args.tournament_id} in {args.season}; "
                    f"revision {schedule.get('revision')}"
                )
            return 0

        if args.season_command == "guest-report":
            report = guest_slot_report(args.season, root=args.root)
            if args.json:
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

        if args.season_command == "guest-candidates":
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

        if args.season_command == "guest-reserve":
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
                print(_json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                action = "Validated reservation preview for" if schedule.get("dry_run") else "Reserved"
                _console.print(
                    f"[green]✓[/green] {action} {args.tournament_id} in {args.season}; "
                    f"revision {schedule.get('revision')}"
                )
            return 0

        if args.season_command == "guest-fill":
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
                print(_json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _console.print(
                    f"[green]✓[/green] Filled guest place in {args.tournament_id} ({args.season}) "
                    f"with {args.external_label}; revision {schedule.get('revision')}"
                )
            return 0

        if args.season_command == "guest-release":
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
                print(_json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                action = "Validated release preview for" if schedule.get("dry_run") else "Released"
                _console.print(
                    f"[green]✓[/green] {action} guest place in {args.tournament_id} ({args.season}); "
                    f"revision {schedule.get('revision')}"
                )
            return 0

        if args.season_command == "findings":
            from ..season_maintenance import list_findings

            report = list_findings(args.season, root=args.root)
            if args.json:
                print(_json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _console.print(
                    f"[bold]Funn {args.season}[/bold] (revision {str(report['revision'])[:12]}, "
                    f"{report['finding_count']} funn)"
                )
                for finding in report["findings"]:
                    _console.print(
                        f"  [dim]{finding['category']}[/dim] {finding['finding_id']}: {finding['message']}"
                    )
            return 0

        if args.season_command in ("repair-options", "search"):
            from ..season_maintenance import repair_options, search

            if args.season_command == "search":
                dimensions = [
                    part.strip()
                    for part in str(args.dimensions or "").split(",")
                    if part.strip()
                ]
                report = search(args.season, args.finding, root=args.root, dimensions=dimensions)
            else:
                report = repair_options(
                    args.season, args.finding, root=args.root, allow_search=bool(args.allow_search)
                )
            if args.json:
                print(_json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _console.print(
                    f"[bold]{args.finding}[/bold]: {report['option_count']} alternativ "
                    f"(revision {str(report['revision'])[:12]})"
                )
                for option in report["options"]:
                    marker = "[magenta]P[/magenta] " if option.get("non_dominated") else "  "
                    _console.print(
                        f"  {marker}[green]•[/green] {option['option_id']} ({option.get('family')})"
                    )
                pareto = report.get("pareto") or {}
                if pareto.get("non_dominated_option_ids"):
                    _console.print(
                        f"  [magenta]P[/magenta] = Pareto-front: "
                        f"{pareto['front_size']} av {pareto.get('measured_option_count', 0)} målt"
                    )
                if not report["options"]:
                    _console.print(
                        f"  [yellow]⚠[/yellow] ingen lovlige alternativer: "
                        f"{report['escalation'].get('reason')}"
                    )
            return 0

        if args.season_command == "apply-repair":
            from ..season_maintenance import apply_repair

            result = apply_repair(
                args.season,
                args.option_id,
                args.expected_revision,
                root=args.root,
                actor=args.actor,
                dry_run=args.dry_run,
                finding_id=args.finding,
                dimensions=[
                    part.strip()
                    for part in str(getattr(args, "dimensions", "") or "").split(",")
                    if part.strip()
                ],
            )
            if args.json:
                print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                if result.get("ok"):
                    action = "Validated repair preview for" if result.get("dry_run") else "Applied repair to"
                    _console.print(
                        f"[green]✓[/green] {action} {args.season}; "
                        f"revision {str(result.get('revision_before'))[:12]} -> "
                        f"{str(result.get('revision_after'))[:12]}"
                    )
                    _console.print(f"  {_format_delta(result.get('delta'))}")
                else:
                    _console.print(
                        f"[red]✗[/red] Avvist ({result.get('reason')}); kanonisk revisjon uendret"
                    )
            return 0

        if args.season_command in ("accept-deviation", "revoke-acceptance"):
            from ..season_maintenance import accept_finding, revoke_acceptance

            if args.season_command == "accept-deviation":
                result = accept_finding(
                    args.season,
                    args.finding,
                    root=args.root,
                    actor=args.actor,
                    note=args.note,
                )
                record = result["acceptance"]
                verb = "Aksepterte"
            else:
                result = revoke_acceptance(
                    args.season,
                    args.finding,
                    root=args.root,
                    actor=args.actor,
                    note=args.note,
                )
                record = result["revoked"]
                verb = "Tilbakekalte aksept for"
            if args.json:
                print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                _console.print(
                    f"[green]✓[/green] {verb} {args.finding} "
                    f"(revisjon {str(result.get('revision'))[:12]})"
                )
                _console.print(
                    f"  avvik {record.get('accepted_deviation')} mot mål {record.get('target')} "
                    f"i {record.get('scope')}"
                )
            return 0

        if args.season_command == "replan":
            from datetime import date as _date

            from ..canonical_replan import replan_around_baseline
            from ..operator_waivers import load_active_waivers
            from ..pipeline.stage1_config import load_effective_config
            from ..pipeline.state import StageName, StageStatus
            from ..season_state import apply_candidate

            replan_state = PipelineState(args.work_dir)
            try:
                replan_config = load_effective_config(replan_state)
            except Exception as exc:
                _console.print(f"[red]✗[/red] Kunne ikke laste Stage 1-konfigurasjon: {exc}")
                return 1
            if not replan_config or not replan_config.get("start_date") or not replan_config.get("end_date"):
                _console.print(
                    "[red]✗[/red] Fant ingen brukbar Stage 1-konfigurasjon. Kjør Stage 1 på nytt før replan."
                )
                return 1

            result = replan_around_baseline(
                season=args.season,
                config=replan_config,
                scraping_result=replan_state.read_stage(StageName.SCRAPING),
                start_date=_date.fromisoformat(str(replan_config["start_date"])),
                end_date=_date.fromisoformat(str(replan_config["end_date"])),
                root=args.root,
                waivers=load_active_waivers(replan_state.work_dir),
                engine=str(args.engine).replace("-", "_"),
                request={
                    "iterations": args.iterations,
                    "seed": args.seed,
                    "move_dates": args.move_dates,
                    "move_hosts": args.move_hosts,
                    "move_slots": args.move_slots,
                },
            )
            if result["lock_violations"]:
                _console.print("[red]✗[/red] Replan-kandidaten bryter kanoniske l\u00e5ser:")
                for violation in result["lock_violations"]:
                    _console.print(f"  [red]•[/red] {violation['message']}")
                return 1
            if not result["verification"].get("ok", True):
                _console.print("[red]✗[/red] Replan-kandidaten feiler hard verifisering:")
                for violation in result["verification"].get("violations", []):
                    _console.print(f"  [red]•[/red] {violation.get('message')}")
                return 1

            planning_checkpoint = replan_state.read_stage(StageName.PLANNING) or {}
            if not planning_checkpoint:
                planning_checkpoint = {"source": "season_replan"}
            planning_checkpoint = dict(planning_checkpoint)
            planning_checkpoint["plan"] = result["candidate"]
            planning_checkpoint["source"] = f"season_replan:{args.engine}"
            replan_state.write_stage(StageName.PLANNING, planning_checkpoint, status=StageStatus.DONE)

            cost = result["change_cost"]
            applied_schedule = None
            if args.apply:
                applied_schedule, _, _ = apply_candidate(
                    season=args.season,
                    candidate=result["candidate"],
                    root=args.root,
                    problem=result["problem"],
                )

            if args.json:
                print(
                    _json.dumps(
                        {
                            "change_cost": cost,
                            "revision": (applied_schedule or {}).get("revision"),
                            "applied": bool(args.apply),
                        },
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                )
            else:
                _console.print(
                    f"[green]✓[/green] Replan av {args.season} skrevet til Stage 3"
                    f"{' og anvendt' if args.apply else ''}; endringskostnad {cost['total']:.1f}"
                )
                for category, count in cost["counts"].items():
                    _console.print(f"  {category}: {count}")
            return 0

        if args.season_command in ("diff", "apply"):
            from datetime import date as _date
            from pathlib import Path as _Path

            from ..canonical_baseline import build_canonical_baseline, change_cost
            from ..operator_waivers import load_active_waivers
            from ..pipeline.stage1_config import load_effective_config
            from ..pipeline.state import StageName
            from ..planning_contract import build_planning_problem, extract_candidate
            from ..season_state import apply_candidate

            candidate_path = _Path(args.candidate)
            if not candidate_path.exists():
                _console.print(f"[red]✗[/red] Kandidatfil ikke funnet: {candidate_path}")
                return 1
            try:
                raw_candidate = _json.loads(candidate_path.read_text(encoding="utf-8"))
                candidate = extract_candidate(raw_candidate)
            except (OSError, ValueError, _json.JSONDecodeError) as exc:
                _console.print(f"[red]✗[/red] Kunne ikke lese kandidat: {exc}")
                return 1

            if args.season_command == "diff":
                canonical_schedule = load_schedule(args.season, root=args.root)
                canonical_decisions = load_decisions(args.season, root=args.root)
                baseline = build_canonical_baseline(canonical_schedule, canonical_decisions)
                cost = change_cost(baseline, candidate)
                if args.json:
                    print(_json.dumps(cost, ensure_ascii=False, indent=2, sort_keys=True))
                else:
                    _console.print(f"[bold]Endringskostnad {args.season}[/bold]")
                    for category, count in cost["counts"].items():
                        _console.print(f"  {category}: {count}")
                    _console.print(f"  total: {cost['total']:.1f}")
                return 0

            verify_state = PipelineState(args.work_dir)
            try:
                verify_config = load_effective_config(verify_state)
            except Exception:
                verify_config = {}
            verify_problem = None
            if verify_config and verify_config.get("start_date") and verify_config.get("end_date"):
                verify_problem = build_planning_problem(
                    verify_config,
                    verify_state.read_stage(StageName.SCRAPING),
                    _date.fromisoformat(str(verify_config["start_date"])),
                    _date.fromisoformat(str(verify_config["end_date"])),
                    waivers=load_active_waivers(verify_state.work_dir),
                )
            canonical_schedule, canonical_decisions, cost = apply_candidate(
                season=args.season,
                candidate=candidate,
                root=args.root,
                problem=verify_problem,
                actor=args.actor,
            )
            if args.json:
                print(
                    _json.dumps(
                        {"schedule": canonical_schedule, "decisions": canonical_decisions, "change_cost": cost},
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                )
            else:
                _console.print(
                    f"[green]✓[/green] Applied candidate to {args.season}; "
                    f"revision {canonical_schedule.get('revision')} "
                    f"(change cost {cost['total']:.1f})"
                )
            return 0

        _console.print("[red]✗[/red] Missing season subcommand")
        return 1
    except (SeasonStateError, SeasonMaintenanceError) as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``rvv-miniputt`` console script."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "status":
        return _cmd_status(args)
    elif args.command == "calendars":
        return _cmd_calendars(args)
    elif args.command == "activities":
        return _cmd_activities(args)
    elif args.command == "registered-teams":
        return _cmd_registered_teams(args)
    elif args.command == "run":
        return _cmd_run(args)
    elif args.command == "operator":
        return _cmd_operator(args)
    elif args.command == "sources":
        return _cmd_sources(args)
    elif args.command == "registrations":
        return _cmd_registrations(args)
    elif args.command == "logs":
        return _cmd_logs(args)
    elif args.command == "cancel":
        return _cmd_cancel(args)
    elif args.command == "replan":
        return _cmd_replan(args)
    elif args.command == "adjust":
        return _cmd_adjust(args)
    elif args.command == "review":
        return _cmd_review(args)
    elif args.command == "tournament":
        return _cmd_tournament(args)
    elif args.command == "scrape":
        return _cmd_scrape(args)
    elif args.command == "scrape-llm":
        return _cmd_scrape_llm(args)
    elif args.command == "recovery-targets":
        return _cmd_recovery_targets(args)
    elif args.command == "recovery-inject":
        return _cmd_recovery_inject(args)
    elif args.command == "scrape-merge":
        return _cmd_scrape_merge(args)
    elif args.command == "critic":
        return _cmd_critic(args)
    elif args.command == "auto-adjust":
        return _cmd_auto_adjust(args)
    elif args.command == "verdict":
        return _cmd_verdict(args)
    elif args.command == "candidates":
        return _cmd_candidates(args)
    elif args.command == "plan":
        return _cmd_plan(args)
    elif args.command == "season":
        return _cmd_season(args)
    elif args.command == "stage3":
        from .pipeline_orchestrator.stage3_session_command import _cmd_stage3_session

        if getattr(args, "stage3_command", None) == "refine":
            from .pipeline_orchestrator.stage3_refine_command import _cmd_stage3_refine

            return _cmd_stage3_refine(args)
        if getattr(args, "stage3_command", None) == "converge":
            from .pipeline_orchestrator.stage3_converge_command import _cmd_stage3_converge

            return _cmd_stage3_converge(args)
        if getattr(args, "stage3_command", None) == "adopt":
            from .pipeline_orchestrator.stage3_converge_command import _cmd_stage3_adopt

            return _cmd_stage3_adopt(args)
        return _cmd_stage3_session(args)
    elif args.command == "waiver":
        return _cmd_waiver(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
