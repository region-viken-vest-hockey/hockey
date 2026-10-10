"""Pipeline work-directory helpers shared by the adjust and review commands."""

from __future__ import annotations

import sys

from rich.console import Console

_console = Console()


def do_re_export(work_dir: str, export_dir: str, *, timestamped_export: bool = False) -> int:
    """Re-export Stage 4 from the current plan checkpoint. Returns exit code."""
    from ..pipeline.state import PipelineState, StageName
    from ..pipeline.stage4_export import run as run_export

    state = PipelineState(work_dir)
    plan_checkpoint = state.read_stage(StageName.PLANNING)
    if not plan_checkpoint:
        _console.print("[red]✗[/red] Ingen Stage 3-plan funnet.")
        return 1

    try:
        result = run_export(plan_checkpoint, state=state, export_dir=export_dir, strict=True, timestamped_export=timestamped_export)
        files = result.get("output_files", {})
        _console.print(f"  [green]✓[/green] {len(files)} fil(er) eksportert")
        for label, path in files.items():
            _console.print(f"    → {path}")
        return 0
    except Exception as exc:
        _console.print(f"  [red]✗[/red] Eksport feilet: {exc}")
        return 1


def load_plan_and_updater(work_dir: str):
    """Load the season plan and return (plan, updater, state). Raises SystemExit on error."""
    from ..pipeline.state import PipelineState
    from ..pipeline.tournament_updater import TournamentUpdater

    state = PipelineState(work_dir)
    updater = TournamentUpdater(state=state)
    try:
        plan = updater.load_plan()
    except ValueError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        sys.exit(1)
    return plan, updater, state
