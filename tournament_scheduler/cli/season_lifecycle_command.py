"""Season lifecycle, inventory and publication-evidence command handlers.

Ported unchanged from the pre-refactor ``rvv_cli._cmd_season`` branches."""

from __future__ import annotations

import argparse

from rich.console import Console

_console = Console()


def _cmd_season_inventory(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season inventory``."""
    from ..season_state import history_inventory
    import json as _json

    report = history_inventory(
        season=args.season,
        root=args.root,
        export_root=getattr(args, "export_root", "export"),
        pipeline_root=getattr(args, "pipeline_root", ".pipeline"),
    )
    if args.json:
        print(_json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        season_inv = report["season"]
        export_inv = report["export"]
        pipeline_inv = report["pipeline"]
        _console.print(f"[bold]Inventory for {args.season}[/bold]")
        _console.print(
            f"  season: {season_inv['total_bytes']} bytes across "
            f"{len(season_inv['files'])} files"
        )
        _console.print(
            f"  decisions history: {season_inv['history']['events']} events "
            f"({', '.join(f'{k}={v}' for k, v in sorted(season_inv['history']['events_by_type'].items()))})"
        )
        for event in season_inv["history"]["largest_events"]:
            _console.print(
                f"    largest event: {event['event']} {event['tournament_id']} "
                f"({event['bytes']} bytes)"
            )
        _console.print(
            f"  export: {export_inv['directory_count']} directories, "
            f"{export_inv['total_bytes']} bytes; "
            f"cleanup-eligible superseded: {export_inv['cleanup_eligible_superseded']}"
        )
        _console.print(
            f"  pipeline: {pipeline_inv['total_bytes']} bytes"
        )
        backups = season_inv.get("compaction_backups") or []
        _console.print(f"  compaction backups: {len(backups)}")
        for backup in backups[:5]:
            _console.print(
                f"    {backup.get('backup_id', '')[:12]} "
                f"({backup.get('decisions_bytes')} bytes, "
                f"{backup.get('compacted_event_indices') and len(backup['compacted_event_indices'])} events)"
            )
    return 0


def _cmd_season_lifecycle(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season lifecycle``."""
    import json as _json

    from ..season_state import season_lifecycle_report

    report = season_lifecycle_report(args.season, root=args.root)
    if args.json:
        print(_json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[bold]Sesonglivssyklus {args.season}[/bold]: {report['state']}"
        )
        baseline = report.get("published_baseline")
        if baseline:
            _console.print(
                f"  publisert: {baseline.get('publication_id')} "
                f"({baseline.get('tournament_count')} turneringer, "
                f"{baseline.get('published_at')})"
            )
        reconciliation = report.get("reconciliation")
        if reconciliation:
            if reconciliation["ok"]:
                _console.print(
                    "  [green]✓[/green] avstemt mot publisert basislinje "
                    f"({reconciliation['applied_mutation_count']} kanoniske endringer)"
                )
            else:
                _console.print(
                    "  [red]✗[/red] uforklart avvik mot publisert basislinje: "
                    + _json.dumps(reconciliation["unexplained_delta"], ensure_ascii=False)
                )
    return 0


def _cmd_season_normalize_arenas(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season normalize-arenas``."""
    from ..season_state import normalize_arena_identities
    import json as _json

    schedule, _decisions = normalize_arena_identities(
        season=args.season,
        root=args.root,
        actor=args.actor,
        note=args.note,
        dry_run=args.dry_run,
    )
    report = schedule.get("arena_normalization") or {}
    summary = {
        "season": args.season,
        "dry_run": bool(args.dry_run),
        "changed": bool(report.get("changed")),
        "changed_count": int(report.get("changed_count") or 0),
        "changes": report.get("changes") or [],
        "revision": schedule.get("revision"),
    }
    if args.json:
        print(_json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        action = "would rewrite" if args.dry_run else "rewrote"
        _console.print(
            f"[green]✓[/green] Arena normalization for {args.season}: "
            f"{action} {summary['changed_count']} tournament(s)"
        )
        for change in summary["changes"]:
            _console.print(
                f"  {change['tournament_id']}: {change['from']} -> {change['to']}"
            )
    return 0


def _cmd_season_publication_evidence(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season publication-evidence``."""
    import json as _json

    from ..season_state import publication_evidence_report

    report = publication_evidence_report(args.season, root=args.root)
    if args.json:
        print(_json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        active = report.get("active_publication")
        _console.print(
            f"[bold]Publiseringsevidence {args.season}[/bold]: {report['state']}"
        )
        if active is None:
            _console.print("  Ingen aktiv publisert basislinje.")
        else:
            evidence = active.get("publication_evidence") or {}
            _console.print(
                f"  aktiv publisering: {active.get('publication_id')} "
                f"(revisjon {active.get('canonical_revision')}, "
                f"kjøring {evidence.get('run_id') or 'ukjent'})"
            )
            previous = active.get("previous_publication")
            if previous:
                _console.print(
                    f"  erstattet: {previous.get('publication_id')} "
                    f"(kjøring {(previous.get('publication_evidence') or {}).get('run_id') or 'ukjent'})"
                )
            summary = (report.get("published_to_canonical_delta") or {}).get("summary") or {}
            if report.get("blocked"):
                _console.print(
                    "  [red]✗[/red] avvik kan ikke beregnes: "
                    f"{report.get('delta_error')}"
                )
            else:
                _console.print(
                    "  avvik publisert -> kanonisk: "
                    f"+{summary.get('added', 0)} -{summary.get('removed', 0)} "
                    f"~{summary.get('changed', 0)} "
                    f"({summary.get('unchanged', 0)} uendret)"
                )
            decision_changes = report.get("decision_changes") or {}
            if decision_changes and not decision_changes.get("available", True):
                _console.print(
                    "  beslutningsendringer: ikke tilgjengelig "
                    f"({decision_changes.get('reason')})"
                )
            elif decision_changes:
                protections = decision_changes.get("change_protections") or {}
                constraints = decision_changes.get("request_constraints") or {}
                _console.print(
                    "  beslutningsendringer: "
                    f"{len(decision_changes.get('approval_changes') or [])} godkjenning(er), "
                    f"{len(decision_changes.get('booking_changes') or [])} booking, "
                    f"{len(protections.get('added') or [])} nye / "
                    f"{len(protections.get('removed') or [])} fjernede beskyttelser, "
                    f"{len(constraints.get('added') or [])} nye / "
                    f"{len(constraints.get('removed') or [])} fjernede krav"
                )
        retained = report.get("retained_evidence") or []
        _console.print(f"  lagret evidence: {len(retained)} fil(er)")
    return 0


def _cmd_season_reopen_planning(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season reopen-planning``."""
    import json as _json

    from ..season_state import reopen_planning

    report = reopen_planning(
        season=args.season,
        reason=args.reason,
        confirm_break_published_baseline=args.confirm_break_published_baseline,
        actor=args.actor,
        root=args.root,
    )
    if args.json:
        print(_json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[yellow]⚠[/yellow] {args.season} er gjenåpnet for planlegging "
            f"(tidligere publisert basislinje er ikke endret)."
        )
        _console.print(f"  årsak: {report['reason']}")
    return 0


def _cmd_season_seal_published(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season seal-published``."""
    import json as _json

    from ..infrastructure.canonical_revision_history import load_canonical_schedule_at_revision
    from ..pipeline.export_lifecycle import find_published_exports_for_season
    from ..pipeline.export_projection_guard import (
        projection_from_export_artifacts,
        tournament_projection,
    )
    from ..season_state import seal_published_season

    published_exports = find_published_exports_for_season(args.season, season_root=args.root)
    if not published_exports:
        _console.print(
            f"[red]✗[/red] Ingen autoritativ publisert eksport funnet for {args.season}; "
            "kan ikke seile en upublisert sesong."
        )
        return 1
    latest = published_exports[0]
    publication_canonical_schedule = load_canonical_schedule_at_revision(
        str(latest.get("canonical_season") or args.season),
        str(latest.get("canonical_revision") or ""),
        season_root=args.root,
    )
    publication_canonical_plan = (
        publication_canonical_schedule.get("plan")
        if isinstance(publication_canonical_schedule, dict)
        else None
    )
    from ..published_baseline import projection_problem_from_schedule

    publication_canonical_problem = (
        projection_problem_from_schedule(publication_canonical_schedule)
        if isinstance(publication_canonical_schedule, dict)
        else None
    )
    published_projection = latest.get("schedule_projection")
    if not isinstance(published_projection, dict):
        if publication_canonical_plan is None:
            _console.print(
                "[red]✗[/red] Kan ikke rekonstruere den publiserte sesongplanen: "
                "eksporten mangler schedule_projection og kanonisk revisjon kunne "
                "ikke gjenopprettes. Nekter å seile."
            )
            return 1
        from ..pipeline.export_projection_guard import ExportProjectionError

        try:
            published_projection = projection_from_export_artifacts(
                str(latest.get("export_dir") or ""),
                published_canonical_plan=publication_canonical_plan,
                published_canonical_problem=publication_canonical_problem,
            )
        except ExportProjectionError as exc:
            _console.print(f"[red]✗[/red] {exc}")
            return 1
    if publication_canonical_plan is not None:
        from ..pipeline.export_projection_guard import ExportProjectionError

        try:
            publication_canonical_projection = tournament_projection(
                publication_canonical_plan, publication_canonical_problem
            )
        except ExportProjectionError as exc:
            _console.print(
                "[red]✗[/red] Kan ikke gjenoppbygge kanonisk operativ projeksjon "
                f"på publiseringstidspunktet: {exc}"
            )
            return 1
    else:
        publication_canonical_projection = None
    materializations = []
    for raw in getattr(args, "attest_materializations", []) or []:
        tournament_id, _, provenance = str(raw).partition("=")
        materializations.append(
            {"tournament_id": tournament_id.strip(), "provenance": provenance.strip()}
        )
    report = seal_published_season(
        season=args.season,
        publication_id=str(latest.get("export_id") or ""),
        canonical_revision=str(latest.get("canonical_revision") or ""),
        published_at=str(latest.get("published_at") or ""),
        published_projection=published_projection,
        publication_canonical_projection=publication_canonical_projection,
        materializations=materializations,
        actor=args.actor,
        note=args.note,
        root=args.root,
    )
    if args.json:
        print(_json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[green]✓[/green] {args.season} er nå published_sealed "
            f"(publisering {report['publication_id']}, "
            f"{report['reconciliation']['applied_mutation_count']} kanoniske endringer)"
        )
        if report["reconciliation"]["publication_omissions"]:
            _console.print(
                "  dokumenterte publiseringsutelatelser: "
                + ", ".join(report["reconciliation"]["publication_omissions"])
            )
        if report["reconciliation"]["materializations"]:
            _console.print(
                "  attesterte etterpubliseringsmaterialiseringer: "
                + ", ".join(report["reconciliation"]["materializations"])
            )
    return 0


