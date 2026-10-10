"""
Activities command implementation for the RVV Miniputt CLI.

This module contains the implementation of the `rvv-miniputt activities` command
for refreshing and publishing the public activity calendar.
"""

from __future__ import annotations

import argparse

from rich.console import Console

from .pipeline_orchestrator.operator_publish import _execute_operator_publish, _print_pages_result

_console = Console()


def _cmd_activities(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt activities`` — refresh/publish the public activity calendar."""
    from ..pipeline.activity_publish import (
        ActivityPublishError,
        default_activity_run_id,
        fetch_pages_branch,
        prepare_activity_latest_export,
    )
    from ..pipeline.input_workbook import WorkbookInputError

    _console.print("[bold]📅 RVV aktivitetskalender[/bold]")

    try:
        if getattr(args, "publish", False) and getattr(args, "push", True):
            fetch_pages_branch(repo_dir=args.repo_dir, remote=args.remote, branch=args.branch)
        prepared = prepare_activity_latest_export(
            input_path=args.input,
            export_dir=args.export_dir,
            repo_dir=args.repo_dir,
            branch=args.branch,
            default_year=getattr(args, "year", None),
            include_latest_base=getattr(args, "base_latest", True),
            require_latest_base=getattr(args, "base_latest", True),
        )
    except (ActivityPublishError, WorkbookInputError) as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        _console.print(f"[red]✗[/red] Aktivitetskalender feilet: {exc}")
        return 1

    _console.print(
        f"  [green]✓[/green] Staget komplett Pages-snapshot i {prepared['export_dir']} "
        f"({prepared['base_file_count']} eksisterende /latest/-fil(er) kopiert)"
    )
    for label, path in prepared["activity_files"].items():
        _console.print(f"    → {path}")

    if not getattr(args, "publish", False):
        _console.print("  [dim]Ikke publisert. Legg til --publish --confirm-public for å oppdatere GitHub Pages.[/dim]")
        return 0

    # Reuse the canonical operator publish path so the staged snapshot still
    # gets sanitized, approval-gated, fingerprinted, committed/pushed and
    # verified exactly like a normal Pages publication.
    args.operator_command = "publish"
    args.export_dir = prepared["export_dir"]
    args.run_id = getattr(args, "run_id", None) or default_activity_run_id()
    args.extra_public_files = getattr(args, "extra_public_files", []) or []
    args.allow_findings = getattr(args, "allow_findings", []) or []
    args.routine_public_assets = True

    publish_result = _execute_operator_publish(args)
    if publish_result is None:
        return 1
    return _print_pages_result(publish_result, as_json=getattr(args, "json", False))
