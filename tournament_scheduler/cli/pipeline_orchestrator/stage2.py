"""Stage 2 (scraping) checkpoint gate and runner."""

from __future__ import annotations

import argparse
from typing import Any

from rich.console import Console

from ._shared import _console
from .judgment import _judge_stage
from .run_log import _force_refresh_stage2_inputs

def _check_stage2_checkpoint(
    scraping_checkpoint: "dict[str, Any]",
    strict: bool,
    console: "Console",
    log_fn: "Any",
    *,
    harness_active: bool = False,
    interactive_reviewed: bool = False,
) -> bool:
    """Deterministic Stage 2 gate: inspect checkpoint fields directly.

    Reads ``sources[].event_count``, ``sources[].blocked``, and ``blocked[]``
    from *scraping_checkpoint* to decide whether the pipeline should proceed
    to Stage 3.

    When *harness_active* is True the gate auto-proceeds if at least one
    source returned events (threshold check), avoiding any interactive prompt.
    When *harness_active* is False and strict mode is on, the operator is
    prompted to confirm before proceeding despite warnings.

    Args:
        scraping_checkpoint: Stage 2 checkpoint dict written by stage2_scraping.run().
        strict: Whether the pipeline is running in strict mode.
        console: Rich ``Console`` for interactive output.
        log_fn: Callable that appends a message to the run log.
        harness_active: True when running headless under a harness (no LLM judge
            configured) — skips interactive prompts and uses threshold logic only.
        interactive_reviewed: True when the caller is ``rvv-miniputt run
            --interactive`` (issue #260 P1: "remove the Stage 2 hidden
            sufficiency shortcut"). In that mode this checkpoint's facts
            (including zero-events) are about to be surfaced through a
            :class:`~tournament_scheduler.application.decisions.DecisionContext`
            an interactive harness reads and decides on directly — this
            gate must not pre-empt that with its own separate zero-events
            hard-fail, or the canonical decision path never gets a say.
            Only the "nothing is watching at all" case (no headless judge,
            not interactive — a fully unattended run) keeps the deterministic
            zero-events hard-fail as an explicitly-named legacy safety net.

    Returns:
        ``True`` if the pipeline should proceed to Stage 3, ``False`` if it should
        halt.
    """
    sources: list[dict[str, Any]] = scraping_checkpoint.get("sources", [])
    blocked_names: list[str] = scraping_checkpoint.get("blocked", [])

    total_events = sum(s.get("event_count", 0) for s in sources if not s.get("blocked"))
    sources_with_events = sum(
        1 for s in sources if not s.get("blocked") and s.get("event_count", 0) > 0
    )
    blocked_count = len(blocked_names)

    log_fn(
        f"Stage 2 checkpoint check: {sources_with_events} sources with events, "
        f"{total_events} total events, {blocked_count} blocked"
    )

    # No sources configured — nothing to validate; let the pipeline proceed.
    if not sources:
        log_fn("Stage 2 gate: no sources configured — skipping threshold check")
        return True

    if sources_with_events == 0:
        console.print(
            "  [red]✗[/red] Stage 2-sjekkpunkt: ingen kilder returnerte hendelser"
        )
        log_fn("Stage 2 gate FAIL: zero sources with events")
        if not strict:
            console.print("  [yellow]⚠[/yellow] Fortsetter pga --non-strict")
            return True
        if interactive_reviewed:
            console.print(
                "  [yellow]⚠[/yellow] Interaktiv modus — avgjørelsen overlates til "
                "DecisionContext for dette steget, ikke en automatisk avbrytelse."
            )
            log_fn("Stage 2 gate: interactive_reviewed — deferring zero-events sufficiency to DecisionContext")
            return True
        return False

    if blocked_count > 0:
        console.print(
            f"  [yellow]⚠[/yellow] Stage 2-sjekkpunkt: {blocked_count} kilde(r) blokkert, "
            f"men {sources_with_events} kilde(r) returnerte hendelser"
        )
        log_fn(f"Stage 2 gate WARN: {blocked_count} blocked sources")

        if harness_active:
            # Harness mode: threshold met (at least one source with events) — auto-proceed
            log_fn("Stage 2 gate: harness active, threshold met — auto-proceeding")
            return True

        if not strict:
            console.print("  [yellow]⚠[/yellow] Fortsetter pga --non-strict")
            return True

        # strict + interactive: ask the operator
        try:
            answer = input("\n  Vil du fortsette til planlegging likevel? (j/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        log_fn(f"Operator confirmation answer (stage2 gate): {answer!r}")
        if answer in ("j", "y", "ja", "yes"):
            console.print("  [yellow]⚠[/yellow] Operatør har overstyrt advarsel — fortsetter")
            log_fn("Stage 2 gate WARN overridden by operator")
            return True
        return False

    # All sources OK
    log_fn("Stage 2 gate PASS: all sources returned events")
    return True


def _run_stage2(
    args: "argparse.Namespace",
    cfg: "dict[str, Any]",
    state: "Any",
    start: "Any",
    end: "Any",
    strict: bool,
    log_fn: "Any",
    resume_from: int,
) -> "tuple[dict[str, Any] | None, bool, bool]":
    """Run Stage 2 (scraping) or skip it when resuming from a later stage.

    Returns ``(scraping, abort, stage_failed)`` where *scraping* is the
    checkpoint dict, *abort* is True when the pipeline should stop (caller
    writes the run log and returns 1), and *stage_failed* is True when the
    stage failed in non-strict mode (pipeline continues but ``run_failed``
    should be set).
    """
    import os

    from ...llm_judge import get_judge_if_headless
    from ...pipeline.stage2_scraping import run as stage2_run
    from ...pipeline.state import StageName

    allow_missing_sources = getattr(args, "allow_missing_sources", False)
    if getattr(args, "manual_bookup_login", False):
        os.environ["RVV_BOOKUP_MANUAL_LOGIN"] = "1"
    timeout = getattr(args, "manual_bookup_login_timeout", None)
    if timeout is not None:
        os.environ["RVV_BOOKUP_MANUAL_LOGIN_TIMEOUT"] = str(timeout)

    if resume_from <= 2:
        _console.print("[bold]Stage 2:[/bold] Skraping...")
        if getattr(args, "manual_bookup_login", False):
            _console.print(
                "  [cyan]ℹ[/cyan] BookUp manuell innlogging er aktiv — "
                "fullfør Vipps/SMS i nettleseren når den åpnes."
            )
        if getattr(args, "force_refresh", False):
            try:
                _force_refresh_stage2_inputs(args.work_dir)
                _console.print("  [green]✓[/green] Cache tvangsoppdatert før Stage 2")
                log_fn("Stage 2 inputs force-refreshed")
            except Exception as exc:
                _console.print(f"  [yellow]⚠[/yellow] Cache-refresh feilet: {exc}")
                log_fn(f"Stage 2 force-refresh warning: {exc}")
        try:
            from time import perf_counter

            from ...pipeline.run_manifest import RunManifest

            _started = perf_counter()
            scraping = stage2_run(
                cfg,
                state,
                start,
                end,
                strict=strict,
                allow_missing_sources=allow_missing_sources,
            )
            try:
                RunManifest(state.work_dir).record_timing("stage2_seconds", perf_counter() - _started)
            except Exception as exc:
                log_fn(f"Stage 2: could not record timing: {exc}")
            n = len(scraping.get("sources", []))
            blocked = scraping.get("blocked", [])
            if scraping.get("skipped"):
                _console.print(f"  [green]✓[/green] Skraping hoppet over — {scraping.get('skip_reason', 'ingen lag registrert')}")
                log_fn(f"Stage 2 skipped: {scraping.get('skip_reason', 'no registered teams')}")
            else:
                _console.print(f"  [green]✓[/green] {n} kilder skannet, {len(blocked)} blokkert")
                log_fn(f"Stage 2 OK: {n} sources scanned, {len(blocked)} blocked")
            if blocked:
                for blocked_name in blocked:
                    _console.print(f"    [yellow]⚠[/yellow] {blocked_name}")
                    log_fn(f"  Blocked: {blocked_name}")
                if scraping.get("warning"):
                    _console.print(f"  [dim]{scraping['warning']}[/dim]")
                if allow_missing_sources:
                    _console.print("  [green]✓[/green] Delvise resultater er lagret og pipeline fortsetter med godkjente mangler.")
                else:
                    _console.print("  [dim]Delvise resultater er lagret; kjør [bold]rvv-miniputt run --allow-missing-sources[/bold] for å fortsette med slike mangler neste gang.[/dim]")
            stage2_summary = {
                "sources_scanned": n,
                "blocked": blocked,
                "source_details": scraping.get("sources", []),
            }
            if not _judge_stage(2, stage2_summary, state, log_fn, stage_name=StageName.SCRAPING):
                return None, True, False
            # Deterministic checkpoint inspection — runs regardless of judge backend.
            try:
                _harness_active = get_judge_if_headless() is None
            except ValueError:
                _harness_active = True  # no backend configured — treat as harness
            if not _check_stage2_checkpoint(
                scraping,
                strict,
                _console,
                log_fn,
                harness_active=_harness_active,
                interactive_reviewed=bool(getattr(args, "interactive", False)),
            ):
                return None, True, False
        except Exception as exc:
            _console.print(f"  [red]✗[/red] {exc}")
            log_fn(f"Stage 2 FAILED: {exc}")
            scraping = state.read_stage(StageName.SCRAPING) or {"sources": [], "blocked": []}
            if scraping.get("warning"):
                _console.print(f"  [dim]{scraping['warning']}[/dim]")
            if strict:
                return None, True, False
            _console.print("  [yellow]⚠[/yellow] Fortsetter pga --non-strict")
            return scraping, False, True
    else:
        scraping = state.read_stage(StageName.SCRAPING)
        if not scraping:
            _console.print("[red]✗[/red] Kan ikke gjenoppta: Stage 2-checkpoint mangler.")
            return None, True, False
        _console.print("[bold]Stage 2:[/bold] Hoppet over (gjenopptatt)")
        log_fn("Stage 2 skipped via --resume-from")

    return scraping, False, False
