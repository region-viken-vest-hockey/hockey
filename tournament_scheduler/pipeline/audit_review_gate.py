"""REVIEW_REQUIRED operator escalation and export approval provenance."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .capability_result import CapabilityResult

_AUDIT_REVIEW_APPROVAL_ANSWERS = {
    "godkjenn revisjon",
    "godkjent revisjon",
    "audit approved",
    "approve audit",
}


def is_audit_review_approved_answer(answer: str) -> bool:
    return answer.strip().lower() in _AUDIT_REVIEW_APPROVAL_ANSWERS


def apply_review_required_gate(
    *,
    work_dir: str,
    audit_result_payload: dict,
    bundle_result: "CapabilityResult",
    with_collision_warning: "Callable[[CapabilityResult], CapabilityResult]",
) -> "CapabilityResult | None":
    """Handle the explicit operator gate for one REVIEW_REQUIRED audit."""
    from .audit_export_artifact import materialize_review_approval
    from .capability_result import CapabilityResult
    from .escalation import EscalationType, Question, raise_question
    from .run_manifest import RunManifest

    audit_id = audit_result_payload.get("audit_id")
    export_fingerprint = audit_result_payload.get("export_fingerprint")
    audit_question = Question(
        type=EscalationType.AUDIT_REVIEW.value,
        capability="pages_publish",
        summary=(
            "Godkjenn publisering til tross for REVIEW_REQUIRED fra semantisk revisjon "
            f"{audit_id} for eksport {export_fingerprint}?"
        ),
    )
    existing_answer = next(
        (q for q in RunManifest(work_dir).all_questions() if q.get("id") == audit_question.id),
        None,
    )
    if existing_answer is not None and existing_answer.get("answered"):
        if is_audit_review_approved_answer(existing_answer.get("answer") or ""):
            try:
                materialize_review_approval(
                    work_dir,
                    audit_payload=audit_result_payload,
                    question=existing_answer,
                )
            except (OSError, ValueError) as exc:
                return with_collision_warning(
                    CapabilityResult.blocked(
                        "Revisjonen er godkjent, men godkjenningen kunne ikke bindes til eksporten.",
                        capability="pages_publish",
                        problems=[str(exc)],
                        artifacts=list(bundle_result.artifacts),
                    )
                )
            return None
        return with_collision_warning(
            CapabilityResult.blocked(
                f"Revisjonsgjennomgang ble avvist tidligere (svar: {existing_answer.get('answer')!r}).",
                capability="pages_publish",
                problems=["Revisjonsgjennomgang avvist for dette revisjonsresultatet."],
                evidence=[f"audit_id={audit_id}"],
                artifacts=list(bundle_result.artifacts),
            )
        )

    audit_question.context = (
        "Semantisk revisjon returnerte REVIEW_REQUIRED for eksport-fingeravtrykk "
        f"{export_fingerprint}."
    )
    audit_question.alternatives = [
        f"Svar 'godkjenn revisjon' på spørsmål {audit_question.id} for å publisere likevel"
    ]
    audit_question.recommendation = "Se over revisjonsfunnene før godkjenning."
    raise_question(work_dir, audit_question)
    return with_collision_warning(
        CapabilityResult.blocked(
            f"Semantisk revisjon krever operatørgjennomgang før publisering (revisjon {audit_id}).",
            capability="pages_publish",
            suggested_actions=list(audit_question.alternatives),
            evidence=[f"audit_id={audit_id}", f"question_id={audit_question.id}"],
            artifacts=list(bundle_result.artifacts),
        )
    )
