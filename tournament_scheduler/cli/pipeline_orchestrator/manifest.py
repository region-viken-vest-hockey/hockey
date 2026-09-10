"""Best-effort run-manifest persistence helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ._shared import _console

# Work dirs (as absolute-path strings) where a manifest persistence failure
# was observed during this process — i.e. this one CLI invocation. Since
# each ``rvv-miniputt``/``rvv-miniputt operator`` invocation is a fresh
# Python process, this never leaks state across runs; it exists purely so
# the final outcome computed near the end of ``_cmd_run`` (far away from
# where any individual manifest call happened) can tell whether persistence
# degraded at any point during *this* run (issue #14).
_MANIFEST_DEGRADED_WORK_DIRS: set[str] = set()

_DEFAULT_OPERATOR_OBJECTIVE = "Produce the best trustworthy season plan from the current workbook."


def _manifest_degraded(work_dir: str) -> bool:
    """Whether a manifest persistence failure was observed for *work_dir*
    during this process — see ``_MANIFEST_DEGRADED_WORK_DIRS``."""
    return str(Path(work_dir).resolve()) in _MANIFEST_DEGRADED_WORK_DIRS


def _warn_manifest_failure(work_dir: str, operation: str, exc: Exception) -> None:
    """Surface a manifest persistence failure instead of swallowing it silently.

    Issue #14: manifest reads/writes used to fail via a bare ``except
    Exception: pass``, so an AI operator (or a human) could see incomplete
    or stale control state with no indication anything went wrong. This
    prints a visible warning, appends a line to a manifest-specific warning
    log next to the workspace's normal per-run logs, and marks the work
    dir degraded for this process so the final run outcome can be capped at
    ``warning`` even if the scheduling pipeline itself completed cleanly —
    losing operator control-state must never look identical to a clean run.
    """
    message = f"Kunne ikke {operation} run manifest: {exc}"
    _console.print(f"[yellow]⚠[/yellow] {message}")
    _MANIFEST_DEGRADED_WORK_DIRS.add(str(Path(work_dir).resolve()))
    try:
        log_dir = Path(work_dir) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "manifest_warnings.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now().isoformat()} operation={operation} error={exc}\n")
    except OSError:
        pass  # the console warning above already happened; this is a bonus, not the primary signal


def _manifest_record(work_dir: str, capability: str, status: str, summary: str, **kwargs: Any) -> None:
    """Best-effort append to the run manifest. Never raises — the manifest is
    an operator-facing summary layered on top of the pipeline, not a
    dependency of it — but a failure is surfaced, not swallowed (issue #14)."""
    try:
        from ...pipeline.capability_result import CapabilityResult
        from ...pipeline.run_manifest import RunManifest

        RunManifest(work_dir).record_capability(
            CapabilityResult(status=status, summary=summary, capability=capability, **kwargs)
        )
    except Exception as exc:
        _warn_manifest_failure(work_dir, f"registrere kapabilitet '{capability}' i", exc)


def _manifest_set_active(work_dir: str, capability: str) -> None:
    """Best-effort mark *capability* active before it starts executing (issue #15).

    Counterpart to ``_manifest_record`` below, called before rather than
    after a stage runs, so a crash mid-stage still leaves a durable record
    of what was in progress when execution stopped instead of showing
    whatever the previous stage last recorded.
    """
    try:
        from ...pipeline.run_manifest import RunManifest

        RunManifest(work_dir).set_current_capability(capability)
    except Exception as exc:
        _warn_manifest_failure(work_dir, f"markere kapabilitet '{capability}' som aktiv i", exc)


def _manifest_start_run(work_dir: str, input_path: str, objective: str | None = None) -> None:
    """Best-effort start of a new run manifest at the top of ``rvv-miniputt run``."""
    try:
        from ...pipeline.fingerprints import file_sha256
        from ...pipeline.run_manifest import RunManifest

        input_fingerprint: dict[str, Any] = {"path": input_path}
        try:
            input_fingerprint["sha256"] = file_sha256(input_path)
        except OSError:
            pass
        RunManifest(work_dir).start_run(
            objective or _DEFAULT_OPERATOR_OBJECTIVE,
            input_fingerprint=input_fingerprint,
        )
    except Exception as exc:
        _warn_manifest_failure(work_dir, "starte", exc)


def _manifest_finalize(work_dir: str, outcome: str) -> None:
    """Best-effort finalization of the run manifest at the end of a run.

    When persistence degraded at any point during this run (issue #14), the
    outcome is capped at ``warning`` even if *outcome* would otherwise have
    been ``ok`` — a clean scheduling result whose control-state didn't
    reliably persist is not the same thing as a clean run.
    """
    if outcome == "ok" and _manifest_degraded(work_dir):
        outcome = "warning"
    try:
        from ...pipeline.run_manifest import RunManifest

        RunManifest(work_dir).finalize(outcome)
    except Exception as exc:
        _warn_manifest_failure(work_dir, "avslutte", exc)
