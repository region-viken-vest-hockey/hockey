"""Canonical Stage 4 rendering and ordinary ``season export`` finalization.

Printing and exit codes stay in the CLI command modules. Presentation-only
regeneration uses the read-only rendering boundary; ordinary canonical export
additionally records freshness, evidence and audit-required marks.
"""

from __future__ import annotations

import argparse
from typing import Any

from rich.console import Console

_console = Console()


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


def _render_canonical_season_export(
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Render Stage 4 artifacts and retain the exact source schedule."""
    from ..pipeline.stage4_export import run as run_export
    from ..pipeline.state import PipelineState
    from ..application.canonical_season.store_facade import (
        SeasonStateError,
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
    from ..season.maintenance import project_canonical_overlays

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

    from tournament_scheduler.publication.export_lifecycle import find_published_exports_for_season

    published_exports = find_published_exports_for_season(
        args.season,
        season_root=args.root,
    )
    latest_published_export = published_exports[0] if published_exports else None
    from tournament_scheduler.publication.projection_guard import ExportProjectionError

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
        from tournament_scheduler.publication.baseline import projection_problem_from_schedule

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
            refuse_placement_demotion=True,
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
    return result, schedule


def render_canonical_season_export(args: argparse.Namespace) -> dict[str, Any]:
    """Render Stage 4 artifacts without mutating canonical season state.

    This boundary deliberately does not clear export freshness, write the
    canonical change-log projection, or mark publication readiness. Callers
    requiring those ordinary ``season export`` effects must use
    :func:`run_canonical_season_export`.
    """
    result, schedule = _render_canonical_season_export(args)
    _write_canonical_export_evidence(schedule, result)
    return result


def run_canonical_season_export(args: argparse.Namespace) -> dict[str, Any]:
    """Render and finalize an ordinary revision-bound canonical export.

    Unlike presentation-only rendering, this operation owns the stale-to-fresh
    publication-readiness transition and its canonical bookkeeping.
    """
    from ..application.canonical_season.store_facade import (
        change_request_ledger,
        mark_export_fresh,
    )
    from ..pipeline.state import PipelineState, StageName, StageStatus

    result, schedule = _render_canonical_season_export(args)
    state = PipelineState(args.work_dir)

    # A successful export covers the revision it was generated from, so
    # clear the one-way "fresh export required" latch that a calendar
    # refresh or config reconciliation set. The clearing is revision-
    # bound: if canonical state advanced while this export ran, the
    # newer state's freshness requirement survives and publication
    # keeps refusing the now-stale artifact.
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

    return result


