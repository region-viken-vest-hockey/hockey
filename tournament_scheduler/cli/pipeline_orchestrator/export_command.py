"""Stage 4 export runner and calendar regeneration."""

from __future__ import annotations

import argparse
from typing import Any

from ._shared import _console

def _run_stage4_export(
    args: "argparse.Namespace",
    plan: "dict[str, Any]",
    state: "Any",
    strict: bool,
    log_fn: "Any",
    resume_from: int,
) -> "tuple[bool, bool, bool]":
    """Run Stage 4 (export) or skip it when resuming from a later stage.

    Returns ``(generated_calendars, abort, stage_failed)`` where
    *generated_calendars* is True when the export produced a calendars_html
    file, *abort* is True when the pipeline should stop (caller writes the
    run log and returns 1), and *stage_failed* is True when the stage failed
    in non-strict mode.
    """
    from ...pipeline.stage4_export import run as stage4_run

    if resume_from <= 4:
        _console.print("[bold]Stage 4:[/bold] Eksport...")
        try:
            from time import perf_counter

            from ...pipeline.run_manifest import RunManifest

            _started = perf_counter()
            export = stage4_run(
                plan,
                state,
                export_dir=args.export_dir,
                strict=strict,
                timestamped_export=getattr(args, "timestamped_export", True),
            )
            try:
                RunManifest(state.work_dir).record_timing("stage4_export_seconds", perf_counter() - _started)
            except Exception as exc:
                log_fn(f"Stage 4: could not record timing: {exc}")
            files = export.get("output_files", {})
            generated_calendars = "calendars_html" in files
            _console.print(f"  [green]✓[/green] {len(files)} fil(er) eksportert")
            for label, file_path in files.items():
                _console.print(f"    → {file_path}")
            log_fn(f"Stage 4 OK: {len(files)} files exported")
            return generated_calendars, False, False
        except Exception as exc:
            _console.print(f"  [red]✗[/red] {exc}")
            log_fn(f"Stage 4 FAILED: {exc}")
            if strict:
                return False, True, False
            _console.print("  [yellow]⚠[/yellow] Fortsetter pga --non-strict")
            return False, False, True
    else:
        _console.print("[bold]Stage 4:[/bold] Hoppet over (gjenopptatt)")
        log_fn("Stage 4 skipped via --resume-from")
        return False, False, False


def _regenerate_calendar(
    args: "argparse.Namespace",
    log_fn: "Any",
) -> bool:
    """Regenerate calendars.html from scrape cache when Stage 4 did not produce it.

    Returns True if calendar generation failed (caller should set run_failed).
    """
    from ...pipeline.calendar_viewer import generate_html as generate_calendars

    _console.print("Genererer calendars.html...", end=" ")
    try:
        path = generate_calendars(work_dir=args.work_dir, export_dir=args.export_dir)
        _console.print(f"[green]✓[/green] {path}")
        log_fn(f"calendars.html generated: {path}")
        return False
    except Exception as exc:
        _console.print(f"[red]✗[/red] {exc}")
        log_fn(f"calendars.html FAILED: {exc}")
        return True
