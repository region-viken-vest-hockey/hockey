"""Season batch command handler (``season batch``)."""

from __future__ import annotations

import argparse

from rich.console import Console

_console = Console()


def _cmd_season_batch(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season batch`` — atomically compose several scoped canonical mutations in one commit."""
    from ..season_state import batch_maintenance
    from .verification_problem import _canonical_verification_problem
    import json

    # Read operations from file
    try:
        with open(args.operations, 'r') as f:
            operations = json.load(f)
    except Exception as exc:
        _console.print(f"[red]✗[/red] Failed to read operations file '{args.operations}': {exc}")
        return 1

    # Process scope: split comma-separated values if provided
    scope = None
    if args.scope:
        scope = []
        for s in args.scope:
            scope.extend([part.strip() for part in s.split(',') if part.strip()])

    # Call batch_maintenance
    result = batch_maintenance(
        season=args.season,
        operations=operations,
        scope=scope,
        root=args.root,
        problem=_canonical_verification_problem(args.work_dir, args.season, args.root),
        actor=args.actor,
        note=args.note,
        dry_run=args.dry_run,
        request_id=args.request_id,
        allow_manual_placement=args.allow_manual_placement,
        allow_host_confirmation=args.allow_host_confirmation,
        accept_regressions=args.accept_team_regressions,
        accept_regression_reason=args.accept_regression_reason,
        accept_reviewed_consequences=args.accept_reviewed_consequences,
    )

    # Output
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if args.dry_run:
            if result.get("consequence_acceptable"):
                _console.print(f"[green]✓[/green] Batch dry-run successful for season {args.season} (consequence acceptable)")
            else:
                _console.print(f"[yellow]⚠[/yellow] Batch dry-run blocked for season {args.season} (consequence NOT acceptable)")
        else:
            _console.print(f"[green]✓[/green] Batch applied successfully for season {args.season}")

    # Handle --fail-on-blocked
    if args.dry_run and args.fail_on_blocked and not result.get("consequence_acceptable"):
        return 3
    return 0
