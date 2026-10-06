"""
Operator subcommand implementations for the RVV Miniputt CLI.

This module contains the implementations of the various subcommands
for the `rvv-miniputt operator` command.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from ..pipeline.state import PipelineState

from rich.console import Console

_console = Console()


def _cmd_operator_questions(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator questions`` — list pending operator questions."""
    from ..application.operator_state import list_operator_questions
    from ..pipeline.state import PipelineState

    state = PipelineState(args.work_dir)
    questions = list_operator_questions(state)
    if args.json:
        import json as _json

        print(_json.dumps(questions, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print("[bold]Pending operator questions[/bold]")
        for q in questions:
            _console.print(f"  [cyan]{q['id']}[/cyan] {q['question']}")
            if q.get("context"):
                _console.print(f"    [dim]Context: {q['context']}[/dim]")
        if not questions:
            _console.print("  [dim]No pending questions[/dim]")
    return 0


def _cmd_operator_answer(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator answer`` — record an answer to an operator question."""
    from ..application.operator_state import record_operator_answer
    from ..pipeline.state import PipelineState

    state = PipelineState(args.work_dir)
    result = record_operator_answer(
        question_id=args.question_id,
        answer=args.answer,
        root=args.root,
        actor=args.actor,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if result.get("recorded"):
            _console.print(
                f"[green]✓[/green] Recorded answer to question {args.question_id}"
            )
        else:
            _console.print(f"[yellow]⚠[/yellow] {result.get('reason')}")
    return 0


def _cmd_operator_promote(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator promote`` — promote an operator question."""
    from ..application.operator_state import promote_operator_question
    from ..pipeline.state import PipelineState

    state = PipelineState(args.work_dir)
    result = promote_operator_question(
        question_id=args.question_id,
        scope=args.scope,
        scope_key=getattr(args, "scope_key", None),
        root=args.root,
        actor=args.actor,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if result.get("promoted"):
            _console.print(
                f"[green]✓[/green] Promoted question {args.question_id} to {args.scope}"
                f"{f'/{args.scope_key}' if args.scope_key else ''}"
            )
        else:
            _console.print(f"[yellow]⚠[/yellow] {result.get('reason')}")
    return 0


def _cmd_operator_health(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator health`` — check operator health."""
    from ..application.operator_state import check_operator_health
    from ..pipeline.state import PipelineState

    state = PipelineState(args.work_dir)
    health = check_operator_health(state)
    if args.json:
        import json as _json

        print(_json.dumps(health, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        status = "pass" if health.get("ok") else "fail"
        _console.print(
            f"[bold]Operator health check[/bold] "
            f"[{status}]{health.get('ok', False)}[/{status}]"
        )
        if health.get("checks"):
            for check, ok in health["checks"].items():
                check_status = "pass" if ok else "fail"
                _console.print(
                    f"  [{check_status}]{ok}[/{check_status}] {check}"
                )
        if health.get("issues"):
            _console.print("  [yellow]Issues:[/yellow]")
            for issue in health["issues"]:
                _console.print(f"    • {issue}")
    return 0