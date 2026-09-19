"""Semantic safety-net audit executors and publish-time gate (issue #325).

Split out of :mod:`operator_action` to stay within the repo's
300-line-per-file guideline (``scripts/check_file_length.py``) — these
functions are registered into / called from that module's
:class:`~operator_action.ActionRegistry` and ``_execute_publish_pages``, not
used standalone.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .capability_result import CapabilityResult
    from .operator_action import ActionRegistry


def register_audit_actions(registry: "ActionRegistry") -> None:
    """Register the ``get_audit_context``/``submit_audit_result`` actions."""
    from .operator_action import OperatorAction, RiskLevel

    registry.register(
        OperatorAction(
            "get_audit_context",
            "Assemble the read-only evidence inventory for the semantic safety-net audit.",
            "llm_audit",
            risk_level=RiskLevel.SAFE.value,
        ),
        execute_get_audit_context,
    )
    registry.register(
        OperatorAction(
            "get_audit_evidence",
            "Return detailed semantic-audit evidence matching a bounded selector (item/tournament/club/category).",
            "llm_audit",
            risk_level=RiskLevel.SAFE.value,
        ),
        execute_get_audit_evidence,
    )
    registry.register(
        OperatorAction(
            "submit_audit_result",
            "Persist a structured semantic safety-net audit verdict.",
            "llm_audit",
            risk_level=RiskLevel.REVERSIBLE.value,
        ),
        execute_submit_audit_result,
    )


def execute_get_audit_context(
    *, work_dir: str, workflow: dict[str, Any] | None = None
) -> "CapabilityResult":
    """Assemble the read-only evidence inventory for the semantic safety-net
    audit — no LLM call, no fresh scraping, just facts the pipeline already
    produced. ``workflow`` is the caller-supplied audit/convergence lifecycle
    projection; this module injects it rather than reading the application/session
    layer so the dependency direction is preserved."""
    from .audit_context import build_audit_context
    from .capability_result import CapabilityResult

    context = build_audit_context(work_dir=work_dir, workflow=workflow)
    if not context.get("export_fingerprint"):
        return CapabilityResult.failed(
            "Ingen Stage 4-eksport funnet — kjør eksport før revisjon.", capability="llm_audit"
        )
    materialize_audit_context(work_dir, context)
    return CapabilityResult.ok(
        "Revisjonskontekst satt sammen.",
        capability="llm_audit",
        evidence=[json.dumps(context, ensure_ascii=False, default=str)],
    )


def materialize_audit_context(work_dir: str, context: dict[str, Any]) -> None:
    """Best-effort immutable snapshot of the exact context the auditor saw.

    The committed ``audit_context.json`` is the analysis record that lets a
    reviewer reconstruct evidence -> audit input -> audit judgment from one
    export directory. A provenance mismatch is a real error, but a missing
    export directory (e.g. an audit against a workspace without a
    materialized export) simply has nothing to attach to.
    """
    from .audit_export_artifact import materialize_audit_context as _materialize

    try:
        _materialize(work_dir, context)
    except (OSError, ValueError):
        return


def _ensure_context_fingerprint(work_dir: str, *, export_fingerprint: str) -> str | None:
    """Resolve (materializing when needed) the context fingerprint for one export.

    A harness that read ``audit-context`` already wrote the artifact. When a
    caller submits without reading it first, the context is rebuilt
    deterministically from the same persisted Stage 4 artifacts and written
    now, so every stored verdict still carries the fingerprint of the exact
    context bound to its export.
    """
    from .audit_export_artifact import load_materialized_audit_context

    try:
        artifact = load_materialized_audit_context(work_dir, export_fingerprint=export_fingerprint)
        if artifact is None:
            from .audit_context import build_audit_context

            context = build_audit_context(work_dir=work_dir)
            if str(context.get("export_fingerprint") or "") != export_fingerprint:
                return None
            materialize_audit_context(work_dir, context)
            artifact = load_materialized_audit_context(
                work_dir, export_fingerprint=export_fingerprint
            )
    except (OSError, ValueError):
        return None
    fingerprint = artifact.get("context_fingerprint") if isinstance(artifact, dict) else None
    return str(fingerprint) if fingerprint else None


def execute_get_audit_evidence(
    *,
    work_dir: str,
    item: int | None = None,
    tournament: str | None = None,
    club: str | None = None,
    age_group: str | None = None,
    category: str | None = None,
    unresolved: bool = False,
    limit: int | None = None,
) -> "CapabilityResult":
    """Return the detailed audit evidence matching one or more selectors.

    The repository owns the query: harness adapters never parse the raw
    artifacts themselves. Results remain bound to the same run/export
    fingerprint as the bounded overview they were advertised in.
    """
    from .audit_context import build_audit_evidence
    from .capability_result import CapabilityResult

    result = build_audit_evidence(
        work_dir=work_dir,
        item=item,
        tournament=tournament,
        club=club,
        age_group=age_group,
        category=category,
        unresolved=unresolved,
        limit=limit,
    )
    if not result.get("export_fingerprint"):
        return CapabilityResult.failed(
            "Ingen Stage 4-eksport funnet — kjør eksport før revisjon.", capability="llm_audit"
        )
    return CapabilityResult.ok(
        "Revisjonsbevis hentet.",
        capability="llm_audit",
        evidence=[json.dumps(result, ensure_ascii=False, default=str)],
    )


def execute_submit_audit_result(*, work_dir: str, result: dict[str, Any]) -> "CapabilityResult":
    """Validate and persist a structured audit verdict.

    Rejected outright when *result*'s ``export_fingerprint`` does not match
    the export currently on disk — a submission for a stale/different export
    must never be recorded as though it covered the current one.
    """
    from .audit_result import current_export_fingerprint, with_resolved_audit_id, write_audit_result
    from .capability_result import CapabilityResult

    current_fp = current_export_fingerprint(work_dir)
    submitted_fp = result.get("export_fingerprint")
    if not current_fp or submitted_fp != current_fp:
        return CapabilityResult.blocked(
            "Innsendt revisjonsresultat matcher ikke gjeldende eksport-fingeravtrykk.",
            capability="llm_audit",
            problems=[f"submitted={submitted_fp!r} current={current_fp!r}"],
            suggested_actions=["Hent ny kontekst med 'operator audit-context' og send inn på nytt."],
        )

    result = dict(result)
    context_fp = _ensure_context_fingerprint(work_dir, export_fingerprint=current_fp)
    if context_fp:
        result["audit_context_fingerprint"] = context_fp
    result = with_resolved_audit_id(result)
    errors = write_audit_result(work_dir, result)
    if errors:
        return CapabilityResult.failed(
            "Revisjonsresultat feilet skjemavalidering.", capability="llm_audit", problems=errors
        )
    status = result.get("status")
    return CapabilityResult.ok(
        f"Revisjonsresultat lagret (status={status}).",
        capability="llm_audit",
        evidence=[f"audit_id={result.get('audit_id')}", f"export_fingerprint={submitted_fp}"],
    )


def current_hard_violations(work_dir: str) -> list[str]:
    """Independently re-verify the currently selected plan against the final
    hard verifier, mirroring
    ``cli.pipeline_orchestrator.hard_verification_gate._baseline_hard_violations_for_plan``
    without importing across the pipeline/cli layering boundary — a harness
    ``PASS`` audit result must never be able to override an actual
    deterministic hard failure."""
    from .state import PipelineState, StageName

    planning_checkpoint = PipelineState(work_dir).read_stage(StageName.PLANNING)
    plan = planning_checkpoint.get("plan") if isinstance(planning_checkpoint, dict) else None
    if not isinstance(plan, dict):
        return []
    try:
        from ..final_verification import verify_final_candidate
        from ..planning_contract import extract_candidate

        candidate = extract_candidate(plan)
    except (ValueError, KeyError):
        return []
    problem = None
    try:
        from .stage1_config import load_effective_config
        from .stage4_export_verification import _build_export_verification_problem

        state = PipelineState(work_dir)
        effective_config = load_effective_config(state) or {}
        problem = _build_export_verification_problem(effective_config, state)
    except Exception:
        problem = None
    try:
        result = verify_final_candidate(candidate, problem)
    except Exception:
        return []
    if result.get("ok", True):
        return []
    return [f"{v.get('code')}: {v.get('message')}" for v in (result.get("violations") or [])]


def apply_publish_audit_gate(
    *,
    work_dir: str,
    bundle_result: "CapabilityResult",
    with_collision_warning: "Callable[[CapabilityResult], CapabilityResult]",
) -> "CapabilityResult | None":
    """Apply deterministic verification, audit freshness and review gates."""
    from .audit_result import audit_is_fresh, is_blocking_status
    from .capability_result import CapabilityResult

    hard_violations = current_hard_violations(work_dir)
    if hard_violations:
        return with_collision_warning(CapabilityResult.blocked(
            "Publisering blokkert: planen feiler deterministisk hard verifisering.",
            capability="pages_publish",
            problems=hard_violations,
            artifacts=list(bundle_result.artifacts),
        ))

    audit_fresh, audit_result_payload = audit_is_fresh(work_dir)
    audit_status = (audit_result_payload or {}).get("status") if audit_fresh else None
    if not audit_fresh or is_blocking_status(audit_status):
        reason = (
            "Ingen revisjon funnet for denne eksporten."
            if audit_result_payload is None
            else "Revisjonsresultatet er foreldet (matcher ikke gjeldende eksport/kjøring)."
            if not audit_fresh
            else f"Semantisk revisjon feilet eller er ufullstendig (status={audit_status})."
        )
        return with_collision_warning(CapabilityResult.blocked(
            f"Semantisk revisjon mangler, er foreldet, eller feilet — publisering krever et "
            f"gyldig revisjonsresultat. {reason}",
            capability="pages_publish",
            problems=[reason],
            suggested_actions=[
                "Kjør 'rvv-miniputt operator audit-context' og 'operator audit-submit' "
                "(interaktiv harness), eller 'operator audit-run --backend <navn>' (headless).",
            ],
            artifacts=list(bundle_result.artifacts),
        ))

    if audit_status != "REVIEW_REQUIRED":
        return None

    from .audit_review_gate import apply_review_required_gate

    return apply_review_required_gate(
        work_dir=work_dir,
        audit_result_payload=audit_result_payload or {},
        bundle_result=bundle_result,
        with_collision_warning=with_collision_warning,
    )
