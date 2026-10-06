"""
Repair command implementation for the RVV Miniputt CLI.

This module contains the implementation of the `rvv-miniputt season` subcommands
for managing repairs and deviations.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from ..pipeline.state import PipelineState

from rich.console import Console

_console = Console()


def _cmd_season_repair_options(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season repair-options`` and ``rvv-miniputt season search`` — show repair options."""
    from ..season_maintenance import repair_options, search

    if args.season_command == "search":
        dimensions = [
            part.strip()
            for part in str(args.dimensions or "").split(",")
            if part.strip()
        ]
        report = search(
            args.season,
            args.finding,
            root=args.root,
            age_group=getattr(args, "age_group", None),
            dimensions=dimensions,
            allow_manual_placement=bool(getattr(args, "allow_manual_placement", False)),
            allow_host_confirmation=bool(getattr(args, "allow_host_confirmation", False)),
        )
    else:
        report = repair_options(
            args.season,
            args.finding,
            root=args.root,
            age_group=getattr(args, "age_group", None),
            allow_search=bool(args.allow_search),
            allow_manual_placement=bool(getattr(args, "allow_manual_placement", False)),
            allow_host_confirmation=bool(getattr(args, "allow_host_confirmation", False)),
        )
    if args.json:
        import json as _json

        print(_json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[bold]{args.finding}[/bold]: {report['option_count']} alternativ "
            f"(revision {str(report['revision'])[:12]})"
        )
        for option in report["options"]:
            marker = "[magenta]P[/magenta] " if option.get("non_dominated") else "  "
            _console.print(
                f"  {marker}[green]•[/green] {option['option_id']} ({option.get('family')})"
            )
        pareto = report.get("pareto") or {}
        if pareto.get("non_dominated_option_ids"):
            _console.print(
                f"  [magenta]P[/magenta] = Pareto-front: "
                f"{pareto['front_size']} av {pareto.get('measured_option_count', 0)} målt"
            )
        if not report["options"]:
            _console.print(
                f"  [yellow]⚠[/yellow] ingen lovlige alternativer: "
                f"{report['escalation'].get('reason')}"
            )
    return 0


def _cmd_season_apply_repair(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season apply-repair`` — apply a repair option."""
    from ..season_maintenance import apply_repair

    result = apply_repair(
        args.season,
        args.option_id,
        args.expected_revision,
        root=args.root,
        actor=args.actor,
        dry_run=args.dry_run,
        finding_id=args.finding,
        age_group=getattr(args, "age_group", None),
        dimensions=[
            part.strip()
            for part in str(getattr(args, "dimensions", "") or "").split(",")
            if part.strip()
        ],
        allow_manual_placement=bool(getattr(args, "allow_manual_placement", False)),
        allow_host_confirmation=bool(getattr(args, "allow_host_confirmation", False)),
        accept_regressions=getattr(args, "accept_regressions", None),
        regression_reason=getattr(args, "accept_regression_reason", None),
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if result.get("ok"):
            action = "Validated repair preview for" if result.get("dry_run") else "Applied repair to"
            _console.print(
                f"[green]✓[/green] {action} {args.season}; "
                f"revision {str(result.get('revision_before'))[:12]} -> "
                f"{str(result.get('revision_after'))[:12]}"
            )
            _console.print(f"  {_format_delta(result.get('delta'))}")
        else:
            _console.print(
                f"[red]✗[/red] Avvist ({result.get('reason')}); kanonisk revisjon uendret"
            )
    return 0


def _cmd_season_accept_deviation(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season accept-deviation`` and ``rvv-miniputt season revoke-acceptance`` — accept or revoke acceptance of a deviation."""
    from ..season_maintenance import accept_finding, revoke_acceptance

    if args.season_command == "accept-deviation":
        result = accept_finding(
            args.season,
            args.finding,
            root=args.root,
            age_group=getattr(args, "age_group", None),
            actor=args.actor,
            note=args.note,
        )
        record = result["acceptance"]
        verb = "Aksepterte"
    else:
        result = revoke_acceptance(
            args.season,
            args.finding,
            root=args.root,
            age_group=getattr(args, "age_group", None),
            actor=args.actor,
            note=args.note,
        )
        record = result["revoked"]
        verb = "Tilbakekalte aksept for"
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[green]✓[/green] {verb} {args.finding} "
            f"(revisjon {str(result.get('revision'))[:12]})"
        )
        _console.print(
            f"  avvik {record.get('accepted_deviation')} mot mål {record.get('target')} "
            f"i {record.get('scope')}"
        )
    return 0


def _format_delta(delta: dict | None) -> str:
    if not delta:
        return ""
    parts = []
    if delta.get("total_travel_km_before") is not None:
        parts.append(
            f"reise (km): "
            f"{delta.get('total_travel_km_before', 0):.0f} -> "
            f"{delta.get('total_travel_km_after', 0):.0f} "
            f"({delta.get('total_travel_km_delta', 0):+.0f})"
        )
    if delta.get("quality_regressions") is not None:
        parts.append(
            f"kvalitetsregresjoner: "
            f"{len(delta.get('quality_regressions') or [])}"
        )
    return "; ".join(parts)