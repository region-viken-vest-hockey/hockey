"""``rvv-miniputt sources status`` — per-source calendar health report."""

from __future__ import annotations

import argparse
import json

from rich.console import Console

_console = Console()


_SOURCE_STATUS_ICON = {"ok": "✓", "warning": "⚠", "blocked": "⛔", "failed": "✗"}
_SOURCE_STATUS_STYLE = {"ok": "green", "warning": "yellow", "blocked": "red", "failed": "red"}


def _cmd_sources_status(args: argparse.Namespace) -> int:
    from ..pipeline.source_health import compute_source_health, snapshot_scope

    results = compute_source_health(args.work_dir)

    if getattr(args, "json", False):
        print(json.dumps([r.to_dict() for r in results], indent=2, ensure_ascii=False))
        return 0

    if not results:
        _console.print(
            f"Ingen Stage 2-sjekkpunkt funnet i {args.work_dir}/. "
            "Kjør 'rvv-miniputt run' eller 'rvv-miniputt operator run' først."
        )
        return 0

    _console.print(f"[bold]Kildehelse[/bold] ({len(results)} kilde(r))\n")
    scope = snapshot_scope(args.work_dir)
    if scope and not scope.get("complete"):
        refreshed = len(scope.get("refreshed_sources") or [])
        total = refreshed + len(scope.get("missing_sources") or [])
        missing = ", ".join(scope.get("missing_sources") or [])
        _console.print(
            f"[yellow]⚠ Øyeblikksbildet er delvis — {refreshed} av {total} kilder er oppdatert.[/yellow]\n"
            f"    [dim]Ikke oppdatert i denne kjøringen: {missing}. "
            "Disse er ikke vurdert her, og det betyr ikke at de mangler.[/dim]\n"
        )
    for result in results:
        icon = _SOURCE_STATUS_ICON.get(result.status, "?")
        style = _SOURCE_STATUS_STYLE.get(result.status, "white")
        name = result.capability.split(":", 1)[-1] if ":" in result.capability else result.capability
        _console.print(f"[{style}]{icon}[/{style}] [bold]{name}[/bold] — {result.summary}")
        for item in result.evidence:
            _console.print(f"    [dim]{item}[/dim]")
        for problem in result.problems:
            _console.print(f"    [yellow]· {problem}[/yellow]")
        for action in result.suggested_actions:
            _console.print(f"    [cyan]→ {action}[/cyan]")
        _console.print()

    blocked_or_warning = [r for r in results if r.status != "ok"]
    return 1 if any(r.status == "blocked" for r in blocked_or_warning) else 0
