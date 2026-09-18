"""Operator audit-context/audit-evidence/audit-submit/audit-run
subcommands (issue #325, bounded/queryable in issue #356).

Mirrors the read-context/submit-decision shape of SKILL.md's "Structured
decision protocol": an interactive harness reads ``audit-context`` (a bounded
overview), pulls exact supporting detail with ``audit-evidence`` when needed,
then submits its verdict via ``audit-submit``. ``audit-run`` is the
headless-only path (no interactive harness active) that does all of that —
context, bounded judge call with evidence expansion, submit — in one command,
for cron/CI.

The audit verdict is part of a persisted workflow, not a terminal run result:
``REVIEW_REQUIRED`` enters bounded convergence automatically, and a committed
mutation re-enters ``audit_required`` for the new export. The workflow phase is
owned by :mod:`tournament_scheduler.application.audit_lifecycle`; this module
only transports it and renders the canonical next transition.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from ._shared import _console


def _workflow_snapshot(work_dir: str) -> dict[str, Any] | None:
    from ...application.audit_lifecycle import workflow_snapshot

    try:
        return workflow_snapshot(work_dir)
    except Exception:
        return None


def _print_next_transition(workflow: dict[str, Any] | None) -> None:
    if not workflow:
        return
    phase = workflow.get("phase") or "(ukjent)"
    next_command = workflow.get("next_command")
    _console.print(f"[dim]Arbeidsflyt: {phase}[/dim]")
    if next_command:
        _console.print(f"  neste: {next_command}")


def _cmd_operator_audit_context(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator audit-context`` — print the assembled
    evidence inventory for an interactive harness to read and reason over."""
    from ...pipeline.operator_action import DEFAULT_REGISTRY, UnknownActionError

    workflow = _workflow_snapshot(args.work_dir)
    action = DEFAULT_REGISTRY.build(
        "get_audit_context", work_dir=args.work_dir, workflow=workflow
    )
    try:
        result = DEFAULT_REGISTRY.execute(action, approved=True)
    except UnknownActionError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    if not result.is_terminal_success:
        _console.print(f"[red]✗[/red] {result.summary}")
        return 1

    context_json = result.evidence[0] if result.evidence else "{}"
    print(context_json)
    return 0


def _cmd_operator_audit_evidence(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator audit-evidence`` — return detailed audit
    evidence for one bounded selector, so a harness never has to parse the
    raw artifacts or ingest the whole evidence bundle."""
    from ...pipeline.operator_action import DEFAULT_REGISTRY, UnknownActionError

    action = DEFAULT_REGISTRY.build(
        "get_audit_evidence",
        work_dir=args.work_dir,
        item=getattr(args, "item", None),
        tournament=getattr(args, "tournament", None),
        club=getattr(args, "club", None),
        age_group=getattr(args, "age_group", None),
        category=getattr(args, "category", None),
        unresolved=bool(getattr(args, "unresolved", False)),
        limit=getattr(args, "limit", None),
    )
    try:
        result = DEFAULT_REGISTRY.execute(action, approved=True)
    except UnknownActionError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    if not result.is_terminal_success:
        _console.print(f"[red]✗[/red] {result.summary}")
        return 1

    print(result.evidence[0] if result.evidence else "{}")
    return 0


def _auto_refine_review_required(args: argparse.Namespace, *, max_epochs: int) -> dict[str, Any]:
    """Continue a REVIEW_REQUIRED run through bounded Pareto convergence.

    Returns the convergence report, or ``{"ok": False, "reason": ...}`` when
    the workspace has no finalized unpromoted candidate to refine (for example
    a promoted-season audit) or the planning problem cannot be rebuilt. Never
    raises: a transport must report the outcome, not crash the audit.
    """
    from ...application.audit_convergence import audit_payload_for_current_export
    from ...application.convergence_refinement import run_bounded_convergence
    from ...pipeline.state import PipelineState
    from .stage3_refine_command import _refinement_problem

    work_dir = args.work_dir
    state = PipelineState(work_dir)
    try:
        _cfg, _scraping, _start, _end, problem = _refinement_problem(state, args)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return {"ok": False, "reason": f"planning_problem_unavailable: {exc}"}
    audit_payload = audit_payload_for_current_export(work_dir)
    try:
        return run_bounded_convergence(
            work_dir,
            problem=problem,
            max_epochs=int(max_epochs),
            audit_payload=audit_payload,
        )
    except Exception as exc:  # noqa: BLE001 - includes "no finalized candidate"
        return {"ok": False, "reason": str(exc)}


def _render_convergence_summary(convergence: dict[str, Any]) -> None:
    if not convergence.get("ok"):
        _console.print(
            "[dim]Ingen automatisk raffinering: "
            f"{convergence.get('reason') or 'ikke tilgjengelig'}[/dim]"
        )
        return
    reason = str(convergence.get("terminal_reason") or "")
    pause = str(convergence.get("pause_reason") or "")
    if reason:
        style = "green" if reason == "pass" else "yellow"
        _console.print(
            f"[{style}]Konvergens: {reason}[/{style}] {convergence.get('terminal_detail') or ''}".rstrip()
        )
    elif pause:
        _console.print(
            f"[yellow]Konvergens satt på pause: {pause}[/yellow] "
            f"{convergence.get('pause_detail') or ''}".rstrip()
        )
    for question in (convergence.get("audit_decision") or {}).get("operator_questions") or []:
        _console.print(
            f"  [yellow]operatørspørsmål[/yellow] (item {question.get('item_id')}): "
            f"{question.get('finding') or question.get('question')}"
        )
    if convergence.get("export_required"):
        _console.print(
            "  [yellow]eksport kreves[/yellow]: kandidaten er verifisert og lagret, "
            "men ikke materialisert som revisjonshåndtrykk "
            f"({convergence.get('committed_epochs')} mutasjon(er))"
        )
    elif convergence.get("export_materialized"):
        _console.print(
            "  én revisjonseksport materialisert ved batchgrensen: "
            f"{convergence.get('export_fingerprint')} "
            f"({convergence.get('committed_epochs')} mutasjon(er))"
        )


def _cmd_operator_audit_submit(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator audit-submit`` — persist a structured
    audit verdict (submitted by an interactive harness after in-session
    review, or by ``audit-run`` for the headless path)."""
    from ...application.audit_lifecycle import record_audit_verdict
    from ...pipeline.operator_action import (
        DEFAULT_REGISTRY,
        ApprovalRequiredError,
        PersistenceUnavailableError,
        UnknownActionError,
    )

    try:
        payload: dict[str, Any] = json.loads(args.result_file.read_text(encoding="utf-8")) if getattr(
            args, "result_file", None
        ) else json.loads(args.result_json)
    except (OSError, json.JSONDecodeError) as exc:
        _console.print(f"[red]✗[/red] Kunne ikke lese revisjonsresultat: {exc}")
        return 1

    action = DEFAULT_REGISTRY.build("submit_audit_result", work_dir=args.work_dir, result=payload)
    try:
        result = DEFAULT_REGISTRY.execute(action, approved=True)
    except (UnknownActionError, ApprovalRequiredError, PersistenceUnavailableError) as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    if result.status == "ok":
        _console.print(f"[green]✓[/green] {result.summary}")
        record_audit_verdict(args.work_dir, payload)
        if str(payload.get("status")) == "REVIEW_REQUIRED" and not bool(
            getattr(args, "no_refine", False)
        ):
            _console.print(
                "[dim]REVIEW_REQUIRED → fortsetter med avgrenset Pareto-konvergens over "
                "denne kandidaten …[/dim]"
            )
            convergence = _auto_refine_review_required(
                args, max_epochs=int(getattr(args, "max_refine_epochs", 6))
            )
            _render_convergence_summary(convergence)
        _print_next_transition(_workflow_snapshot(args.work_dir))
        return 0
    _console.print(f"[red]✗[/red] {result.summary}")
    for problem in result.problems:
        _console.print(f"    [dim]{problem}[/dim]")
    return 1


def _cmd_operator_audit_run(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator audit-run`` — headless-only: build
    context, call the given judge backend, submit the result. Refuses to run
    while an interactive harness is active, since the harness is expected to
    perform the audit itself in-session instead (issue #325)."""
    from ...application.audit_lifecycle import record_audit_verdict
    from ...llm_judge.audit import run_headless_audit
    from ...llm_judge.harness import is_harness_active
    from ...pipeline.audit_context import build_audit_context, build_audit_evidence_index
    from ...pipeline.operator_action import DEFAULT_REGISTRY, UnknownActionError

    if is_harness_active() and not getattr(args, "force", False):
        _console.print(
            "[yellow]?[/yellow] En interaktiv harness ser ut til å kjøre denne økten — "
            "harnessen bør utføre revisjonen selv i økten (se 'operator audit-context' / "
            "'operator audit-submit') fremfor å kjøre den skjulte revisjonsveien. "
            "Bruk --force for å kjøre likevel."
        )
        return 1

    max_rounds = max(1, int(getattr(args, "max_refine_rounds", 2)))
    last_status = ""
    for _round in range(max_rounds):
        context = build_audit_context(
            work_dir=args.work_dir, workflow=_workflow_snapshot(args.work_dir)
        )
        if not context.get("export_fingerprint"):
            _console.print("[red]✗[/red] Ingen Stage 4-eksport funnet — kjør eksport før revisjon.")
            return 1

        evidence_index = build_audit_evidence_index(work_dir=args.work_dir)
        result = run_headless_audit(context, args.backend, evidence_index=evidence_index)
        last_status = str(result.get("status") or "")

        action = DEFAULT_REGISTRY.build("submit_audit_result", work_dir=args.work_dir, result=result)
        try:
            submit_result = DEFAULT_REGISTRY.execute(action, approved=True)
        except UnknownActionError as exc:
            _console.print(f"[red]✗[/red] {exc}")
            return 1

        if submit_result.status != "ok":
            _console.print(f"[red]✗[/red] {submit_result.summary}")
            return 1

        _console.print(f"[green]✓[/green] Revisjon fullført (status={last_status}).")
        record_audit_verdict(args.work_dir, result)
        if last_status == "PASS":
            _print_next_transition(_workflow_snapshot(args.work_dir))
            return 0
        if last_status != "REVIEW_REQUIRED" or bool(getattr(args, "no_refine", False)):
            _print_next_transition(_workflow_snapshot(args.work_dir))
            return 1

        _console.print(
            "[dim]REVIEW_REQUIRED → fortsetter med avgrenset Pareto-konvergens …[/dim]"
        )
        convergence = _auto_refine_review_required(
            args, max_epochs=int(getattr(args, "max_refine_epochs", 6))
        )
        _render_convergence_summary(convergence)
        if not convergence.get("committed_epochs"):
            _print_next_transition(_workflow_snapshot(args.work_dir))
            return 1

    _console.print(
        f"[yellow]⚠[/yellow] Avgrenset revisjonsrunder brukt opp (siste status={last_status})."
    )
    _print_next_transition(_workflow_snapshot(args.work_dir))
    return 1
