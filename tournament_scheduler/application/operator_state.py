"""Operator-state application use cases.

This module is the first typed slice of the application layer requested by
GitHub issue #44. It wraps durable pipeline state APIs in small in-process
functions that transports can call without owning the policy themselves.
"""

from __future__ import annotations

from .dto import OperatorHealth, OperatorQuestion
from ..pipeline.escalation import (
    all_questions,
    answer_question,
    promote_question,
    unanswered_questions,
)
from ..pipeline.run_manifest import RunManifest


def list_operator_questions(work_dir: str, *, include_all: bool = False) -> list[OperatorQuestion]:
    """Return operator escalation questions for *work_dir*.

    By default this returns only unanswered questions. ``include_all=True``
    returns the audit trail, including answered and stale questions.
    """

    raw_questions = all_questions(work_dir) if include_all else unanswered_questions(work_dir)
    return [OperatorQuestion.from_dict(question) for question in raw_questions]


def record_operator_answer(
    work_dir: str,
    question_id: str,
    answer: str,
    *,
    decided_by: str | None = None,
) -> OperatorQuestion:
    """Record a durable answer to a previously-raised operator question."""

    entry = OperatorQuestion.from_dict(
        answer_question(work_dir, question_id, answer, decided_by=decided_by)
    )
    _trace_operator_answer(work_dir, entry)
    return entry


def _trace_operator_answer(work_dir: str, entry: OperatorQuestion) -> None:
    """Link one durable operator answer to the run's controller trace.

    Best-effort observability only: the answer is already persisted in the
    run manifest, so a trace write failure must never fail the decision. The
    event records the question/answer/actor plus the run and the candidate/
    export identity the question was asked against, so a post-run analysis can
    place the human decision on the same timeline as the automatic ones.
    """
    try:
        from ..pipeline.audit_result import current_export_fingerprint
        from ..pipeline.controller_trace import (
            EVENT_OPERATOR_ANSWER,
            ControllerTrace,
            resolve_trace_run_id,
        )

        run_id = resolve_trace_run_id(work_dir)
        candidate_fingerprint = ""
        try:
            from .stage3_session_store import Stage3SessionStore

            session = Stage3SessionStore(work_dir).load(expected_run_id=run_id or None)
            candidate_fingerprint = str(
                session.finalized_fingerprint or session.candidate_fingerprint or ""
            )
        except Exception:
            candidate_fingerprint = ""
        ControllerTrace(work_dir, run_id).emit(
            EVENT_OPERATOR_ANSWER,
            question_id=entry.id,
            question_type=entry.type,
            capability=entry.capability,
            scope=entry.scope,
            scope_key=entry.scope_key,
            answer=entry.answer,
            decided_by=entry.decided_by,
            candidate_fingerprint=candidate_fingerprint,
            export_fingerprint=current_export_fingerprint(work_dir),
        )
    except Exception:
        # Observability must never fail or roll back the recorded answer.
        pass


def promote_operator_question(
    work_dir: str,
    question_id: str,
    scope: str,
    *,
    scope_key: str = "",
    decided_by: str | None = None,
) -> OperatorQuestion:
    """Promote an answered operator question to a broader decision scope."""

    return OperatorQuestion.from_dict(
        promote_question(
            work_dir,
            question_id,
            scope,
            new_scope_key=scope_key,
            decided_by=decided_by,
        )
    )


def check_operator_health(work_dir: str) -> OperatorHealth:
    """Check whether the operator run manifest is readable and writable."""

    return OperatorHealth.from_dict(RunManifest(work_dir).check_health())
