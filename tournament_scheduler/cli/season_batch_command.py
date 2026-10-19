"""Season batch command handler (``season batch``)."""

from __future__ import annotations

import argparse
import json

from rich.console import Console

from ..application.canonical_season.batch import batch_maintenance
from ..application.canonical_season.scenario import parse_scenario
from ..application.canonical_season_service import CanonicalSeasonService
from ..infrastructure.canonical_season_store import SeasonStateError
from .verification_problem import _canonical_verification_problem

_console = Console()


def _declared_scope(cli_scope: list[str] | None, scenario_scope: list[str] | None) -> list[str] | None:
    """Return the one declared scope; a CLI scope and a scenario scope must agree."""

    cli_ids = None
    if cli_scope:
        cli_ids = [part.strip() for item in cli_scope for part in item.split(",") if part.strip()]
    if cli_ids is not None and scenario_scope is not None and set(cli_ids) != set(scenario_scope):
        raise ValueError("--scope disagrees with the scope declared in the scenario document")
    return cli_ids if cli_ids is not None else scenario_scope


def _cmd_season_batch(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season batch`` — atomically compose several scoped canonical mutations in one commit."""
    if not args.dry_run and not (args.expected_revision and args.reviewed_token):
        _console.print(
            "[red]✗[/red] Apply needs --expected-revision and --reviewed-token from the matching preview"
        )
        return 2
    try:
        with open(args.operations, "r") as f:
            scenario = parse_scenario(json.load(f))
        scope = _declared_scope(args.scope, scenario["scope"])
    except (OSError, ValueError, SeasonStateError) as exc:
        _console.print(f"[red]✗[/red] Failed to read scenario '{args.operations}': {exc}")
        return 1

    result = batch_maintenance(
        CanonicalSeasonService(root=args.root),
        season=args.season,
        operations=scenario["operations"],
        scope=scope,
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
        expected_revision=args.expected_revision,
        expected_token=args.reviewed_token,
    )

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

    if args.dry_run and args.fail_on_blocked and not result.get("consequence_acceptable"):
        return 3
    return 0
