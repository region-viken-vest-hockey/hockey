"""Season seal-published command handler (the published-season baseline seal)."""

from __future__ import annotations

import argparse

from rich.console import Console

_console = Console()


def _cmd_season_seal_published(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season seal-published``."""
    import json as _json

    from ..infrastructure.canonical_revision_history import load_canonical_schedule_at_revision
    from tournament_scheduler.publication.export_lifecycle import find_published_exports_for_season
    from tournament_scheduler.publication.projection_guard import (
        projection_from_export_artifacts,
        tournament_projection,
    )
    from tournament_scheduler.publication.baseline import PublishedBaselineError
    from ..application.canonical_season.store_facade import seal_published_season

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
    from tournament_scheduler.publication.baseline import projection_problem_from_schedule

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
        from tournament_scheduler.publication.projection_guard import ExportProjectionError

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
        from tournament_scheduler.publication.projection_guard import ExportProjectionError

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
    try:
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
    except PublishedBaselineError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1
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
