"""``rvv-miniputt season preview``: read-only consequence preview transport."""

from __future__ import annotations

import argparse
import json

from ..application.canonical_season.consequence_preview import preview_canonical_operation
from ..application.canonical_season_service import CanonicalSeasonService
from ..application.canonical_season.scenario import parse_scenario
from ..infrastructure.canonical_season_store import SeasonStateError
from .season_batch_command import _declared_scope
from .season_canonical_commands import _console, _emit_json, _verification_problem


def _preview_arguments(args: argparse.Namespace) -> dict:
    """Map the operation-specific CLI flags onto the shared preview contract."""
    if args.operation == "replace":
        if not (args.tournament_id and args.remove_team and args.add_team):
            raise ValueError("replace preview needs --tournament-id, --remove-team and --add-team")
        scoped = {
            "tournament_id": args.tournament_id,
            "remove_team_label": args.remove_team,
            "add_team_label": args.add_team,
        }
    elif args.operation == "swap":
        if not (args.tournament_a_id and args.team_a and args.tournament_b_id and args.team_b):
            raise ValueError("swap preview needs --tournament-a-id, --team-a, --tournament-b-id and --team-b")
        scoped = {
            "tournament_a_id": args.tournament_a_id,
            "team_a_label": args.team_a,
            "tournament_b_id": args.tournament_b_id,
            "team_b_label": args.team_b,
        }
    elif args.operation == "move":
        if not args.tournament_id:
            raise ValueError("move preview needs --tournament-id")
        scoped = {
            "tournament_id": args.tournament_id,
            "date": args.date,
            "arena": args.arena,
            "host_club": args.host_club,
            "start_time": args.start_time,
            "allow_cross_half": args.allow_cross_half,
            "allow_manual_placement": args.allow_manual_placement,
            "allow_host_confirmation": args.allow_host_confirmation,
        }
    elif args.operation == "repair":
        if not args.option_id:
            raise ValueError("repair preview needs --option-id")
        scoped = {
            "option_id": args.option_id,
            "finding_id": args.finding,
            "allow_manual_placement": args.allow_manual_placement,
            "allow_host_confirmation": args.allow_host_confirmation,
            "accept_regressions": args.accept_team_regressions or (),
            "regression_reason": args.accept_regression_reason,
        }
    elif args.operation == "batch":
        if not args.operations_file:
            raise ValueError("batch preview needs --operations-file")
        with open(args.operations_file, "r") as handle:
            scenario = parse_scenario(json.load(handle))
        scoped = {
            "operations": scenario["operations"],
            "scope": _declared_scope(args.scope, scenario["scope"]),
            "allow_manual_placement": args.allow_manual_placement,
            "allow_host_confirmation": args.allow_host_confirmation,
            "accept_regressions": args.accept_team_regressions,
            "accept_regression_reason": args.accept_regression_reason,
            "accept_reviewed_consequences": args.accept_reviewed_consequences,
        }
    else:
        if not (args.tournament_ids and args.remove_team):
            raise ValueError("remove preview needs --tournament-ids and --remove-team")
        scoped = {
            "tournament_ids": list(args.tournament_ids),
            "remove_team_label": args.remove_team,
            "reconcile_withdrawal": args.reconcile_withdrawal,
        }
    return {
        **scoped,
        "problem": _verification_problem(args),
        "actor": args.actor,
        "note": args.note,
        "request_id": args.request_id,
    }


def _cmd_season_preview(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season preview``; never writes canonical state."""
    try:
        arguments = _preview_arguments(args)
    except (OSError, ValueError, SeasonStateError) as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 2
    result = preview_canonical_operation(
        CanonicalSeasonService(root=args.root),
        operation=args.operation,
        season=args.season,
        expected_revision=args.expected_revision,
        **arguments,
    )
    if args.json:
        _emit_json(result)
    else:
        verdict = result["verdict"]
        _console.print(
            f"{args.operation} preview for {args.season}: {verdict['status']} "
            f"(revision {result['expected_revision']})"
        )
        for blocker in verdict["blockers"]:
            _console.print(f"  - {blocker}")
        if result.get("reviewed_token"):
            _console.print(f"reviewed token: {result['reviewed_token']}")
    if args.fail_on_blocked and not result["verdict"]["applicable"]:
        return 3
    return 0


def _cmd_season_swap_candidates(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season swap-candidates``; never writes canonical state."""
    from ..application.canonical_season.swap_search import search_participant_swaps

    try:
        result = search_participant_swaps(
            CanonicalSeasonService(root=args.root),
            season=args.season,
            tournament_ids=list(args.tournament_ids),
        )
    except (ValueError, SeasonStateError) as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 2
    if args.json:
        _emit_json(result)
    else:
        _console.print(
            f"swap candidates for {args.season}: {result['candidate_count']} evaluated, "
            f"{result['safe_candidate_count']} safe_to_apply (revision {result['canonical_state_revision']})"
        )
        if result["empty_reason"]:
            _console.print(f"  no candidates: {result['empty_reason']}")
        for item in result["candidates"]:
            _console.print(
                f"  {item['tournament_a_id']} {item['team_a_label']} <-> "
                f"{item['tournament_b_id']} {item['team_b_label']}: {item['verdict']['status']}"
            )
    return 0
