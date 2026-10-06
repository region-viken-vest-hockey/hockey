"""
Retire team command implementation for the RVV Miniputt CLI.

This module contains the implementation of the `rvv-miniputt season retire-team` command.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from ..pipeline.state import PipelineState

from rich.console import Console

_console = Console()


def _cmd_season_retire_team(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season retire-team`` — retire a team and rebalance."""
    from ..application.canonical_season_service import CanonicalSeasonService
    import json as _json

    service = CanonicalSeasonService(root=args.root)
    rebalance_proposals = None
    if args.accept_rebalance:
        if not args.rebalance_proposals:
            _console.print("[red]✗[/red] --rebalance-proposals JSON file required with --accept-rebalance")
            return 1
        with open(args.rebalance_proposals, "r", encoding="utf-8") as f:
            rebalance_proposals = _json.load(f)
    if args.dry_run:
        result = service.preview_retire_team(
            season=args.season,
            club=args.club,
            team_label=args.team,
            age_group=args.age_group,
            effective_from=args.effective_from,
            request_id=args.request_id,
            actor=args.actor,
            note=args.note,
            host_club=args.host_club,
        )
    else:
        result = service.apply_retire_team(
            season=args.season,
            club=args.club,
            team_label=args.team,
            age_group=args.age_group,
            effective_from=args.effective_from,
            request_id=args.request_id,
            actor=args.actor,
            note=args.note,
            accept_rebalance=args.accept_rebalance,
            rebalance_proposals=rebalance_proposals,
            host_club=args.host_club,
        )
    if args.json:
        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(f"[bold]Team Retirement {'Preview' if args.dry_run else 'Applied'}[/bold]")
        classification = result.get("classification", result.get("retirement", {}))
        if "classification" in result:
            # Preview
            _console.print(f"  Retiring team: {classification['retiring_team']['club']} / {classification['retiring_team']['label']} ({classification['retiring_team']['age_group']})")
            _console.print(f"  Effective from: {classification['effective_from']}")
            _console.print(f"  Hosted tournaments cancelled: {len(classification['hosted_tournaments']['cancel'])}")
            for t in classification['hosted_tournaments']['cancel']:
                _console.print(f"    {t['tournament_id']} ({t['date']} {t['arena']})")
            _console.print(f"  Away tournaments withdrawn: {len(classification['away_participation']['remove_from'])}")
            for t in classification['away_participation']['remove_from']:
                _console.print(f"    {t['tournament_id']} ({t['date']} @ {t['host_club']})")
            _console.print(f"  Rebalance proposals: {len(classification['rebalance']['proposals'])}")
            for p in classification['rebalance']['proposals']:
                _console.print(f"    {p['tournament_id']}: +{p['candidate_club']}/{p['candidate_label']}")
            _console.print(f"  Unresolved vacancies: {len(classification['rebalance']['unresolved_vacancies'])}")
            if classification['rebalance']['unresolved_vacancies']:
                for v in classification['rebalance']['unresolved_vacancies']:
                    _console.print(f"    {v}")
            _console.print(f"  Can apply: {'Yes' if classification['can_apply'] else 'No'}")
            if not classification['can_apply']:
                _console.print(f"  Verification OK: {'Yes' if classification['verification']['ok'] else 'No'}")
                _console.print(f"  Hosting OK: {'Yes' if classification['hosting_responsibility']['ok'] else 'No'}")
                _console.print(f"  Protections OK: {'Yes' if classification['change_protections']['ok'] else 'No'}")
                _console.print(f"  Constraints OK: {'Yes' if classification['request_constraints']['acceptable'] else 'No'}")
        else:
            # Applied
            _console.print(f"  Retiring team: {classification['retiring_team']['club']} / {classification['retiring_team']['label']} ({classification['retiring_team']['age_group']})")
            _console.print(f"  Effective from: {classification['effective_from']}")
            _console.print(f"  Hosted tournaments cancelled: {len(classification['cancelled_hosted'])}")
            for t in classification['cancelled_hosted']:
                _console.print(f"    {t['tournament_id']} ({t['date']} {t['arena']})")
            _console.print(f"  Away tournaments withdrawn: {len(classification['withdrawn_away'])}")
            for t in classification['withdrawn_away']:
                _console.print(f"    {t['tournament_id']} ({t['date']} @ {t['host_club']})")
            _console.print(f"  Rebalance applied: {len(classification['rebalance_applied'])}")
            for r in classification['rebalance_applied']:
                _console.print(f"    {r['tournament_id']}: +{r['added_team']['club']}/{r['added_team']['label']}")
            _console.print(f"  Revision: {result.get('revision')}")
            _console.print(f"  Canonical state revision: {result.get('canonical_state_revision')}")
    return 0