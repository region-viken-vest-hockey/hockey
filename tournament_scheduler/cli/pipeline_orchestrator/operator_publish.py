"""Operator publish/verify/rollback/publish-history subcommands."""

from __future__ import annotations

import argparse
from datetime import datetime
from typing import Any

from ...pipeline.run_log_paths import resolve_active_run_log_dir
from ._shared import _console
from .manifest import _warn_manifest_failure

def _execute_operator_publish(args: argparse.Namespace) -> "Any | None":
    """Build and execute the ``publish_pages`` action, recording it in the run manifest.

    Shared by ``_cmd_operator_publish`` (standalone ``operator publish``) and
    ``_cmd_operator_run`` (``operator run --publish``) so both paths build
    the exact same action from the exact same flags — see the module-level
    docstring note on ``op_run`` in ``cli/args.py`` (issue #32 follow-up):
    "run --publish" must behave identically to "run" followed by a separate
    "publish", not a distinct, weaker code path.

    Always invoked with ``approved=True`` at the :class:`ActionRegistry`
    level — running this command at all is the coarse consent to attempt an
    external-risk action (#10). That is deliberately *not* sufficient on
    its own to actually push to the Pages branch: ``_execute_publish_pages``
    additionally requires either ``--confirm-public`` on this exact
    invocation or a previously durable-answered approval for this exact
    bundle/target (issue #19).

    Returns the :class:`CapabilityResult` (whatever its status — the caller
    renders blocked/failed outcomes too), or ``None`` if the action registry
    itself raised before producing one (unknown action, missing approval, or
    persistence unavailable) — in which case this already printed the error.
    """
    from ...pipeline.operator_action import (
        DEFAULT_REGISTRY,
        ApprovalRequiredError,
        PersistenceUnavailableError,
        UnknownActionError,
    )
    from ...pipeline.run_manifest import ManifestPersistenceError, RunManifest

    action_kwargs: dict[str, Any] = {
        "work_dir": args.work_dir,
        "repo_dir": getattr(args, "repo_dir", ".") or ".",
        "branch": getattr(args, "branch", "gh-pages") or "gh-pages",
        "remote": getattr(args, "remote", "origin") or "origin",
        "push": getattr(args, "push", True),
        "confirm_public": getattr(args, "confirm_public", False),
        "dry_run": getattr(args, "dry_run", False),
        "verify": getattr(args, "verify", True),
    }
    # For `operator run --publish`, Stage 4 may write into a timestamped child of
    # args.export_dir (for example export/2026-07-29T1712).  Do not pass the
    # parent export root to the publish action; letting it resolve from the
    # Stage 4 checkpoint publishes the actual freshly exported bundle.  The
    # standalone `operator publish --export-dir ...` command still supports an
    # explicit override.
    if getattr(args, "operator_command", None) == "publish" and getattr(args, "export_dir", None):
        action_kwargs["export_dir"] = args.export_dir
    if getattr(args, "run_id", None):
        action_kwargs["run_id"] = args.run_id
    if getattr(args, "extra_public_files", None):
        from ...pipeline.pages_bundle import DEFAULT_ALLOWED_FILENAMES

        action_kwargs["allowed_filenames"] = DEFAULT_ALLOWED_FILENAMES | set(args.extra_public_files)
    if getattr(args, "allow_findings", None):
        action_kwargs["allow_findings"] = set(args.allow_findings)
    if getattr(args, "verify_max_attempts", None) is not None:
        action_kwargs["verify_max_attempts"] = args.verify_max_attempts
    if getattr(args, "verify_retry_delay_seconds", None) is not None:
        action_kwargs["verify_retry_delay_seconds"] = args.verify_retry_delay_seconds

    action = DEFAULT_REGISTRY.build("publish_pages", **action_kwargs)
    try:
        result = DEFAULT_REGISTRY.execute(action, approved=True)
    except (UnknownActionError, ApprovalRequiredError, PersistenceUnavailableError) as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return None

    try:
        RunManifest(args.work_dir).record_capability(result)
    except ManifestPersistenceError as exc:
        _warn_manifest_failure(args.work_dir, "registrere Pages-publisering i", exc)

    return result


def _append_publish_outcome_to_run_log(work_dir: str, state: "Any", result: "Any") -> None:
    """Append a run-triggered publish step's outcome to that run's own log file.

    ``_write_run_log`` (called inside ``_cmd_run``) already finishes and
    closes the pipeline run's log before ``operator run --publish`` gets a
    chance to publish — so without this, the publish step was completely
    invisible to the log the user checks after the fact, even though it ran
    in the very same invocation. "run --publish" is meant to be the same as
    "run" plus publishing, so its log should be the same run log with the
    publish outcome appended, not a separate silent action (issue #32
    follow-up).

    Best-effort: appended after the fact by locating the most recently
    written ``pipeline_run_*.log`` for this workspace, so a failure here
    (e.g. log dir missing) never affects the publish result itself.
    """
    try:
        log_dir = resolve_active_run_log_dir(state)
        candidates = sorted(
            log_dir.glob("pipeline_run_*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return
        log_path = candidates[0]
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n# Publish ({datetime.now().isoformat()})\n")
            handle.write(f"# Status: {result.status}\n")
            handle.write(f"{result.summary}\n")
            for artifact in result.artifacts:
                handle.write(f"  artifact: {artifact}\n")
            for problem in result.problems:
                handle.write(f"  problem: {problem}\n")
    except Exception:
        pass


def _cmd_operator_publish(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator publish`` — publish the exported plan to GitHub Pages (issue #17)."""
    result = _execute_operator_publish(args)
    if result is None:
        return 1
    return _print_pages_result(result, as_json=getattr(args, "json", False))


def _print_pages_result(result, *, as_json: bool) -> int:
    """Shared human/JSON rendering for verify/rollback results (issue #20)."""
    if as_json:
        import json as _json

        print(_json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0 if result.is_terminal_success else 1

    if result.status == "ok":
        _console.print(f"[green]✓[/green] {result.summary}")
    elif result.status == "warning":
        _console.print(f"[yellow]⚠[/yellow] {result.summary}")
    elif result.status == "blocked":
        _console.print(f"[yellow]?[/yellow] {result.summary}")
    else:
        _console.print(f"[red]✗[/red] {result.summary}")
    for artifact in result.artifacts:
        _console.print(f"    [cyan]{artifact}[/cyan]")
    for item in result.evidence:
        _console.print(f"    [dim]{item}[/dim]")
    for problem in result.problems:
        _console.print(f"    [dim]{problem}[/dim]")
    for action in result.suggested_actions:
        _console.print(f"    [dim]· {action}[/dim]")
    return 0 if result.is_terminal_success else 1


def _cmd_operator_verify(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator verify`` — re-check the last Pages publication (issue #20)."""
    from ...pipeline.operator_action import DEFAULT_REGISTRY, UnknownActionError

    action_kwargs: dict[str, Any] = {"work_dir": args.work_dir}
    if getattr(args, "max_attempts", None) is not None:
        action_kwargs["max_attempts"] = args.max_attempts
    if getattr(args, "retry_delay_seconds", None) is not None:
        action_kwargs["retry_delay_seconds"] = args.retry_delay_seconds

    action = DEFAULT_REGISTRY.build("verify_pages", **action_kwargs)
    try:
        result = DEFAULT_REGISTRY.execute(action, approved=True)
    except UnknownActionError as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1
    return _print_pages_result(result, as_json=getattr(args, "json", False))


def _cmd_operator_rollback(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator rollback <run-id>`` — restore '/latest/' (issue #20).

    Gated exactly like ``operator publish`` (issue #19): always invoked with
    ``approved=True`` at the registry level (running the command is the
    coarse consent), but the actual git write additionally requires
    ``--confirm-public`` on this invocation or a prior durable approval for
    this exact rollback target.
    """
    from ...pipeline.operator_action import (
        DEFAULT_REGISTRY,
        ApprovalRequiredError,
        PersistenceUnavailableError,
        UnknownActionError,
    )
    from ...pipeline.run_manifest import ManifestPersistenceError, RunManifest

    action_kwargs: dict[str, Any] = {
        "work_dir": args.work_dir,
        "run_id": args.run_id,
        "repo_dir": getattr(args, "repo_dir", ".") or ".",
        "branch": getattr(args, "branch", "gh-pages") or "gh-pages",
        "remote": getattr(args, "remote", "origin") or "origin",
        "push": getattr(args, "push", True),
        "confirm_public": getattr(args, "confirm_public", False),
    }

    action = DEFAULT_REGISTRY.build("rollback_pages", **action_kwargs)
    try:
        result = DEFAULT_REGISTRY.execute(action, approved=True)
    except (UnknownActionError, ApprovalRequiredError, PersistenceUnavailableError) as exc:
        _console.print(f"[red]✗[/red] {exc}")
        return 1

    try:
        RunManifest(args.work_dir).record_capability(result)
    except ManifestPersistenceError as exc:
        _warn_manifest_failure(args.work_dir, "registrere Pages-tilbakerulling i", exc)

    return _print_pages_result(result, as_json=getattr(args, "json", False))


def _cmd_operator_publish_history(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt operator publish-history`` — list Pages publish/rollback events (issue #20)."""
    from ...pipeline.pages_publish import list_publication_history

    history = list_publication_history(
        repo_dir=getattr(args, "repo_dir", ".") or ".", branch=getattr(args, "branch", "gh-pages") or "gh-pages"
    )

    if getattr(args, "json", False):
        import json as _json

        print(_json.dumps(history, indent=2, ensure_ascii=False))
        return 0

    if not history:
        _console.print("Ingen publiseringshistorikk funnet.")
        return 0

    _console.print(f"[bold]Publiseringshistorikk[/bold] ({len(history)})\n")
    for entry in history:
        marker = "[cyan]↩[/cyan]" if entry["kind"] == "rollback" else "[green]↑[/green]"
        label = "Tilbakerulling til" if entry["kind"] == "rollback" else "Publisert"
        _console.print(f"{marker} {label} kjøring [bold]{entry['run_id']}[/bold]")
        _console.print(f"    [dim]{entry['date']}  commit {entry['commit_sha'][:12]}[/dim]")
    return 0
