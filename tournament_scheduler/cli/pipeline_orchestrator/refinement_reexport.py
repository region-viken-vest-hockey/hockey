"""Re-run the refinement loop and re-export after a manual adjustment."""

from __future__ import annotations

import argparse
from typing import Any

from ._shared import _console
from .judgment import _compute_verdict_tone, _extract_plan_obj
from .refinement_loop import _MAX_REFINEMENT_ITERATIONS, _run_refinement_loop

def _run_refinement_and_reexport(
    args: "argparse.Namespace",
    plan: "dict[str, Any]",
    state: "Any",
    strict: bool,
    log_fn: "Any",
    resume_from: int,
) -> "tuple[dict[str, Any], bool, bool]":
    """Run the skill-driven refinement loop and optional Stage 4 re-export.

    Only runs when the plan verdict tone is 'rough'.  Failures here do not
    abort the pipeline.

    Returns ``(plan, generated_calendars, stage_failed)`` where *plan* is the
    (possibly refined) plan dict, *generated_calendars* is True when the
    re-export produced a calendars_html file, and *stage_failed* is always
    False (refinement is best-effort).
    """
    from ...pipeline.stage4_export import run as stage4_run

    generated_calendars = False
    try:
        plan_payload = _extract_plan_obj(plan)
        not_started = isinstance(plan, dict) and (
            plan.get("not_started") or (
                isinstance(plan_payload, dict) and plan_payload.get("placeholder") == "not_started"
            )
        )
        if not_started:
            log_fn("Post-Stage4 refinement skipped: no registered teams")
            return plan, generated_calendars, False

        initial_tone = _compute_verdict_tone(plan)
        log_fn(f"Post-Stage4 verdict tone: {initial_tone}")
        if initial_tone == "rough":
            _console.print(
                "\n[bold cyan]Plankvalitet: ROUGH — starter automatisk refinering...[/bold cyan]"
            )
            final_tone, refined_plan = _run_refinement_loop(
                plan, state, args, strict, log_fn
            )
            log_fn(f"Refinement loop complete: final tone={final_tone}")
            tone_label = {"strong": "SOLID", "mixed": "OK", "rough": "ROUGH"}.get(final_tone, final_tone.upper())
            if final_tone != "rough":
                _console.print(
                    f"  [green]✓[/green] Plankvalitet etter refinering: {tone_label} — re-eksporterer..."
                )
                # Re-run Stage 4 to export with the improved plan
                try:
                    export2 = stage4_run(
                        refined_plan,
                        state,
                        export_dir=args.export_dir,
                        strict=strict,
                        timestamped_export=getattr(args, "timestamped_export", True),
                    )
                    files2 = export2.get("output_files", {})
                    generated_calendars = "calendars_html" in files2
                    _console.print(f"  [green]✓[/green] Re-eksport: {len(files2)} fil(er)")
                    log_fn(f"Post-refinement Stage 4 re-export OK: {len(files2)} files")
                    plan = refined_plan
                except Exception as exc:
                    _console.print(f"  [yellow]⚠[/yellow] Re-eksport feilet: {exc}")
                    log_fn(f"Post-refinement Stage 4 re-export FAILED: {exc}")
            else:
                _console.print(
                    f"  [yellow]⚠[/yellow] Plankvalitet fremdeles ROUGH etter {_MAX_REFINEMENT_ITERATIONS} forsøk"
                )
        else:
            tone_label = {"strong": "SOLID", "mixed": "OK"}.get(initial_tone, initial_tone.upper())
            _console.print(f"\n  [dim]Plankvalitet: {tone_label} — ingen refinering nødvendig[/dim]")
    except Exception as exc:
        _console.print(f"  [yellow]⚠[/yellow] Refinering feilet uventet: {exc}")
        log_fn(f"Refinement loop unexpected error: {exc}")

    return plan, generated_calendars, False
