"""
Plan adjustment command implementations for the RVV Miniputt CLI.

This module contains the implementations of the `rvv-miniputt replan` and
`rvv-miniputt adjust` commands for modifying the tournament plan.
"""

from __future__ import annotations

import argparse
import json as _json
from datetime import date as _date, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..pipeline.state import PipelineState

from rich.console import Console

_console = Console()


def _cmd_replan(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt replan`` — replan around baseline."""
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


def _load_critic_state(
    state: "PipelineState",
    work_dir: str,
) -> "tuple[object | None, list[str]]":
    """Reload the Stage 3 checkpoint and return (season_plan, issues).

    Returns (None, []) when no checkpoint exists so callers can detect and abort.
    """
    from .plan_critic import generate_critic_summary
    from ..pipeline.tournament_updater import TournamentUpdater

    try:
        season_plan = TournamentUpdater(state=state).load_plan()
    except ValueError:
        return None, []
    issues = generate_critic_summary(season_plan)
    return season_plan, issues


def _cmd_auto_adjust(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt auto-adjust`` — automated adjustment loop.

    Each iteration:
      1. Reload the Stage 3 checkpoint and re-run the plan critic.
      2. Break early if ``count_critic_issues_from_dict`` returns 0.
      3. Translate the first auto-fixable issue to a concrete move via ``suggest_moves``.
      4. Apply the move by calling ``_cmd_replan`` internally.
      5. Reload checkpoint and re-evaluate before the next iteration.

    Repeats until all auto-fixable issues are resolved or ``--max-iterations``
    is reached.  Non-auto-fixable issues are collected and printed at the end.
    """
    from ..pipeline.state import PipelineState
    from .plan_critic import count_issues_from_plan, suggest_moves

    state = PipelineState(args.work_dir)
    max_iter = getattr(args, "max_iterations", 3)

    _console.print(
        f"[bold cyan]Auto-adjust:[/bold cyan] starter justeringsløkke "
        f"(max {max_iter} iterasjoner)…"
    )

    applied_total = 0
    manual_issues: list = []
    iteration = 0
    # Track recently-moved IDs (window=2) to break A↔B cascade cycles
    recently_moved: list[str] = []
    _CYCLE_WINDOW = 2

    for iteration in range(1, max_iter + 1):
        # Reload checkpoint and re-run critic at the start of every iteration
        season_plan, issues = _load_critic_state(state, args.work_dir)
        if season_plan is None:
            _console.print(
                f"[red]✗[/red] Ingen Stage 3-checkpoint funnet i '{args.work_dir}'. "
                "Kjør ``rvv-miniputt run`` først."
            )
            return 1

        # Use count_issues_from_plan as the fast early-exit check
        from ..pipeline.state import StageName
        raw_checkpoint = state.read_stage(StageName.PLANNING)
        plan_raw = (raw_checkpoint or {}).get("plan") if isinstance(raw_checkpoint, dict) else None
        issue_count = count_issues_from_plan(plan_raw) if plan_raw is not None else len(issues)

        if issue_count == 0:
            _console.print(
                f"[green]✓[/green] Ingen problemer funnet etter {iteration - 1} iterasjon(er)."
            )
            break

        moves = suggest_moves(season_plan, issues)
        auto_moves = [m for m in moves if m["can_auto_fix"] and m["tournament_id"]]
        manual_moves = [m for m in moves if not m["can_auto_fix"]]

        # Collect manual-review issues (deduplicated across iterations)
        for m in manual_moves:
            if m["issue"] not in [mi["issue"] for mi in manual_issues]:
                manual_issues.append(m)

        # Skip tournament IDs cascade-placed in recent iterations to break A↔B cycles
        fresh_moves = [m for m in auto_moves if m["tournament_id"] not in recently_moved]
        if not fresh_moves:
            # All candidates were recently moved — cycle detected, clear window and retry
            recently_moved.clear()
            fresh_moves = auto_moves

        if not fresh_moves:
            _console.print(
                f"[yellow]![/yellow] Iterasjon {iteration}: ingen auto-fikserbare problemer "
                f"gjenstår ({issue_count} problem(er) krever manuell behandling)."
            )
            break

        _console.print(
            f"\n[bold]Iterasjon {iteration}/{max_iter}[/bold] — "
            f"{issue_count} problem(er), {len(auto_moves)} auto-fikserbar(e):"
        )

        # Apply ONE move per iteration, then reload and re-evaluate
        move = fresh_moves[0]
        tid = move["tournament_id"]
        new_date = move["new_date"]
        reason = move["reason"]

        _console.print(f"  [cyan]→[/cyan] Turneringsid {tid}: flyttes til {new_date}")
        _console.print(f"    [dim]{reason}[/dim]")

        replan_args = argparse.Namespace(
            tournament_id=tid,
            new_date=new_date,
            suggest=False,
            reason=reason,
            force=True,
            work_dir=args.work_dir,
            export_dir=args.export_dir,
            timestamped_export=getattr(args, "timestamped_export", False),
        )
        # Snapshot dates before replan so we can detect cascade victims afterward
        pre_dates = {t.id: t.date for t in season_plan.tournaments}
        rc = _cmd_replan(replan_args)
        if rc == 0:
            applied_total += 1
            # Detect all tournaments whose dates changed (both the moved one and
            # any cascade victims) and add them to the cycle-detection window.
            post_plan, _ = _load_critic_state(state, args.work_dir)
            if post_plan is not None:
                for t in getattr(post_plan, "tournaments", []):
                    if pre_dates.get(t.id) != t.date:
                        if t.id not in recently_moved:
                            recently_moved.append(t.id)
            if len(recently_moved) > _CYCLE_WINDOW * 4:
                recently_moved = recently_moved[-(_CYCLE_WINDOW * 4):]
            # Reload and re-evaluate immediately so the next iteration starts fresh
            _, refreshed_issues = _load_critic_state(state, args.work_dir)
            remaining = len(refreshed_issues)
            _console.print(
                f"  [green]✓[/green] Endring brukt — "
                f"{remaining} problem(er) gjenstår etter reload."
            )
        else:
            _console.print(
                f"  [red]✗[/red] Kunne ikke flytte {tid} — avbryter løkken."
            )
            break
    else:
        _console.print(
            f"[yellow]![/yellow] Maks iterasjoner ({max_iter}) nådd — "
            "noen problemer kan gjenstå."
        )

    # Summary
    _console.print(
        f"\n[bold]Auto-adjust ferdig:[/bold] {applied_total} endring(er) brukt "
        f"over {iteration} iterasjon(er)."
    )

    # Collect any remaining unresolved issues after the loop
    _, remaining_issues = _load_critic_state(state, args.work_dir)
    if remaining_issues:
        remaining_moves = []
        if remaining_issues:
            # We need a plan object for suggest_moves — reload once more
            from ..pipeline.state import StageName as _SN
            _chk = state.read_stage(_SN.PLANNING)
            _sp = _chk.get("plan") if isinstance(_chk, dict) else None
            if _sp is not None:
                from .plan_critic import suggest_moves as _sm
                remaining_moves = _sm(_sp, remaining_issues)

        _print_escalation_table(remaining_issues, remaining_moves, manual_issues)

    elif manual_issues:
        # No remaining auto-fixable issues but there are known manual ones
        _print_escalation_table([], [], manual_issues)

    return 0


def _print_escalation_table(
    remaining_issues: list,
    remaining_moves: list,
    manual_issues: list,
) -> None:
    """Print a Rich-formatted escalation table for issues that could not be auto-fixed.

    ``remaining_issues`` are issues still present after the loop.
    ``remaining_moves`` are the move proposals for those issues (may be empty).
    ``manual_issues`` are issues collected during the loop that were flagged as
    non-auto-fixable from the start.
    """
    from rich import box
    from rich.panel import Panel
    from rich.table import Table

    # Merge remaining + manual, deduplicated by issue string
    seen: set = set()
    rows: list = []

    move_by_issue: dict = {m["issue"]: m for m in remaining_moves}

    for issue in remaining_issues:
        if issue not in seen:
            seen.add(issue)
            m = move_by_issue.get(issue)
            rows.append(
                (
                    issue,
                    m["reason"] if m else "Ikke analysert",
                    "Ja" if (m and m["can_auto_fix"]) else "Nei",
                )
            )

    for mi in manual_issues:
        if mi["issue"] not in seen:
            seen.add(mi["issue"])
            rows.append((mi["issue"], mi["reason"], "Nei"))

    if not rows:
        return

    table = Table(
        title="Uløste problemer — manuell gjennomgang nødvendig",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold yellow",
        expand=True,
    )
    table.add_column("Problem", style="yellow", ratio=4)
    table.add_column("Foreslått tiltak", style="dim", ratio=5)
    table.add_column("Auto-fikserbar?", style="cyan", ratio=1, justify="center")

    for problem, action, auto in rows:
        table.add_row(problem, action, auto)

    panel = Panel(
        table,
        title="[bold red]Eskalering — disse problemene krever manuell handling[/bold red]",
        border_style="red",
        box=box.ROUNDED,
    )
    _console.print()
    _console.print(panel)


