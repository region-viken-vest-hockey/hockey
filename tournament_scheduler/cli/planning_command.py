"""
Planning command implementations for the RVV Miniputt CLI.

This module contains the implementations of the `rvv-miniputt season` subcommands
related to planning and replanning.
"""

from __future__ import annotations

import argparse
from datetime import date as _date
from pathlib import Path as _Path
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from ..pipeline.state import PipelineState

from rich.console import Console

_console = Console()


def _cmd_season_promote(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season promote`` — promote a plan to canonical season."""
    from ..pipeline.state import PipelineState
    from ..pipeline.stage4_export import run as run_export
    from ..season_state import (
        promote_from_stage3,
        schedule_path,
        decisions_path,
    )

    schedule, decisions = promote_from_stage3(
        work_dir=args.work_dir,
        season=args.season,
        root=args.root,
        actor=args.actor,
        force=args.force,
    )
    summary = {
        "season": schedule["season"],
        "schedule_path": str(schedule_path(schedule["season"], root=args.root)),
        "decisions_path": str(decisions_path(schedule["season"], root=args.root)),
        "fingerprint": schedule["fingerprint"],
        "tournament_count": len(schedule["plan"].get("tournaments", [])),
        "decision_count": len(decisions.get("decisions", {})),
    }
    if args.json:
        import json as _json

        print(_json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[green]✓[/green] Promoted {args.season} to canonical season"
        )
        _console.print(f"  season: {summary['season']}")
        _console.print(f"  schedule: {summary['schedule_path']}")
        _console.print(f"  decisions: {summary['decisions_path']}")
        _console.print(f"  fingerprint: {summary['fingerprint']}")
        _console.print(
            f"  tournaments: {summary['tournament_count']}, "
            f"decisions: {summary['decision_count']}"
        )
    return 0


def _cmd_season_plan(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season plan`` — generate a plan."""
    from ..pipeline.state import PipelineState
    from ..season_state import plan

    state = PipelineState(args.work_dir)
    result = plan(
        season=args.season,
        root=args.root,
        work_dir=args.work_dir,
        actor=args.actor,
        note=args.note,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _console.print(
            f"[green]✓[/green] Planned {args.season}: "
            f"{result['tournament_count']} tournament(s), "
            f"{result.get('placed_count', 0)} placed"
        )
        if result.get("verification"):
            v = result["verification"]
            _console.print(
                f"  verification: {v.get('ok', False)} ({len(v.get('violations', []))} violations)"
                )
            if not v.get("ok", True):
                for violation in v.get("violations", []):
                    _console.print(f"    [red]✗[/red] {violation}")
    return 0


def _cmd_season_replan(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season replan`` — replan around baseline."""
    from ..pipeline.state import PipelineState
    from ..canonical_replan import replan_around_baseline
    from ..operator_waivers import load_active_waivers
    from ..pipeline.stage1_config import load_effective_config
    from ..pipeline.state import StageName, StageStatus
    from ..season_state import apply_candidate

    replan_state = PipelineState(args.work_dir)
    try:
        replan_config = load_effective_config(replan_state)
    except Exception as exc:
        _console.print(f"[red]✗[/red] Kunne ikke laste Stage 1-konfigurasjon: {exc}")
        return 1
    if not replan_config or not replan_config.get("start_date") or not replan_config.get("end_date"):
        _console.print(
            "[red]✗[/red] Fant ingen brukbar Stage 1-konfigurasjon. Kjør Stage 1 på nytt før replan."
        )
        return 1

    result = replan_around_baseline(
        season=args.season,
        config=replan_config,
        scraping_result=replan_state.read_stage(StageName.SCRAPING),
        start_date=_date.fromisoformat(str(replan_config["start_date"])),
        end_date=_date.fromisoformat(str(replan_config["end_date"])),
        root=args.root,
        waivers=load_active_waivers(replan_state.work_dir),
        engine=str(args.engine).replace("-", "_"),
        request={
            "iterations": args.iterations,
            "seed": args.seed,
            "move_dates": args.move_dates,
            "move_hosts": args.move_hosts,
            "move_slots": args.move_slots,
        },
    )
    if result["lock_violations"]:
        _console.print("[red]✗[/red] Replan-kandidaten bryter kanoniske l\u00e5ser:")
        for violation in result["lock_violations"]:
            _console.print(f"  [red]•[/red] {violation['message']}")
        return 1
    if not result["verification"].get("ok", True):
        _console.print("[red]✗[/red] Replan-kandidaten feiler hard verifisering:")
        for violation in result["verification"].get("violations", []):
            _console.print(f"  [red]•[/red] {violation.get('message')}")
        return 1

    planning_checkpoint = replan_state.read_stage(StageName.PLANNING) or {}
    if not planning_checkpoint:
        planning_checkpoint = {"source": "season_replan"}
    planning_checkpoint = dict(planning_checkpoint)
    planning_checkpoint["plan"] = result["candidate"]
    planning_checkpoint["source"] = f"season_replan:{args.engine}"
    replan_state.write_stage(StageName.PLANNING, planning_checkpoint, status=StageStatus.DONE)

    cost = result["change_cost"]
    applied_schedule = None
    if args.apply:
        applied_schedule, _, _ = apply_candidate(
            season=args.season,
            candidate=result["candidate"],
            root=args.root,
            problem=result["problem"],
            allow_manual_placement=bool(getattr(args, "allow_manual_placement", False)),
            allow_host_confirmation=bool(getattr(args, "allow_host_confirmation", False)),
        )

    if args.json:
        print(
            _json.dumps(
                {
                    "change_cost": cost,
                    "revision": (applied_schedule or {}).get("revision"),
                    "applied": bool(args.apply),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    else:
        _console.print(
            f"[green]✓[/green] Replan av {args.season} skrevet til Stage 3"
            f"{' og anvendt' if args.apply else ''}; endringskostnad {cost['total']:.1f}"
        )
        for category, count in cost["counts"].items():
            _console.print(f"  {category}: {count}")
    return 0


def _cmd_season_diff_apply(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt season diff`` and ``rvv-miniputt season apply`` — compare or apply a candidate plan."""
    from ..pipeline.state import PipelineState
    from ..canonical_baseline import build_canonical_baseline, change_cost
    from ..operator_waivers import load_active_waivers
    from ..pipeline.stage1_config import load_effective_config
    from ..pipeline.state import StageName
    from ..planning_contract import build_planning_problem, extract_candidate
    from ..season_state import apply_candidate

    candidate_path = _Path(args.candidate)
    if not candidate_path.exists():
        _console.print(f"[red]✗[/red] Kandidatfil ikke funnet: {candidate_path}")
        return 1
    try:
        raw_candidate = _json.loads(candidate_path.read_text(encoding="utf-8"))
        candidate = extract_candidate(raw_candidate)
    except (OSError, ValueError, _json.JSONDecodeError) as exc:
        _console.print(f"[red]✗[/red] Kunne ikke lese kandidat: {exc}")
        return 1

    if args.season_command == "diff":
        canonical_schedule = load_schedule(args.season, root=args.root)
        canonical_decisions = load_decisions(args.season, root=args.root)
        baseline = build_canonical_baseline(canonical_schedule, canonical_decisions)
        cost = change_cost(baseline, candidate)
        if args.json:
            print(_json.dumps(cost, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            _console.print(f"[bold]Endringskostnad {args.season}[/bold]")
            for category, count in cost["counts"].items():
                _console.print(f"  {category}: {count}")
            _console.print(f"  total: {cost['total']:.1f}")
        return 0

    verify_state = PipelineState(args.work_dir)
    try:
        verify_config = load_effective_config(verify_state)
    except Exception:
        verify_config = {}
    verify_problem = None
    if verify_config and verify_config.get("start_date") and verify_config.get("end_date"):
        verify_problem = build_planning_problem(
            verify_config,
            verify_state.read_stage(StageName.SCRAPING),
            _date.fromisoformat(str(verify_config["start_date"])),
            _date.fromisoformat(str(verify_config["end_date"])),
            waivers=load_active_waivers(verify_state.work_dir),
        )
    canonical_schedule, canonical_decisions, cost = apply_candidate(
        season=args.season,
        candidate=candidate,
        root=args.root,
        problem=verify_problem,
        actor=args.actor,
        allow_manual_placement=bool(getattr(args, "allow_manual_placement", False)),
        allow_host_confirmation=bool(getattr(args, "allow_host_confirmation", False)),
    )
    if args.json:
        print(
            _json.dumps(
                {"schedule": canonical_schedule, "decisions": canonical_decisions, "change_cost": cost},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    else:
        _console.print(
            f"[green]✓[/green] Applied candidate to {args.season}; "
            f"revision {canonical_schedule.get('revision')} "
            f"(change cost {cost['total']:.1f})"
        )
    return 0