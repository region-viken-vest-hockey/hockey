"""Operator resume/escalation/recovery-loop helpers and the top-level ``operator run`` command."""

from __future__ import annotations

import argparse
from typing import Any

from ._shared import _console
from .manifest import _DEFAULT_OPERATOR_OBJECTIVE, _warn_manifest_failure
from .operator_publish import _append_publish_outcome_to_run_log, _execute_operator_publish, _print_pages_result
from .run_command import _cmd_run

def _resolve_operator_resume_stage(state: "Any") -> "int | None":
    """Return the 1-based index of the earliest stage that needs (re)running.

    A stage needs running when it has no checkpoint yet, is not done, or was
    invalidated (stale) by an upstream change. Returns ``None`` when every
    stage is done and fresh — there is nothing pending for the operator to do.
    """
    from ...pipeline.state import StageName

    for stage in (StageName.CONFIG, StageName.SCRAPING, StageName.PLANNING, StageName.EXPORT):
        if not state.checkpoint_path(stage).exists():
            return stage.index
        if not state.is_done(stage) or state.is_stale(stage):
            return stage.index
    return None


def _raise_escalation_questions(work_dir: str) -> None:
    """Escalate every capability result that came back ``requires_human``.

    Best-effort and idempotent: raising the same question twice (same type,
    capability, and summary, in the same scope context) is a no-op, so this
    can safely run after every ``rvv-miniputt run`` invocation without ever
    re-asking something the human already answered, or duplicating a
    still-open question.

    Scoped to ``input_version`` (issue #12) when the run manifest has an
    input fingerprint: a blocked capability's cause is almost always a fact
    about *this* workbook's data (e.g. "0 turneringer mulig for U12"), so an
    answer recorded for one workbook must not be silently reused once the
    organizer uploads a different one — the old entry is marked stale
    instead, and the new workbook gets its own fresh escalation. Falls back
    to the durable ``workspace`` scope when no fingerprint is available
    (e.g. a legacy or synthesized manifest), matching pre-#12 behavior.
    """
    try:
        from ...pipeline.capability_result import CapabilityResult
        from ...pipeline.escalation import DecisionScope, from_capability_result, raise_question
        from ...pipeline.run_manifest import RunManifest

        manifest = RunManifest(work_dir).read()
        input_sha256 = (manifest.get("input_fingerprint") or {}).get("sha256")
        scope = DecisionScope.INPUT_VERSION.value if input_sha256 else DecisionScope.WORKSPACE.value
        scope_key = input_sha256 or ""

        for entry in manifest.get("capabilities") or []:
            if not entry.get("requires_human"):
                continue
            result = CapabilityResult.from_dict(entry)
            raise_question(work_dir, from_capability_result(result, scope=scope, scope_key=scope_key))
    except Exception as exc:
        _warn_manifest_failure(work_dir, "eskalere spørsmål til", exc)


def _print_operator_summary(work_dir: str) -> None:
    """Print the operator's final structured summary from the run manifest.

    Best-effort: the manifest is an operator-facing summary layered on top of
    the pipeline, so a failure to read it should never mask the pipeline's own
    exit code or console output.
    """
    try:
        from ...pipeline.run_manifest import RunManifest

        manifest = RunManifest(work_dir).read()
    except Exception:
        return

    outcome = str(manifest.get("final_outcome", "in_progress"))
    outcome_style = {"ok": "green", "warning": "yellow", "blocked": "yellow", "failed": "red"}.get(outcome, "white")
    icon_by_status = {"ok": "✓", "warning": "⚠", "blocked": "⛔", "failed": "✗"}
    style_by_status = {"ok": "green", "warning": "yellow", "blocked": "yellow", "failed": "red"}

    _console.print("\n[bold]Operator-sammendrag[/bold]")
    _console.print(f"  Mål:      {manifest.get('objective') or '-'}")
    _console.print(f"  Resultat: [{outcome_style}]{outcome.upper()}[/{outcome_style}]")

    action_log = manifest.get("action_log") or []
    if action_log:
        transition_icon = {"resolved": "✓", "retry": "↻", "escalate": "⛔", "no_progress_stop": "⏹"}
        transition_style = {"resolved": "green", "retry": "yellow", "escalate": "red", "no_progress_stop": "red"}
        _console.print(f"  Gjenopprettingsforsøk ({len(action_log)}):")
        for entry in action_log:
            transition = str(entry.get("transition", "?"))
            icon = transition_icon.get(transition, "?")
            style = transition_style.get(transition, "white")
            action_id = entry.get("action_id") or "(ingen handling)"
            _console.print(
                f"    [{style}]{icon}[/{style}] {entry.get('target', '?'):<12} {action_id:<20} {transition}"
            )

    capabilities = manifest.get("capabilities") or []
    if capabilities:
        _console.print("  Kapabiliteter:")
        for entry in capabilities:
            status = str(entry.get("status", "?"))
            icon = icon_by_status.get(status, "?")
            style = style_by_status.get(status, "white")
            name = str(entry.get("capability", "?"))
            _console.print(f"    [{style}]{icon}[/{style}] {name:<10} {entry.get('summary', '')}")
            for problem in entry.get("problems") or []:
                _console.print(f"        [dim]· {problem}[/dim]")
            if entry.get("requires_human"):
                for action in entry.get("suggested_actions") or []:
                    _console.print(f"        [cyan]→ {action}[/cyan]")

    unanswered = [q for q in manifest.get("pending_questions") or [] if not q.get("answered")]
    if unanswered:
        _console.print("\n  [bold yellow]Ubesvarte spørsmål:[/bold yellow]")
        for question in unanswered:
            _console.print(f"    [yellow]?[/yellow] ({question.get('type')}) {question.get('summary')}")
            if question.get("context"):
                _console.print(f"        [dim]Kontekst: {question['context']}[/dim]")
            if question.get("recommendation"):
                _console.print(f"        [cyan]Anbefaling: {question['recommendation']}[/cyan]")
            if question.get("impact"):
                _console.print(f"        [dim]Konsekvens: {question['impact']}[/dim]")
            for alt in question.get("alternatives") or []:
                _console.print(f"        [dim]· {alt}[/dim]")
            _console.print(
                f"        [dim]Svar med: rvv-miniputt operator answer {question.get('id')} \"<svar>\"[/dim]"
            )

    if outcome in ("blocked", "failed"):
        _console.print(
            "  [dim]Kjør 'rvv-miniputt status --json' for full detaljer, "
            "eller 'rvv-miniputt logs show' for siste kjøringslogg.[/dim]"
        )


def _run_recovery_loop(work_dir: str) -> "dict[str, Any] | None":
    """Best-effort observe-decide-act recovery pass (issue #11).

    Runs unconditionally at the top of ``operator run``: a no-op when
    Stage 2 hasn't produced a checkpoint yet or every source is already
    healthy. Never raises — a failure here should degrade to "the human
    sees the same blocked sources they would have anyway", not crash the
    operator entry point. A manifest persistence failure specifically is
    still surfaced as a visible warning (issue #14) rather than silently
    folded into "nothing to recover" — it's a materially different
    situation than the loop simply finding no unhealthy sources.
    """
    from ...pipeline.run_manifest import ManifestPersistenceError

    try:
        from ...pipeline.operator_loop import run_source_recovery_loop

        return run_source_recovery_loop(work_dir)
    except ManifestPersistenceError as exc:
        _warn_manifest_failure(work_dir, "registrere gjenopprettingshandlinger i", exc)
        return None
    except Exception:
        return None


def _cmd_operator_run(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator run`` — the goal-oriented AI operator entry point.

    A thin wrapper around ``rvv-miniputt run``, which already implements
    bounded retries, inter-stage judgment, and run-manifest bookkeeping: this
    resolves the active objective, runs a bounded observe-decide-act
    recovery pass over calendar source health (issue #11), auto-detects
    where to resume from unless the caller overrides it, skips work
    entirely when nothing is pending, and prints a final structured summary
    once the run completes. It does not duplicate any scheduling or
    recovery logic — the recovery pass only dispatches through the #10
    action registry, which itself calls the same stage/scraper code the
    portable CLI uses.
    """
    from ...pipeline.state import PipelineState

    args.objective = getattr(args, "objective", None) or _DEFAULT_OPERATOR_OBJECTIVE
    state = PipelineState(args.work_dir)

    recovery_summary = _run_recovery_loop(args.work_dir)
    recovered_any = bool(recovery_summary and recovery_summary.get("actions_taken"))

    explicit_resume = getattr(args, "resume_from", None)
    if getattr(args, "force", False):
        args.resume_from = "1"
    elif explicit_resume:
        args.resume_from = explicit_resume
    else:
        auto_stage = _resolve_operator_resume_stage(state)
        if recovered_any:
            # The recovery pass repaired the unified scrape cache, but only
            # an actual Stage 2 rerun rewrites the stage2_scraping.json
            # checkpoint to reflect that — force it back into the plan even
            # if auto-detection otherwise thought nothing was pending.
            auto_stage = 2 if auto_stage is None else min(auto_stage, 2)
        if auto_stage is None:
            _console.print("[bold]🏒 RVV Miniputt operator[/bold]\n")
            _console.print(f"[dim]Mål:[/dim] {args.objective}")
            _console.print(
                "[green]✓[/green] Alle stadier er allerede fullført og oppdaterte — ingenting å gjøre."
            )
            _console.print("[dim]Bruk --force for å kjøre pipelinen på nytt fra bunnen.[/dim]")
            _print_operator_summary(args.work_dir)
            return 0
        args.resume_from = str(auto_stage)

    rc = _cmd_run(args)
    _raise_escalation_questions(args.work_dir)
    _print_operator_summary(args.work_dir)

    if rc == 0 and getattr(args, "publish", False):
        publish_result = _execute_operator_publish(args)
        if publish_result is None:
            publish_rc = 1
        else:
            publish_rc = _print_pages_result(publish_result, as_json=getattr(args, "json", False))
            _append_publish_outcome_to_run_log(args.work_dir, state, publish_result)
        rc = rc if publish_rc == 0 else publish_rc

    return rc
