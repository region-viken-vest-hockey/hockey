"""
Operator command implementation for the RVV Miniputt CLI.

This module contains the implementation of the `rvv-miniputt operator` command
for managing operator questions and health checks.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from ..pipeline.state import PipelineState

from rich.console import Console

_console = Console()


def _cmd_operator(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator`` — operator manifest/escalation control-path."""
    from ..application.operator_state import (
        check_operator_health,
        list_operator_questions,
        promote_operator_question,
        record_operator_answer,
    )
    from ..pipeline.state import PipelineState

    state = PipelineState(args.work_dir)

    if args.health:
        return _cmd_operator_health(args)
    if args.questions:
        return _cmd_operator_questions(args)
    if args.answer:
        return _cmd_operator_answer(args)
    if args.promote:
        return _cmd_operator_promote(args)
    # Default: show operator manifest
    _console.print("[bold]Operator manifest[/bold]")
    _console.print(f"  work_dir: {args.work_dir}")
    _console.print(f"  season: {args.season}")
    _console.print(f"  root: {args.root}")
    health = check_operator_health(state)
    status = "pass" if health.get("ok") else "fail"
    _console.print(f"  health: [{status}]{health.get('ok', False)}[/{status}]")
    questions = list_operator_questions(state)
    _console.print(f"  questions: {len(questions)} pending")
    return 0