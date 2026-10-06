"""
Plan adjustment command implementations for the RVV Miniputt CLI.

This module contains the implementations of the `rvv-miniputt replan` and
`rvv-miniputt adjust` commands for modifying the tournament plan.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from ..pipeline.state import PipelineState

from rich.console import Console

_console = Console()


def _cmd_replan(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt replan`` — replan around baseline."""
    from ..pipeline.state import PipelineState
    from ..season_state import load_schedule, load_decisions
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


def _cmd_adjust(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt adjust`` — adjust tournament details."""
    from ..pipeline.state import PipelineState
    from ..pipeline.adjustment_workflow import AdjustmentWorkflow

    work_dir = args.work_dir
    state = PipelineState(work_dir)
    wf = AdjustmentWorkflow(state)

    # Load the plan first to verify we have something to work with.
    try:
        plan = wf.load_plan()
    except ValueError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    # --- No tournament ID: list available tournaments ---
    if not args.tournament_id:
        _console.print("[bold]Turneringer i sesongplanen:[/bold]\n")
        for t in plan.tournaments:
            status = ""
            if t.cancelled:
                status = f" [red](AVLYST: {t.cancellation_reason or 'ingen grunn'})[/red]"
            _console.print(
                f"  [cyan]{t.id}[/cyan]  {t.date.isoformat()}  "
                f"{t.age_group:5s}  {t.arena:20s}  "
                f"{len(t.teams)} lag{status}"
            )
        _console.print(
            "\nBruk [bold]rvv-miniputt adjust --tournament-id <id>[/bold] "
            "for å justere en turnering."
        )
        return 0

    tid = args.tournament_id

    # --- Find the tournament ---
    try:
        tournament = wf._find_tournament(plan, tid)
    except ValueError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    # --- Apply adjustments ---
    changes_made = False

    if args.date:
        try:
            new_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            _console.print(
                f"[red]✗[/red] Ugyldig datoformat '{args.date}'. Bruk YYYY-MM-DD."
            )
            return 1
        tournament.date = new_date
        changes_made = True

    if args.age_group:
        tournament.age_group = args.age_group
        changes_made = True

    if args.arena:
        tournament.arena = args.arena
        changes_made = True

    if args.teams is not None:
        # Parse teams format: "club1:label1,club2:label2"
        teams = []
        for team_str in args.teams.split(","):
            if ":" in team_str:
                club, label = team_str.split(":", 1)
                teams.append({"club": club.strip(), "label": label.strip()})
            else:
                _console.print(
                    f"[yellow]⚠[/yellow] Ignoring invalid team format: {team_str}"
                )
        tournament.teams = teams
        changes_made = True

    if not changes_made:
        _console.print("[yellow]⚠[/yellow] No changes specified")
        return 0

    # --- Validate the adjusted tournament ---
    # TODO: Add validation logic here

    # --- Write the plan checkpoint ---
    wf.write_plan(plan, log_entry=f"Adjusted tournament {tid}")
    _console.print(f"[green]✓[/green] Adjusted tournament {tid}")

    # --- Re-export ---
    if not args.no_export:
        _console.print("\n[bold]Re-eksporterer...[/bold]")
        try:
            # This would call the appropriate export function
            _console.print("  [yellow]⚠[/yellow] Export not yet implemented for adjust")
        except Exception as exc:
            _console.print(f"  [red]✗[/red] Eksport feilet: {exc}")
            return 1

    _console.print("\n[bold green]✓ Ferdig.[/bold green]")
    return 0