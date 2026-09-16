"""Operator audit-context/audit-evidence/audit-submit/audit-run
subcommands (issue #325, bounded/queryable in issue #356).

Mirrors the read-context/submit-decision shape of SKILL.md's "Structured
decision protocol": an interactive harness reads ``audit-context`` (a bounded
overview), pulls exact supporting detail with ``audit-evidence`` when needed,
then submits its verdict via ``audit-submit``. ``audit-run`` is the
headless-only path (no interactive harness active) that does all of that —
context, bounded judge call with evidence expansion, submit — in one command,
for cron/CI.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from ._shared import _console


def _cmd_operator_audit_context(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator audit-context`` — print the assembled
    evidence inventory for an interactive harness to read and reason over."""
    from ...pipeline.operator_action import DEFAULT_REGISTRY, UnknownActionError

    action = DEFAULT_REGISTRY.build("get_audit_context", work_dir=args.work_dir)
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


def _cmd_operator_audit_submit(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator audit-submit`` — persist a structured
    audit verdict (submitted by an interactive harness after in-session
    review, or by ``audit-run`` for the headless path)."""
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

    context = build_audit_context(work_dir=args.work_dir)
    if not context.get("export_fingerprint"):
        _console.print("[red]✗[/red] Ingen Stage 4-eksport funnet — kjør eksport før revisjon.")
        return 1

    evidence_index = build_audit_evidence_index(work_dir=args.work_dir)
    result = run_headless_audit(context, args.backend, evidence_index=evidence_index)

    action = DEFAULT_REGISTRY.build("submit_audit_result", work_dir=args.work_dir, result=result)
    try:
        submit_result = DEFAULT_REGISTRY.execute(action, approved=True)
    except UnknownActionError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    if submit_result.status != "ok":
        _console.print(f"[red]✗[/red] {submit_result.summary}")
        return 1

    _console.print(f"[green]✓[/green] Revisjon fullført (status={result.get('status')}).")
    return 0 if result.get("status") == "PASS" else 1
