"""Provenance-bound verification context for the reviewed Stage 4 handoff.

Stage 4 already verifies the exact candidate it serializes, using a normalized
``planning_problem`` built from that run's Stage 1 config, Stage 2 calendar
evidence, active operator waivers and any canonical-baseline locks.  That
problem is a run-scoped lifecycle fact -- the same inputs can be edited by a
later run without re-exporting, and ``.pipeline`` checkpoints survive across
invocations.

Promotion must therefore verify the exact reviewed candidate against the exact
context that accepted the Stage 4 export.  Rebuilding a problem from whatever
Stage 1/2 files happen to be present at promotion time (or falling back to the
context-free verifier) can silently apply a *different* ruleset to the same
candidate, producing false hard violations or false acceptance.

This module owns:

* :func:`build_verification_context` -- the compact, versioned snapshot Stage 4
  stores alongside its export checkpoint; and
* :func:`resolve_promotion_verification_context` -- the resolver promotion uses
  to prove that snapshot still describes the same run/candidate revision before
  re-verifying, refusing explicitly when it cannot.
"""

from __future__ import annotations

from typing import Any

from .fingerprints import stable_payload_sha256

VERIFICATION_CONTEXT_SCHEMA_VERSION = 1


class VerificationContextError(RuntimeError):
    """Raised when promotion cannot prove a provenance-bound verification context.

    This is deliberately *not* degraded to ``problem=None``: a missing,
    stale or mismatched context is a lifecycle/provenance failure, not a
    different (context-free) ruleset.
    """


def build_verification_context(
    *,
    run_id: str | None,
    candidate: dict[str, Any],
    problem: dict[str, Any] | None,
    verify_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return the versioned verification-context snapshot for a Stage 4 export.

    Stores the exact normalized problem (so promotion need not rebuild it from
    mutable Stage 1/2 state) plus its fingerprint, the source run id and the
    candidate fingerprint the context was verified against.
    """
    candidate_fingerprint = stable_payload_sha256(candidate.get("tournaments", []))
    return {
        "schema_version": VERIFICATION_CONTEXT_SCHEMA_VERSION,
        "run_id": run_id,
        "candidate_fingerprint": candidate_fingerprint,
        "problem": problem,
        "problem_fingerprint": stable_payload_sha256(problem) if problem is not None else None,
        "verify_ok": bool((verify_result or {}).get("ok", True)),
    }


def resolve_promotion_verification_context(
    *,
    work_dir: str,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Resolve the provenance-bound verification context for promoted *candidate*.

    Reads the Stage 4 export checkpoint and proves, deterministically, that the
    candidate, source run and verification context all belong to the same
    reviewed handoff.  Returns a dict with the normalized ``problem`` to verify
    against plus the provenance to persist in canonical state.

    Raises :class:`VerificationContextError` (never degrades silently) when:

    * there is no completed, non-stale Stage 4 export;
    * the export has no verification-context provenance (older format);
    * the selected candidate fingerprint differs from the reviewed export;
    * the context's run id differs from the current run manifest's run id;
    * Stage 4's own final verification was not hard-valid; or
    * the context's normalized problem is missing or fingerprint-mismatched.
    """
    from .run_manifest import RunManifest
    from .state import PipelineState, StageName, StageStatus

    state = PipelineState(work_dir)
    envelope = state.read_envelope(StageName.EXPORT)
    export_checkpoint = envelope.get("data") or {}
    if not isinstance(export_checkpoint, dict) or not export_checkpoint:
        raise VerificationContextError(
            "Cannot promote: no reviewed Stage 4 export found in this workspace; "
            "verify and export the exact candidate before promoting."
        )
    export_status = str(envelope.get("status") or "")
    if export_status != StageStatus.DONE.value or envelope.get("stale"):
        raise VerificationContextError(
            "Cannot promote: the Stage 4 export is not a completed, non-stale reviewed handoff "
            f"(status={export_status or 'unknown'}, stale={bool(envelope.get('stale'))})."
        )

    context = export_checkpoint.get("verification_context")
    if not isinstance(context, dict):
        raise VerificationContextError(
            "Cannot promote: the Stage 4 export carries no verification-context provenance; "
            "refusing to fall back to context-free verification."
        )
    context_schema = int(context.get("schema_version", 0) or 0)
    if context_schema != VERIFICATION_CONTEXT_SCHEMA_VERSION:
        raise VerificationContextError(
            f"Cannot promote: unsupported verification-context schema_version={context_schema!r}."
        )

    candidate_fingerprint = stable_payload_sha256(candidate.get("tournaments", []))
    export_fingerprint = export_checkpoint.get("export_fingerprint")
    if not export_fingerprint or export_fingerprint != candidate_fingerprint:
        raise VerificationContextError(
            "Cannot promote: the selected candidate no longer matches the reviewed Stage 4 export "
            f"(candidate={candidate_fingerprint[:12]}, export={str(export_fingerprint)[:12]})."
        )
    if context.get("candidate_fingerprint") != export_fingerprint:
        raise VerificationContextError(
            "Cannot promote: the stored verification context describes a different candidate than "
            "the reviewed Stage 4 export."
        )

    context_run_id = context.get("run_id")
    current_run_id = RunManifest(work_dir).read().get("run_id")
    if not context_run_id or context_run_id != current_run_id:
        raise VerificationContextError(
            "Cannot promote: the verification context belongs to run "
            f"{context_run_id!r}, not the current run {current_run_id!r}."
        )

    export_verify_result = export_checkpoint.get("verify_result") or {}
    if not context.get("verify_ok") or not export_verify_result.get("ok", True):
        raise VerificationContextError(
            "Cannot promote: the reviewed Stage 4 export was not hard-valid under its own "
            "verification context."
        )

    problem = context.get("problem")
    if problem is None:
        raise VerificationContextError(
            "Cannot promote: the reviewed Stage 4 export has no normalized verification problem; "
            "refusing to substitute context-free verification."
        )
    if stable_payload_sha256(problem) != context.get("problem_fingerprint"):
        raise VerificationContextError(
            "Cannot promote: the verification problem does not match its recorded fingerprint."
        )

    reviewed_plan = export_checkpoint.get("reviewed_plan")
    if not isinstance(reviewed_plan, dict):
        raise VerificationContextError(
            "Cannot promote: the Stage 4 export carries no reviewed final plan snapshot; "
            "re-export the reviewed candidate so promotion cannot copy stale Stage 3 operator state."
        )
    if stable_payload_sha256(reviewed_plan.get("tournaments", [])) != export_fingerprint:
        raise VerificationContextError(
            "Cannot promote: the reviewed Stage 4 plan snapshot does not match the export fingerprint."
        )

    return {
        "problem": problem,
        "context": context,
        "reviewed_plan": reviewed_plan,
        "run_id": context_run_id,
        "candidate_fingerprint": candidate_fingerprint,
        "export_fingerprint": export_fingerprint,
        "export_dir": export_checkpoint.get("export_dir"),
        "problem_fingerprint": context.get("problem_fingerprint"),
    }
