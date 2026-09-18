"""``rvv-miniputt stage3 refine`` — refine a finalized unpromoted candidate.

This is the normal refinement entry point for a hard-valid Stage 3/Stage 4
candidate that has *not* been promoted. It reopens the exact reviewed
candidate, enumerates finding-directed repair options through the same
repository-owned providers as promoted-season maintenance, applies one
verified option as a new candidate revision and re-runs Stage 4 with
provenance to the superseded export.

It deliberately is not promotion, not a Stage 3 reset and not a Stage 1/2
rerun: Stage 1/2 checkpoints/fingerprints are read-only inputs.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Any

from ._shared import _console


def _refinement_problem(state: Any, args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], Any, Any, dict[str, Any]]:
    """Rebuild the exact mid-planning problem from preserved Stage 1/2 facts."""
    from ...pipeline.stage1_config import load_effective_config
    from ...pipeline.state import StageName
    from .verification import _mid_planning_decision_problem

    cfg: dict[str, Any] = {}
    try:
        cfg = load_effective_config(state, input_path=getattr(args, "input", None)) or {}
    except Exception as exc:
        raise RuntimeError(f"kunne ikke laste effektiv konfigurasjon: {exc}") from exc
    if not cfg.get("start_date") or not cfg.get("end_date"):
        checkpoint_cfg = state.read_stage(StageName.CONFIG) or {}
        cfg = {**checkpoint_cfg, **cfg}
    if not cfg.get("start_date") or not cfg.get("end_date"):
        raise RuntimeError("Stage 1-konfigurasjonen mangler et gyldig sesongvindu")
    scraping = state.read_stage(StageName.SCRAPING) or {}
    start = datetime.strptime(str(cfg["start_date"]), "%Y-%m-%d")
    end = datetime.strptime(str(cfg["end_date"]), "%Y-%m-%d")
    problem = _mid_planning_decision_problem(cfg, scraping, start, end, state.work_dir) or {}
    return cfg, scraping, start, end, problem


def _cmd_stage3_refine(args: argparse.Namespace) -> int:
    from ...application.candidate_refinement import (
        RefinementError,
        load_finalized_candidate,
        refinement_findings,
        refinement_options,
        refine_finalized_candidate,
    )
    from ...pipeline.state import PipelineState

    work_dir = getattr(args, "work_dir", ".pipeline")
    state = PipelineState(work_dir)
    json_output = bool(getattr(args, "json", False))
    as_json = lambda payload: json.dumps(payload, indent=2, ensure_ascii=False)  # noqa: E731

    try:
        _session, _checkpoint, candidate = load_finalized_candidate(work_dir)
    except RefinementError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    try:
        _cfg, _scraping, _start, _end, problem = _refinement_problem(state, args)
    except Exception as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    finding_id = getattr(args, "finding", None)
    option_id = getattr(args, "option_id", None)
    allow_search = bool(getattr(args, "search", False))
    dimensions = ("participants", "host")

    if option_id is None:
        # Read-only inspection: list findings, or the options for one finding,
        # so a harness can choose a repository-declared action.
        if finding_id:
            try:
                view = refinement_options(
                    candidate, problem, finding_id, allow_search=allow_search, dimensions=dimensions
                )
            except Exception as exc:
                _console.print(f"[red]✗[/red] {exc}")
                return 1
            if json_output:
                print(as_json(view))
            else:
                _console.print(
                    f"[bold]{finding_id}[/bold]: {view['option_count']} alternativ(er) "
                    f"({len(view['pareto'])} på paretofronten)"
                )
                for option in view["options"]:
                    _console.print(
                        f"  - {option['option_id']} [{option.get('family')}]"
                    )
                if view.get("escalation", {}).get("needed"):
                    _console.print(f"  eskalering: {view['escalation'].get('reason')}")
            return 0

        findings = refinement_findings(candidate, problem)
        payload = {
            "candidate_fingerprint": _session.finalized_fingerprint or _session.candidate_fingerprint,
            "finding_count": len(findings),
            "findings": findings,
        }
        if json_output:
            print(as_json(payload))
        else:
            _console.print(f"[bold]Funn for valgt kandidat[/bold]: {len(findings)}")
            for finding in findings:
                _console.print(
                    f"  - {finding['finding_id']} [{finding['category']}] {finding.get('message', '')}"
                )
        return 0

    try:
        result = refine_finalized_candidate(
            work_dir,
            problem=problem,
            option_id=option_id,
            finding_id=finding_id,
            dimensions=dimensions,
            dry_run=bool(getattr(args, "dry_run", False)),
            export=not bool(getattr(args, "no_export", False)),
            export_dir=getattr(args, "export_dir", None),
            timestamped_export=not bool(getattr(args, "flat_export", False)),
            strict=not bool(getattr(args, "non_strict", False)),
            actor="operator",
            rationale=str(getattr(args, "rationale", "") or ""),
        )
    except RefinementError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    if result.get("ok") and not result.get("dry_run") and result.get("export"):
        # Stage 4 re-ran; refresh the sanitized provenance/evidence bundle so
        # it is bound to the new export fingerprint (best-effort).
        try:
            from ...pipeline.state import StageName
            from .verification import _write_run_evidence_bundle

            _write_run_evidence_bundle(
                args, state, _cfg, _scraping, _start, _end,
                state.read_stage(StageName.PLANNING),
                lambda message: None,
            )
        except Exception:
            pass
        result["audit_required"] = True

    if json_output:
        print(as_json(result))
    else:
        if not result.get("ok"):
            if result.get("candidate_committed"):
                _console.print(
                    f"[yellow]⚠[/yellow] Refinering fullført, men Stage 4 feilet: "
                    f"{result.get('export_error') or result.get('export_errors')}"
                )
                _console.print(
                    f"  kandidat: {(result.get('candidate_fingerprint_before') or '')[:12]} → "
                    f"{(result.get('candidate_fingerprint_after') or '')[:12]} (lagret; krever ny eksport)"
                )
            else:
                _console.print(
                    f"[red]✗[/red] Refinering avvist: {result.get('reason')} "
                    f"(kandidat uendret: {(result.get('candidate_fingerprint_before') or '')[:12]})"
                )
        else:
            revision = result.get("session_revision_after")
            _console.print(
                f"[green]✓[/green] Refinering {'(tørrkjøring) ' if result.get('dry_run') else ''}"
                f"revisjon {result.get('session_revision_before')} → {revision}"
            )
            _console.print(f"  kandidat: {(result.get('candidate_fingerprint_before') or '')[:12]} → "
                           f"{(result.get('candidate_fingerprint_after') or '')[:12]}")
            if result.get("export_dir"):
                _console.print(f"  ny eksport: {result['export_dir']}")
            if result.get("audit_required"):
                _console.print("  krever ny semantisk revisjon: ja")
    return 0 if result.get("ok") else 1
