"""
Operator escalation/health subcommands for the RVV Miniputt CLI.

Transport only: argument parsing and rendering. Question, answer, promotion and
health policy lives in ``application.operator_state``.
"""

from __future__ import annotations

import argparse
import json as _json

from rich.console import Console

_console = Console()


def _cmd_operator_questions(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator questions`` — list operator questions."""
    from ..application.operator_state import list_operator_questions

    include_all = bool(getattr(args, "all", False))
    questions = [
        question.to_dict()
        for question in list_operator_questions(args.work_dir, include_all=include_all)
    ]
    if getattr(args, "json", False):
        print(_json.dumps(questions, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if not questions:
        _console.print("[dim]Ingen ubesvarte operatørspørsmål.[/dim]")
        return 0

    _console.print("[bold]Operatørspørsmål[/bold]")
    for question in questions:
        status = "besvart" if question.get("answered") else ("utdatert" if question.get("stale") else "åpen")
        _console.print(
            f"  [cyan]{question['id']}[/cyan] ({question['type']}) [{status}] {question.get('summary', '')}"
        )
        if question.get("context"):
            _console.print(f"    [dim]Kontekst: {question['context']}[/dim]")
        if question.get("recommendation"):
            _console.print(f"    Anbefaling: {question['recommendation']}")
        if question.get("answer"):
            _console.print(f"    Svar: {question['answer']}")
    return 0


def _cmd_operator_answer(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator answer`` — record an answer to an operator question."""
    from ..application.operator_state import record_operator_answer

    try:
        entry = record_operator_answer(
            args.work_dir,
            args.question_id,
            args.answer,
            decided_by=getattr(args, "decided_by", None),
        )
    except ValueError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    if getattr(args, "json", False):
        print(_json.dumps(entry.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(f"[green]✓[/green] Registrert svar på spørsmål {args.question_id}")
    return 0


def _cmd_operator_promote(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator promote`` — promote an answered question to a broader scope."""
    from ..application.operator_state import promote_operator_question

    scope_key = getattr(args, "scope_key", None) or ""
    try:
        entry = promote_operator_question(
            args.work_dir,
            args.question_id,
            args.scope,
            scope_key=scope_key,
            decided_by=getattr(args, "decided_by", None),
        )
    except ValueError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    if getattr(args, "json", False):
        print(_json.dumps(entry.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        target =f"{args.scope}/{scope_key}" if scope_key else args.scope
        _console.print(f"[green]✓[/green] Forfremmet spørsmål {args.question_id} til {target}")
    return 0


def _cmd_operator_health(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator health`` — check operator manifest health."""
    from ..application.operator_state import check_operator_health

    health = check_operator_health(args.work_dir).to_dict()
    if getattr(args, "json", False):
        print(_json.dumps(health, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if health.get("healthy") else 1

    if health.get("healthy"):
        _console.print("[green]✓[/green] Operatørtilstand er sunn")
        return 0

    recovery = health.get("manifest_recovery") or {}
    if recovery:
        _console.print(
            f"[yellow]⚠[/yellow] Manifestet ble gjenopprettet: {recovery.get('reason', 'ukjent årsak')}"
        )
        if recovery.get("backup_path"):
            _console.print(f"  Sikkerhetskopi av ødelagt manifest: {recovery['backup_path']}")
    elif not health.get("writable"):
        _console.print(f"[red]✗[/red] Manifestet kan ikke skrives: {health.get('detail', '')}")
    else:
        _console.print(f"[red]✗[/red] Operatørtilstand er ikke sunn: {health.get('detail', '')}")
    return 1
