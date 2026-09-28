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


def _resolve_publication_scope(
    *, work_dir: str, repo_dir: str
) -> dict[str, Any] | None:
    """Resolve the tournament-scoped publication eligibility for the export.

    Returns ``None`` when the provenance-bound reviewed export cannot be
    resolved (the caller then preserves the full semantic-audit gate). The
    assessment itself never raises into the gate: a resolution or evidence
    failure is reported as ``NOT_CHECKABLE`` so publication falls back to the
    existing audit semantics rather than being silently allowed.
    """
    import os
    from pathlib import Path

    from .publication_scope import resolve_publication_scope
    from .verification_context import resolve_publish_verification_context

    try:
        bound = resolve_publish_verification_context(work_dir=work_dir)
    except Exception:  # noqa: BLE001 - preserve existing gate semantics
        return None

    season_root = os.environ.get("RVV_CANONICAL_SEASON_ROOT") or str(
        Path(repo_dir) / "season"
    )
    return resolve_publication_scope(
        reviewed_plan=bound.get("reviewed_plan") or {},
        problem=bound.get("problem"),
        season=bound.get("canonical_season"),
        season_root=season_root,
        export_fingerprint=bound.get("export_fingerprint"),
        canonical_revision=bound.get("canonical_revision"),
    )


def _blocked_for_publication_scope(
    *,
    scope: dict[str, Any],
    bundle_result: "CapabilityResult",
    with_collision_warning: "Callable[[CapabilityResult], CapabilityResult]",
) -> "CapabilityResult | None":
    """Block a HELD/BLOCKED tournament-scoped publication result.

    An ``ELIGIBLE`` or ``NOT_CHECKABLE`` assessment returns ``None`` so the
    caller applies the existing semantic-audit gate instead.
    """
    from .capability_result import CapabilityResult
    from .publication_scope import (
        STATUS_BLOCKED,
        STATUS_ELIGIBLE,
        STATUS_HELD,
        STATUS_NOT_CHECKABLE,
    )

    status = scope.get("status")
    if status in (STATUS_ELIGIBLE, STATUS_NOT_CHECKABLE):
        return None
    if status not in (STATUS_BLOCKED, STATUS_HELD):
        return None

    reasons = list(scope.get("reasons") or [])
    problems = [str(reason.get("message") or reason.get("code")) for reason in reasons]
    held_ids = sorted({str(entry.get("tournament_id") or "") for entry in scope.get("held") or []})
    booked_ids = sorted(str(item) for item in scope.get("booked_tournament_ids") or [])
    proposed_ids = sorted(str(item) for item in scope.get("proposed_tournament_ids") or [])
    evidence = [
        f"publication_scope_status={status}",
        f"publication_scope_export_fingerprint={scope.get('export_fingerprint')}",
        f"publication_scope_canonical_revision={scope.get('canonical_revision')}",
        f"publication_scope_held_tournament_ids={','.join(held_ids)}",
        f"publication_scope_booked_tournament_ids={','.join(booked_ids)}",
        f"publication_scope_proposed_tournament_ids={','.join(proposed_ids)}",
    ]
    if status == STATUS_BLOCKED:
        summary = (
            "Publisering blokkert av global publiseringssikkerhet: publiseringsomfanget "
            "kan ikke forsvares for gjeldende kanoniske revisjon."
        )
    else:
        summary = (
            "Publisering holdt tilbake: en eller flere turneringer har bookingbevis "
            "som motsier den offentlige plasseringen, eller en tidligere publisert "
            "oppføring forsvant uten kanonisk avlysning."
        )
    return with_collision_warning(
        CapabilityResult.blocked(
            summary,
            capability="pages_publish",
            problems=problems,
            evidence=evidence,
            suggested_actions=[
                "Løs den navngitte turneringen: flytt/planlegg den på nytt eller korriger "
                "bookingbeviset, og eksporter på nytt. En plassering uten bookingbevis "
                "publiseres som forslag/vil bekreftes, ikke som booket.",
            ],
            artifacts=list(bundle_result.artifacts),
        )
    )


def apply_publish_audit_gate(
    *,
    work_dir: str,
    bundle_result: "CapabilityResult",
    with_collision_warning: "Callable[[CapabilityResult], CapabilityResult]",
    repo_dir: str = ".",
) -> "CapabilityResult | None":
    """Apply deterministic verification, audit freshness and review gates.

    The hard-verification step is delegated to
    :mod:`.publish_hard_verification`, which re-verifies the reviewed export
    against the *provenance-bound* problem it was accepted with. It fails
    closed, so a missing/stale/inconsistent checkpoint is never mistaken for a
    zero-violation pass.

    Publication eligibility is *tournament-scoped*: an incremental republish is
    gated on the exact last-publication delta (identity traceability, no silent
    hosting transfer, no unexplained removal, and no placement whose own
    booking evidence rejects/contradicts it), while the full-season semantic
    audit's planning debt remains visible as diagnostic rather than as a global
    blocker. A missing accepted booking is not itself a blocker: the placement
    publishes as a clearly labelled proposal awaiting host confirmation. A
    missing/stale audit still blocks, and an unresolved (``NOT_CHECKABLE``)
    scope falls back to the full audit gate.
    """
    from .audit_result import audit_is_fresh, is_blocking_status
    from .capability_result import CapabilityResult
    from .publication_scope import STATUS_ELIGIBLE
    from .publish_hard_verification import (
        current_hard_verification,
        hard_verification_evidence,
        hard_verification_problems,
    )

    hard_verification = current_hard_verification(work_dir)
    if hard_verification.get("error") or not hard_verification.get("ok"):
        if hard_verification.get("error"):
            summary = (
                "Publisering blokkert: eksporten kunne ikke hard-verifiseres mot den "
                "proveniensbundne revisjonen den ble akseptert med."
            )
            suggested_actions = [
                "Kjør 'rvv-miniputt season export' på gjeldende kanoniske revisjon og "
                "prøv publisering på nytt; ikke publiser en eksport uten komplett "
                "verification-context.",
            ]
        else:
            summary = "Publisering blokkert: planen feiler deterministisk hard verifisering."
            suggested_actions = []
        return with_collision_warning(CapabilityResult.blocked(
            summary,
            capability="pages_publish",
            problems=hard_verification_problems(hard_verification),
            evidence=hard_verification_evidence(hard_verification),
            suggested_actions=suggested_actions,
            artifacts=list(bundle_result.artifacts),
        ))

    audit_fresh, audit_result_payload = audit_is_fresh(work_dir)
    audit_status = (audit_result_payload or {}).get("status") if audit_fresh else None
    if not audit_fresh:
        reason = (
            "Ingen revisjon funnet for denne eksporten."
            if audit_result_payload is None
            else "Revisjonsresultatet er foreldet (matcher ikke gjeldende eksport/kjøring)."
        )
        return with_collision_warning(CapabilityResult.blocked(
            f"Semantisk revisjon mangler eller er foreldet — publisering krever et "
            f"gyldig revisjonsresultat for denne eksporten. {reason}",
            capability="pages_publish",
            problems=[reason],
            suggested_actions=[
                "Kjør 'rvv-miniputt operator audit-context' og 'operator audit-submit' "
                "(interaktiv harness), eller 'operator audit-run --backend <navn>' (headless).",
            ],
            artifacts=list(bundle_result.artifacts),
        ))

    scope = _resolve_publication_scope(work_dir=work_dir, repo_dir=repo_dir)
    if scope is not None and scope.get("status") == STATUS_ELIGIBLE:
        # Full-season planning debt is retained as diagnostic (the audit result
        # and the assessment reasons stay on record); it no longer blocks an
        # incremental republish whose exact delta is fully eligible.
        return None
    if scope is not None:
        blocked = _blocked_for_publication_scope(
            scope=scope,
            bundle_result=bundle_result,
            with_collision_warning=with_collision_warning,
        )
        if blocked is not None:
            return blocked

    # NOT_CHECKABLE or unresolved scope: preserve the existing semantic gate.
    if is_blocking_status(audit_status):
        reason = f"Semantisk revisjon feilet eller er ufullstendig (status={audit_status})."
        return with_collision_warning(CapabilityResult.blocked(
            f"Semantisk revisjon feilet eller er ufullstendig — publisering krever et "
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
