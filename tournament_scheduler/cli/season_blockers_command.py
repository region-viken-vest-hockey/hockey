"""CLI transport for the read-only ``season blockers`` application query."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Mapping

from tournament_scheduler.application.season_blockers import season_blockers


def _render_section(console: Any, title: str, entries: list[Mapping[str, Any]]) -> None:
    console.print(f"\n[bold]{title}[/bold]")
    if not entries:
        console.print("  none")
        return
    counts = Counter(str(entry.get("code") or "unknown") for entry in entries)
    for code, count in sorted(counts.items()):
        console.print(f"  {count} {code}")
        examples = [entry for entry in entries if str(entry.get("code") or "unknown") == code]
        for entry in examples[:3]:
            ids = ", ".join(str(value) for value in entry.get("tournament_ids") or [])
            suffix = f" [{ids}]" if ids else ""
            message = str(entry.get("message") or "")
            console.print(f"    - {message}{suffix}")
        if count > 3:
            console.print(f"    … {count - 3} more")


def run_season_blockers(args: Any, *, console: Any) -> int:
    report = season_blockers(args.season, root=args.root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return int(report["exit_code"])

    status = str(report.get("status") or "NOT_CHECKABLE")
    colour = "green" if status == "ELIGIBLE" else "yellow" if status == "HELD" else "red"
    console.print(f"[bold]Season {report['season']}[/bold]")
    console.print(f"Publication status: [{colour}]{status}[/{colour}]")
    if report.get("revision"):
        console.print(f"Canonical revision: {str(report['revision'])[:12]}")

    _render_section(console, "GENUINE BLOCKERS", report["genuine_blockers"])
    _render_section(console, "GLOBAL SAFETY BLOCKERS", report["global_blockers"])
    _render_section(console, "PREREQUISITE FAILURES", report["prerequisite_failures"])
    _render_section(console, "ACCEPTED / NON-BLOCKING", report["accepted_non_blocking"])
    _render_section(console, "HISTORICAL / DIAGNOSTIC DEBT", report["historical_debt"])

    audit = report.get("diagnostic_audit") or {}
    console.print("\n[bold]FULL-SEASON AUDIT[/bold]")
    console.print(f"  verdict: {audit.get('status') or 'NOT_CHECKABLE'}")
    console.print(f"  publication implication: {audit.get('publication_implication')}")

    console.print("\n[bold]NEXT ACTION[/bold]")
    console.print(f"  {report.get('next_action')}")
    return int(report["exit_code"])


__all__ = ["run_season_blockers"]
