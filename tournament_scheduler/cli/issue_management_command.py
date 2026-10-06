"""
Issue management command implementations for the RVV Miniputt CLI.

This module contains the implementations of the `rvv-miniputt season` subcommands
for managing issues, blockers, findings, and infeasibility reports.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from ..pipeline.state import PipelineState

from rich.console import Console

_console = Console()


def _cmd_season_blockers(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season blockers`` — show blockers."""
    from .season_blockers_command import run_season_blockers

    return run_season_blockers(args, console=_console)


def _cmd_season_findings(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season findings`` — show findings."""
    from ..season_maintenance import list_findings

    state = PipelineState(args.work_dir)
    report = list_findings(args.season, root=args.root)
    if args.json:
        import json as _json

        print(_json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[bold]Funn {args.season}[/bold] (revision {str(report['revision'])[:12]}, "
            f"{report['finding_count']} funn)"
        )
        comparison = report.get("baseline_comparison") or {}
        if comparison.get("active"):
            summary = comparison.get("summary") or {}
            _console.print("[bold]Baseline comparison[/bold]")
            for status in ("NEW", "REGRESSED", "IMPROVED", "RESOLVED", "KNOWN"):
                _console.print(f"  {status:<9} {int(summary.get(status, 0))}")
            if not comparison.get("new_count") and not comparison.get("regression_count"):
                _console.print("[green]No regressions relative to accepted baseline.[/green]")
            if getattr(args, "all", False):
                visible = report["findings"]
            else:
                wanted = {"NEW", "REGRESSED"}
                entry_status = {
                    str(entry.get("finding_id") or ""): str(entry.get("status") or "")
                    for entry in comparison.get("entries") or []
                }
                # Hard findings are never baseline-suppressible: keep
                # them visible even when the default view emphasizes
                # only NEW/REGRESSED accepted-debt changes.
                visible = [
                    finding
                    for finding in report["findings"]
                    if entry_status.get(str(finding.get("finding_id") or "")) in wanted
                    or str(finding.get("severity") or "").lower() == "hard"
                ]
        else:
            visible = report["findings"]
        for finding in visible:
            is_hard = str(finding.get("severity") or "").lower() == "hard"
            marker = "[red]![/red] " if is_hard else "  "
            _console.print(
                f"{marker}[dim]{finding['category']}[/dim] {finding['finding_id']}: {finding['message']}"
            )
    return 0


def _cmd_season_audit(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season audit`` — run season audit."""
    from ..season_maintenance import season_audit

    report = season_audit(args.season, root=args.root)
    if args.json:
        print(_json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        audit = report.get("audit") or {}
        status = str(audit.get("status") or "UNKNOWN")
        colour = "green" if status == "PASS" else "yellow" if status == "INCOMPLETE" else "red"
        _console.print(
            f"[bold]Sesongrevisjon {args.season}[/bold] "
            f"(revisjon {str(report.get('revision'))[:12]})"
        )
        _console.print(
            f"  [{colour}]{status}[/{colour}] {audit.get('check_count', 0)} sjekker, "
            f"{audit.get('blocking_finding_count', 0)} blokkerende funn"
        )
        for reason in audit.get("reasons") or []:
            _console.print(f"  [dim]- {reason}[/dim]")
        if audit.get("incomplete_checks"):
            _console.print(
                "  [yellow]ufullstendig dekning:[/yellow] "
                + ", ".join(audit["incomplete_checks"])
            )
    return 0


def _cmd_season_record_infeasibility(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season record-infeasibility`` — record placement infeasibility proofs."""
    from ..pipeline.state import PipelineState
    from ..season_maintenance import record_placement_infeasibility_proofs

    state = PipelineState(args.work_dir)
    result = record_placement_infeasibility_proofs(
        season=args.season,
        root=args.root,
        finding_ids=list(getattr(args, "finding", None) or []),
        actor=args.actor,
        note=args.note,
        expected_revision=args.expected_revision,
        dry_run=bool(args.dry_run),
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        prefix = "Validated" if result.get("dry_run") else "Recorded"
        _console.print(
            f"[green]✓[/green] {prefix} {result.get('recorded_count', 0)} "
            f"placement-infeasibility proof(s) for {args.season} "
            f"(revision {str(result.get('canonical_state_revision'))[:12]})"
        )
        for entry in result.get("recorded") or []:
            _console.print(
                f"  [green]•[/green] {entry.get('obligation_id')} "
                f"({entry.get('age_group')} {entry.get('source_date')}, "
                f"{entry.get('candidate_count')} rejected candidate(s))"
            )
        for entry in result.get("skipped") or []:
            _console.print(
                f"  [yellow]⚠[/yellow] {entry.get('obligation_id')} not proven: "
                f"{entry.get('reason')} ({entry.get('coverage_status')})"
            )
    return 0


def _cmd_season_release_infeasibility(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season release-infeasibility`` — release placement infeasibility proofs."""
    from ..pipeline.state import PipelineState
    from ..season_maintenance import release_placement_infeasibility_proofs

    state = PipelineState(args.work_dir)
    result = release_placement_infeasibility_proofs(
        season=args.season,
        root=args.root,
        finding_ids=list(getattr(args, "finding", None) or []),
        actor=args.actor,
        note=args.note,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[green]✓[/green] Released {len(result.get('released_obligation_ids') or [])} "
            f"placement-infeasibility proof(s) for {args.season} "
            f"(revision {str(result.get('canonical_state_revision'))[:12]})"
        )
    return 0


def _cmd_season_infeasibility_report(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season infeasibility-report`` — show infeasibility report."""
    from ..pipeline.state import PipelineState
    from ..season_maintenance import placement_infeasibility_report

    state = PipelineState(args.work_dir)
    result = placement_infeasibility_report(
        season=args.season,
        root=args.root,
        include_superseded=bool(getattr(args, "include_superseded", False)),
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[bold]Placement infeasibility {args.season}[/bold] "
            f"(revision {str(result.get('canonical_state_revision'))[:12]})"
            f"  {result.get('current_proof_count', 0)} current / "
            f"{result.get('active_proof_count', 0)} active proof(s); "
            f"{len(result.get('unproven_obligation_ids') or [])} unproven obligation(s)"
        )
        for entry in result.get("proofs") or []:
            marker = "[green]✔[/green]" if entry.get("current") else "[yellow]⚠[/yellow]"
            _console.print(
                f"  {marker} {entry.get('obligation_id')} "
                f"[{entry.get('status')}] {entry.get('stale_reason') or 'current'}"
            )
    return 0