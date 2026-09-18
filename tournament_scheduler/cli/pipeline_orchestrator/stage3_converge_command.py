"""``rvv-miniputt stage3 converge`` — autonomous bounded Pareto convergence.

A hard-valid Stage 4 export plus ``REVIEW_REQUIRED`` is feedback, not
automatically a human escalation: while repository-owned findings still map to
a supported repair/search direction, this command keeps refining the exact
reviewed unpromoted candidate, retaining a bounded non-dominated frontier and
re-running Stage 4 after every accepted mutation, until the controller reports
PASS, operator-required, bounded-search-exhausted or Pareto-stable.

It is a thin transport over
:func:`tournament_scheduler.application.convergence_refinement.run_bounded_convergence`:
it rebuilds the mid-planning problem from the preserved Stage 1/2 facts and
renders the returned truthful terminal report. No scheduling rule, repair or
verifier is implemented here.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from ._shared import _console


def _cmd_stage3_converge(args: argparse.Namespace) -> int:
    from ...application.convergence_refinement import run_bounded_convergence
    from ...pipeline.state import PipelineState
    from .stage3_refine_command import _refinement_problem

    work_dir = getattr(args, "work_dir", ".pipeline")
    state = PipelineState(work_dir)
    json_output = bool(getattr(args, "json", False))
    as_json = lambda payload: json.dumps(payload, indent=2, ensure_ascii=False)  # noqa: E731

    try:
        _cfg, _scraping, _start, _end, problem = _refinement_problem(state, args)
    except Exception as exc:
        if json_output:
            print(as_json({"ok": False, "reason": str(exc)}))
        else:
            _console.print(f"[red]✗[/red] {exc}")
        return 1

    try:
        audit_payload = None
        if not bool(getattr(args, "ignore_audit", False)):
            from ...application.audit_convergence import audit_payload_for_current_export

            audit_payload = audit_payload_for_current_export(work_dir)
        result = run_bounded_convergence(
            work_dir,
            problem=problem,
            max_epochs=int(getattr(args, "max_epochs", 6)),
            max_no_improvement_epochs=int(getattr(args, "max_no_improvement", 2)),
            frontier_limit=int(getattr(args, "frontier_limit", 6)),
            export=not bool(getattr(args, "no_export", False)),
            export_dir=getattr(args, "export_dir", None),
            timestamped_export=not bool(getattr(args, "flat_export", False)),
            strict=not bool(getattr(args, "non_strict", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
            allow_search=not bool(getattr(args, "no_search", False)),
            force_finding_id=getattr(args, "finding", None),
            preferred_option_id=getattr(args, "option_id", None),
            audit_payload=audit_payload,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as a transport failure
        if json_output:
            print(as_json({"ok": False, "reason": str(exc)}))
        else:
            _console.print(f"[red]✗[/red] Konvergens feilet: {exc}")
        return 1

    if result.get("committed_epochs") or result.get("export_materialized"):
        # The batch boundary materialized (or refreshed) the review export;
        # refresh the sanitized evidence bundle so it is bound to the newest
        # export fingerprint (best-effort).
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

    if json_output:
        print(as_json(result))
    else:
        _render(result)
    return 0 if result.get("ok") else 1


def _cmd_stage3_adopt(args: argparse.Namespace) -> int:
    """``rvv-miniputt stage3 adopt`` — re-validate and adopt a retained candidate.

    Thin transport over
    :func:`tournament_scheduler.application.convergence_refinement.select_frontier_candidate`.
    """
    from ...application.convergence_refinement import select_frontier_candidate
    from ...pipeline.state import PipelineState
    from .stage3_refine_command import _refinement_problem

    work_dir = getattr(args, "work_dir", ".pipeline")
    state = PipelineState(work_dir)
    json_output = bool(getattr(args, "json", False))
    as_json = lambda payload: json.dumps(payload, indent=2, ensure_ascii=False)  # noqa: E731

    try:
        _cfg, _scraping, _start, _end, problem = _refinement_problem(state, args)
    except Exception as exc:
        if json_output:
            print(as_json({"ok": False, "reason": str(exc)}))
        else:
            _console.print(f"[red]✗[/red] {exc}")
        return 1

    try:
        result = select_frontier_candidate(
            work_dir,
            candidate_ref=str(getattr(args, "candidate_ref", "")),
            problem=problem,
            export=not bool(getattr(args, "no_export", False)),
            export_dir=getattr(args, "export_dir", None),
            timestamped_export=not bool(getattr(args, "flat_export", False)),
            strict=not bool(getattr(args, "non_strict", False)),
            actor="operator",
            rationale=str(getattr(args, "rationale", "") or ""),
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as a transport failure
        if json_output:
            print(as_json({"ok": False, "reason": str(exc)}))
        else:
            _console.print(f"[red]✗[/red] Adopsjon feilet: {exc}")
        return 1

    if json_output:
        print(as_json(result))
    elif result.get("ok"):
        if result.get("already_current"):
            _console.print("[green]✓[/green] Kandidaten er allerede gjeldende revisjon.")
        else:
            _console.print(
                f"[green]✓[/green] Adoptert {result.get('candidate_ref')}: "
                f"revisjon {result.get('session_revision_before')} → {result.get('session_revision_after')}"
            )
            if result.get("audit_required"):
                _console.print("  krever ny semantisk revisjon over nyeste eksport: ja")
    else:
        _console.print(f"[red]✗[/red] Avvist: {result.get('reason')}")
    return 0 if result.get("ok") else 1


def _render(result: dict[str, Any]) -> None:
    reason = str(result.get("terminal_reason") or "")
    pause = str(result.get("pause_reason") or "")
    detail = str(result.get("terminal_detail") or "")
    if reason:
        style = "green" if reason == "pass" else "yellow"
        _console.print(f"[{style}]Konvergens: {reason}[/{style}]")
    elif pause:
        _console.print(f"[yellow]Konvergens satt på pause: {pause}[/yellow]")
        detail = str(result.get("pause_detail") or detail)
    else:
        _console.print("[yellow]Konvergens: (ukjent)[/yellow]")
    if detail:
        _console.print(f"  {detail}")
    if result.get("resumable"):
        _console.print("  kan gjenopptas med større --max-epochs (ingen manuell nullstilling)")
    _console.print(
        f"  epoker: {len(result.get('epochs') or [])} "
        f"(fullførte mutasjoner: {result.get('committed_epochs', 0)})"
    )
    frontier = result.get("frontier") or []
    _console.print(f"  paretofront: {len(frontier)} kandidat(er)")
    for entry in frontier:
        _console.print(
            f"    - {entry.get('candidate_ref')} [{entry.get('direction')}] "
            f"{(entry.get('candidate_fingerprint') or '')[:12]}"
        )
    audit_decision = result.get("audit_decision") or {}
    for question in audit_decision.get("operator_questions") or []:
        _console.print(
            f"  [yellow]operatørspørsmål[/yellow] (item {question.get('item_id')}): "
            f"{question.get('finding') or question.get('question')}"
        )
    if result.get("export_required"):
        _console.print(
            "  [yellow]eksport kreves[/yellow]: kandidaten er verifisert og lagret, "
            "men ikke materialisert som revisjonshåndtrykk"
        )
    elif result.get("export_materialized"):
        _console.print(
            f"  ny revisjonseksport: {result.get('export_dir')} "
            f"({result.get('export_fingerprint')})"
        )
    if result.get("audit_required"):
        _console.print("  krever ny semantisk revisjon over nyeste eksport: ja")
