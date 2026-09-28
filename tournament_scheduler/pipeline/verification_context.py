"""Provenance-bound verification context for the reviewed Stage 4 handoff.

Stage 4 already verifies the exact candidate it serializes, using a normalized
``planning_problem`` built from that run's Stage 1 config, Stage 2 calendar
evidence, active operator waivers and any canonical-baseline locks.  That
problem is a run-scoped lifecycle fact -- the same inputs can be edited by a
later run without re-exporting, and ``.pipeline`` checkpoints survive across
invocations.

Promotion and the public publication preflight must therefore both verify the
exact reviewed candidate against the exact context that accepted the Stage 4
export.  Rebuilding a problem from whatever Stage 1/2 files happen to be
present at promotion/publication time (or falling back to the context-free
verifier) can silently apply a *different* ruleset to the same candidate,
producing false hard violations or false acceptance.  In particular a problem
rebuilt from mutable Stage 1/2 state loses canonical overlays -- host-confirmed
per-tournament ice-time overrides, durable participation withdrawals, canonical
roster renames -- and so reports an already accepted, source-backed booking as
a violation at publication time while ``season findings`` correctly reports
none.

This module owns:

* :func:`build_verification_context` -- the compact, versioned snapshot Stage 4
  stores alongside its export checkpoint;
* :func:`resolve_promotion_verification_context` -- the resolver promotion uses
  to prove that snapshot still describes the same run/candidate revision before
  re-verifying; and
* :func:`resolve_publish_verification_context` -- the same proof for the public
  publication preflight, so both consumers resolve one canonical contract.
"""

from __future__ import annotations

from typing import Any

from .fingerprints import stable_payload_sha256

VERIFICATION_CONTEXT_SCHEMA_VERSION = 1


class VerificationContextError(RuntimeError):
    """Raised when promotion/publication cannot prove a provenance-bound verification context.

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

    Stores the exact normalized problem (so promotion/publication need not
    rebuild it from mutable Stage 1/2 state) plus its fingerprint, the source
    run id and the candidate fingerprint the context was verified against.
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


def _resolve_reviewed_export_context(
    *,
    work_dir: str,
    candidate: dict[str, Any] | None,
    require_current_run: bool,
    error_prefix: str,
) -> dict[str, Any]:
    """Shared provenance proof for a reviewed Stage 4 export.

    Resolves the exact ``problem`` the export was accepted with plus the
    reviewed plan snapshot, after proving candidate/run/context/problem all
    belong to the same reviewed handoff. Both promotion and the publication
    preflight call this one implementation so neither can independently derive
    a weaker or divergent verification contract. Raises
    :class:`VerificationContextError`; it never degrades to a context-free
    verification.
    """
    from .run_manifest import RunManifest
    from .state import PipelineState, StageName, StageStatus

    def _fail(message: str) -> "VerificationContextError":
        return VerificationContextError(f"{error_prefix}{message}")

    state = PipelineState(work_dir)
    envelope = state.read_envelope(StageName.EXPORT)
    export_checkpoint = envelope.get("data") or {}
    if not isinstance(export_checkpoint, dict) or not export_checkpoint:
        raise _fail(
            "no reviewed Stage 4 export found in this workspace; verify and export "
            "the exact candidate first."
        )
    export_status = str(envelope.get("status") or "")
    if export_status != StageStatus.DONE.value or envelope.get("stale"):
        raise _fail(
            "the Stage 4 export is not a completed, non-stale reviewed handoff "
            f"(status={export_status or 'unknown'}, stale={bool(envelope.get('stale'))})."
        )

    context = export_checkpoint.get("verification_context")
    if not isinstance(context, dict):
        raise _fail(
            "the Stage 4 export carries no verification-context provenance; "
            "refusing to fall back to context-free verification."
        )
    raw_schema = context.get("schema_version")
    if raw_schema is None:
        raw_schema = 0
    try:
        context_schema = int(raw_schema)
    except (TypeError, ValueError):
        raise _fail(f"invalid verification-context schema_version={raw_schema!r}.") from None
    if context_schema != VERIFICATION_CONTEXT_SCHEMA_VERSION:
        raise _fail(f"unsupported verification-context schema_version={context_schema!r}.")

    reviewed_plan = export_checkpoint.get("reviewed_plan")
    resolved_candidate = candidate if isinstance(candidate, dict) else reviewed_plan
    if not isinstance(resolved_candidate, dict):
        raise _fail(
            "the Stage 4 export carries no reviewed final plan snapshot; "
            "re-export the reviewed candidate so the plan is not copied from stale "
            "Stage 3 operator state."
        )
    candidate_fingerprint = stable_payload_sha256(resolved_candidate.get("tournaments", []))
    export_fingerprint = export_checkpoint.get("export_fingerprint")
    if not export_fingerprint or export_fingerprint != candidate_fingerprint:
        raise _fail(
            "the selected candidate no longer matches the reviewed Stage 4 export "
            f"(candidate={candidate_fingerprint[:12]}, export={str(export_fingerprint)[:12]})."
        )
    if context.get("candidate_fingerprint") != export_fingerprint:
        raise _fail(
            "the stored verification context describes a different candidate than "
            "the reviewed Stage 4 export."
        )

    context_run_id = context.get("run_id")
    if require_current_run:
        current_run_id = RunManifest(work_dir).read().get("run_id")
        if not context_run_id or context_run_id != current_run_id:
            raise _fail(
                f"the verification context belongs to run {context_run_id!r}, not the "
                f"current run {current_run_id!r}."
            )

    export_verify_result = export_checkpoint.get("verify_result") or {}
    if not context.get("verify_ok") or not export_verify_result.get("ok", True):
        raise _fail(
            "the reviewed Stage 4 export was not hard-valid under its own "
            "verification context."
        )

    problem = context.get("problem")
    if problem is None:
        raise _fail(
            "the reviewed Stage 4 export has no normalized verification problem; "
            "refusing to substitute context-free verification."
        )
    if stable_payload_sha256(problem) != context.get("problem_fingerprint"):
        raise _fail("the verification problem does not match its recorded fingerprint.")

    if not isinstance(reviewed_plan, dict):
        raise _fail(
            "the Stage 4 export carries no reviewed final plan snapshot; "
            "re-export the reviewed candidate so the plan is not copied from stale "
            "Stage 3 operator state."
        )
    if stable_payload_sha256(reviewed_plan.get("tournaments", [])) != export_fingerprint:
        raise _fail("the reviewed Stage 4 plan snapshot does not match the export fingerprint.")

    # Public/source presentation context is optional (legacy handoffs predate
    # it) but, when present, it is fingerprint-verified so canonical export
    # never renders unverified source metadata.
    from .public_export_context import (
        PublicExportContextError,
        fingerprint_public_export_context,
        verify_public_export_context,
    )

    public_export_context = export_checkpoint.get("public_export_context")
    if not isinstance(public_export_context, dict) or not public_export_context:
        public_export_context = None
    public_export_context_fingerprint = None
    if public_export_context is not None:
        try:
            public_export_context_fingerprint = verify_public_export_context(
                public_export_context,
                expected_fingerprint=export_checkpoint.get("public_export_context_fingerprint")
                or fingerprint_public_export_context(public_export_context),
            )
        except PublicExportContextError as exc:
            raise _fail(str(exc)) from exc

    return {
        "problem": problem,
        "context": context,
        "reviewed_plan": reviewed_plan,
        "run_id": context_run_id,
        "candidate_fingerprint": candidate_fingerprint,
        "export_fingerprint": export_fingerprint,
        "export_dir": export_checkpoint.get("export_dir"),
        "problem_fingerprint": context.get("problem_fingerprint"),
        "canonical_season": export_checkpoint.get("canonical_season"),
        "canonical_revision": export_checkpoint.get("canonical_revision"),
        "public_export_context": public_export_context,
        "public_export_context_fingerprint": public_export_context_fingerprint,
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
    return _resolve_reviewed_export_context(
        work_dir=work_dir,
        candidate=candidate,
        require_current_run=True,
        error_prefix="Cannot promote: ",
    )


def resolve_publish_verification_context(
    *,
    work_dir: str,
) -> dict[str, Any]:
    """Resolve the reviewed export's provenance-bound context for publication.

    The public publication preflight must re-verify the exact plan and the
    exact problem the reviewed Stage 4 export was accepted with. This is the
    same proof promotion uses, applied to the export's own ``reviewed_plan``
    rather than a separately selected Stage 3 candidate, so publication cannot
    rebuild a different ruleset from mutable Stage 1/2 state and report an
    accepted, source-backed booking as a hard violation.

    Raises :class:`VerificationContextError` on missing, stale or inconsistent
    checkpoint/problem/revision provenance; it never degrades to a context-free
    verification.
    """
    return _resolve_reviewed_export_context(
        work_dir=work_dir,
        candidate=None,
        require_current_run=True,
        error_prefix="Cannot publish: ",
    )
