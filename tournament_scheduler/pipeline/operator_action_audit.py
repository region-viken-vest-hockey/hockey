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

# A distinct token set from `operator_action._PUBLICATION_APPROVAL_ANSWERS`:
# a stale "--confirm-public"/"godkjenn" answer to the publication question
# must never also satisfy the separate audit-review escalation — an operator
# reviewing a REVIEW_REQUIRED semantic-audit finding must answer that
# specific question, not have an unrelated older publish approval reused
# for it.
_AUDIT_REVIEW_APPROVAL_ANSWERS = {"godkjenn revisjon", "godkjent revisjon", "audit approved", "approve audit"}


def is_audit_review_approved_answer(answer: str) -> bool:
    return answer.strip().lower() in _AUDIT_REVIEW_APPROVAL_ANSWERS


def execute_get_audit_context(*, work_dir: str) -> "CapabilityResult":
    """Assemble the read-only evidence inventory for the semantic safety-net
    audit — no LLM call, no fresh scraping, just facts the pipeline already
    produced."""
    from .audit_context import build_audit_context
    from .capability_result import CapabilityResult

    context = build_audit_context(work_dir=work_dir)
    if not context.get("export_fingerprint"):
        return CapabilityResult.failed(
            "Ingen Stage 4-eksport funnet — kjør eksport før revisjon.", capability="llm_audit"
        )
    return CapabilityResult.ok(
        "Revisjonskontekst satt sammen.",
        capability="llm_audit",
        evidence=[json.dumps(context, ensure_ascii=False, default=str)],
    )


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

    # A harness submission may omit audit_id; derive it so a later
    # REVIEW_REQUIRED approval stays scoped to this exact export.
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
    # Honor this run's explicit operator waivers here too, so the publish gate
    # blocks only *unwaived* hard violations -- a valid operator exception must
    # not make publication impossible. Best-effort: fall back to the
    # problem-free self-consistency verification when the problem can't be
    # reconstructed.
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
    """Return a blocked :class:`CapabilityResult` if publication must stop
    here, or ``None`` if the audit gate passes and publish should proceed.

    Order matters: deterministic hard verification is checked first and
    always wins regardless of any audit result (a harness ``PASS`` can never
    override an actual hard violation), then audit freshness/status, then
    (for ``REVIEW_REQUIRED``) an operator-review escalation distinct from
    the publish-approval escalation.
    """
    from .audit_result import audit_is_fresh, is_blocking_status
    from .capability_result import CapabilityResult
    from .escalation import EscalationType, Question, raise_question
    from .run_manifest import RunManifest

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

    audit_id = (audit_result_payload or {}).get("audit_id")
    export_fingerprint = (audit_result_payload or {}).get("export_fingerprint")
    audit_question = Question(
        type=EscalationType.AUDIT_REVIEW.value,
        capability="pages_publish",
        # The export fingerprint is part of the question identity too, so an
        # approval never spans two different exports even if audit_id is absent.
        summary=(
            f"Godkjenn publisering til tross for REVIEW_REQUIRED fra semantisk revisjon "
            f"{audit_id} for eksport {export_fingerprint}?"
        ),
    )
    existing_audit_answer = next(
        (q for q in RunManifest(work_dir).all_questions() if q.get("id") == audit_question.id), None
    )
    if existing_audit_answer is not None and existing_audit_answer.get("answered"):
        if is_audit_review_approved_answer(existing_audit_answer.get("answer") or ""):
            try:
                from .audit_export_artifact import materialize_review_approval

                materialize_review_approval(
                    work_dir,
                    audit_payload=audit_result_payload or {},
                    question=existing_audit_answer,
                )
            except (OSError, ValueError) as exc:
                return with_collision_warning(CapabilityResult.blocked(
                    "Revisjonen er godkjent, men godkjenningen kunne ikke bindes til eksporten.",
                    capability="pages_publish",
                    problems=[str(exc)],
                    artifacts=list(bundle_result.artifacts),
                ))
            return None
        return with_collision_warning(CapabilityResult.blocked(
            f"Revisjonsgjennomgang ble avvist tidligere (svar: {existing_audit_answer.get('answer')!r}).",
            capability="pages_publish",
            problems=["Revisjonsgjennomgang avvist for dette revisjonsresultatet."],
            evidence=[f"audit_id={audit_id}"],
            artifacts=list(bundle_result.artifacts),
        ))

    audit_question.context = (
        f"Semantisk revisjon returnerte REVIEW_REQUIRED for eksport-fingeravtrykk "
        f"{(audit_result_payload or {}).get('export_fingerprint')}."
    )
    audit_question.alternatives = [
        f"Svar 'godkjenn revisjon' på spørsmål {audit_question.id} for å publisere likevel",
    ]
    audit_question.recommendation = "Se over revisjonsfunnene før godkjenning."
    raise_question(work_dir, audit_question)
    return with_collision_warning(CapabilityResult.blocked(
        f"Semantisk revisjon krever operatørgjennomgang før publisering (revisjon {audit_id}).",
        capability="pages_publish",
        suggested_actions=list(audit_question.alternatives),
        evidence=[f"audit_id={audit_id}", f"question_id={audit_question.id}"],
        artifacts=list(bundle_result.artifacts),
    ))
