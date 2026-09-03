"""``rvv-miniputt plan`` — harness-neutral candidate verification/scoring (issue #257).

These commands operate only on the stable ``planning_problem``/``candidate``
contracts in :mod:`tournament_scheduler.planning_contract`. They do not call
into ``SeasonPlanner`` and do not require an LLM, so any planner
implementation's output can be checked the same way.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from typing import Any, Dict

from rich.console import Console

_console = Console()


def _load_json_file(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _cmd_plan(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt plan ...`` — dispatches to sub-subcommands."""
    if args.plan_command == "verify":
        return _cmd_plan_verify(args)
    if args.plan_command == "score":
        return _cmd_plan_score(args)
    if args.plan_command == "problem":
        return _cmd_plan_problem(args)
    _console.print("[yellow]Bruk: rvv-miniputt plan verify|score|problem[/yellow]")
    return 1


def _cmd_plan_verify(args: argparse.Namespace) -> int:
    from ..planning_contract import extract_candidate, verify_candidate

    try:
        candidate = extract_candidate(_load_json_file(args.candidate))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _console.print(f"[red]✗[/red] Kunne ikke lese kandidatplan: {exc}")
        return 1

    problem = None
    if args.problem:
        try:
            problem = _load_json_file(args.problem)
        except (OSError, json.JSONDecodeError) as exc:
            _console.print(f"[red]✗[/red] Kunne ikke lese planning_problem: {exc}")
            return 1

    result = verify_candidate(candidate, problem)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["ok"] else 1

    if result["ok"]:
        _console.print("[green]✓[/green] Kandidatplan består alle harde krav.")
    else:
        _console.print(f"[red]✗[/red] Kandidatplan bryter {len(result['violations'])} harde krav:")
        for v in result["violations"]:
            _console.print(f"  [red]•[/red] [{v['code']}] {v['message']}")
    if result["skipped"]:
        _console.print(
            f"[dim]Hoppet over (mangler --problem): {', '.join(result['skipped'])}[/dim]"
        )
    return 0 if result["ok"] else 1


def _cmd_plan_score(args: argparse.Namespace) -> int:
    from ..planning_contract import extract_candidate, score_candidate

    try:
        candidate = extract_candidate(_load_json_file(args.candidate))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _console.print(f"[red]✗[/red] Kunne ikke lese kandidatplan: {exc}")
        return 1

    report = score_candidate(candidate)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    participation = report["participation"]
    diversity = report["opponent_diversity"]
    turnaround = report["turnaround"]
    hosting = report["hosting"]

    _console.print("[bold]Deltakelse[/bold]")
    _console.print(f"  Spredning (maks-min kamper): {participation['spread']}")
    _console.print("[bold]Motstanderdiversitet[/bold]")
    _console.print(f"  Unike lagpar: {diversity['unique_pairs']}")
    _console.print(f"  Andel nye kamper (pairwise novelty): {diversity['pairwise_novelty']:.1%}")
    _console.print(f"  Lagpar som møtes 3+ ganger: {diversity['pairs_meeting_3_plus']}")
    _console.print(f"  Maks repetisjon for ett lagpar: {diversity['max_pair_repeat']}")
    _console.print(f"  Diversitet mellom klubber: {diversity['inter_club_diversity']:.1%}")
    _console.print(f"  Antall samme-klubb-oppgjør: {diversity['same_club_pairing_count']}")
    _console.print(f"  Maks lag fra samme klubb i én turnering: {diversity['max_same_club_teams_per_tournament']}")
    _console.print("[bold]Pause mellom turneringer[/bold]")
    _console.print(f"  Minste pause: {turnaround['min_turnaround_days']} dager")
    for threshold, count in turnaround["gaps_under_days"].items():
        _console.print(f"  Pauser under {threshold} dager: {count}")
    _console.print("[bold]Vertskap[/bold]")
    _console.print(f"  Spredning (maks-min turneringer som vert): {hosting['spread']}")
    _console.print("[bold]Månedsfordeling[/bold]")
    for month, count in sorted(report["month_distribution"].items()):
        _console.print(f"  {month}: {count}")
    return 0


def _cmd_plan_problem(args: argparse.Namespace) -> int:
    from ..planning_contract import build_planning_problem
    from ..pipeline.state import PipelineState, StageName

    state = PipelineState(args.work_dir)
    config = state.read_stage(StageName.CONFIG)
    if not config:
        _console.print("[red]✗[/red] Fant ingen Stage 1-konfigurasjon i arbeidsmappen. Kjør Stage 1 først.")
        return 1
    scraping_result = state.read_stage(StageName.SCRAPING)

    start_date = date.fromisoformat(args.start_date) if args.start_date else None
    end_date = date.fromisoformat(args.end_date) if args.end_date else None
    if start_date is None or end_date is None:
        planning_checkpoint = state.read_stage(StageName.PLANNING) or {}
        plan_dict = planning_checkpoint.get("plan", {})
        if start_date is None and plan_dict.get("start_date"):
            start_date = date.fromisoformat(plan_dict["start_date"])
        if end_date is None and plan_dict.get("end_date"):
            end_date = date.fromisoformat(plan_dict["end_date"])
    if start_date is None or end_date is None:
        _console.print(
            "[red]✗[/red] Kunne ikke bestemme planleggingsvinduet — oppgi --start-date/--end-date "
            "eller kjør Stage 3 først."
        )
        return 1

    problem = build_planning_problem(config, scraping_result, start_date, end_date)
    payload = json.dumps(problem, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(payload)
        _console.print(f"[green]✓[/green] Skrev planning_problem.json til {args.output}")
    else:
        print(payload)
    return 0
