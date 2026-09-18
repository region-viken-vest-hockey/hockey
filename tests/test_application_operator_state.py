import pytest

from tournament_scheduler.application.dto import OperatorHealth
from tournament_scheduler.application.operator_state import (
    check_operator_health,
    list_operator_questions,
    promote_operator_question,
    record_operator_answer,
)
from tournament_scheduler.pipeline.escalation import Question, raise_question
from tournament_scheduler.pipeline.run_manifest import RunManifest


def test_list_operator_questions_returns_typed_unanswered_questions(tmp_path):
    RunManifest(tmp_path).start_run("objective")
    question = Question(
        type="credentials",
        capability="scraping",
        summary="Need source login",
        recommendation="Use club service account",
    )
    raise_question(str(tmp_path), question)

    questions = list_operator_questions(str(tmp_path))

    assert len(questions) == 1
    assert questions[0].id == question.id
    assert questions[0].type == "credentials"
    assert questions[0].capability == "scraping"
    assert questions[0].summary == "Need source login"
    assert questions[0].recommendation == "Use club service account"
    assert questions[0].created_at is not None
    assert questions[0].to_dict()["alternatives"] == []


def test_answer_and_all_questions_preserve_audit_trail(tmp_path):
    RunManifest(tmp_path).start_run("objective")
    question = Question(type="credentials", capability="scraping", summary="x")
    raise_question(str(tmp_path), question)

    answered = record_operator_answer(str(tmp_path), question.id, "fixed", decided_by="operator")

    assert answered.answered is True
    assert answered.answer == "fixed"
    assert answered.decided_by == "operator"
    assert list_operator_questions(str(tmp_path)) == []
    assert [q.id for q in list_operator_questions(str(tmp_path), include_all=True)] == [question.id]


def test_answer_is_linked_into_the_controller_trace(tmp_path):
    """An operator answer is reconstructable against the same run/candidate."""
    from tournament_scheduler.pipeline.controller_trace import (
        EVENT_OPERATOR_ANSWER,
        read_controller_trace,
    )
    from tournament_scheduler.pipeline.state import PipelineState, StageName, StageStatus

    manifest = RunManifest(tmp_path)
    manifest.start_run("objective", run_id="run-trace-1")
    PipelineState(tmp_path).write_stage(
        StageName.EXPORT,
        {"export_fingerprint": "export-fp-1"},
        status=StageStatus.DONE,
    )
    question = Question(
        type="external_publication",
        capability="pages_publish",
        summary="Godkjenn publisering?",
        scope="run",
        scope_key="run-trace-1",
    )
    raise_question(str(tmp_path), question)

    record_operator_answer(str(tmp_path), question.id, "godkjenn", decided_by="operator")

    events = read_controller_trace(tmp_path, "run-trace-1")
    answers = [e for e in events if e["event"] == EVENT_OPERATOR_ANSWER]
    assert len(answers) == 1
    answer = answers[0]
    assert answer["question_id"] == question.id
    assert answer["question_type"] == "external_publication"
    assert answer["scope"] == "run"
    assert answer["answer"] == "godkjenn"
    assert answer["decided_by"] == "operator"
    assert answer["export_fingerprint"] == "export-fp-1"


def test_promote_operator_question_returns_new_typed_scope(tmp_path):
    RunManifest(tmp_path).start_run("objective", run_id="run-1")
    question = Question(
        type="credentials",
        capability="scraping",
        summary="x",
        scope="run",
        scope_key="run-1",
    )
    raise_question(str(tmp_path), question)
    record_operator_answer(str(tmp_path), question.id, "fixed")

    promoted = promote_operator_question(str(tmp_path), question.id, "workspace")

    assert promoted.scope == "workspace"
    assert promoted.scope_key == ""
    assert promoted.answer == "fixed"
    assert promoted.promoted_from == question.id


def test_record_answer_unknown_question_raises_value_error(tmp_path):
    RunManifest(tmp_path).start_run("objective")

    with pytest.raises(ValueError):
        record_operator_answer(str(tmp_path), "missing", "fixed")


def test_operator_health_is_typed_and_exposes_exit_code(tmp_path):
    RunManifest(tmp_path).start_run("objective")

    health = check_operator_health(str(tmp_path))

    assert health == OperatorHealth(healthy=True, writable=True, detail="", manifest_recovery=None)
    assert health.to_dict()["healthy"] is True
    assert health.exit_code == 0


def test_operator_health_reports_recovered_manifest(tmp_path):
    RunManifest(tmp_path).path.write_text("broken", encoding="utf-8")

    health = check_operator_health(str(tmp_path))

    assert health.healthy is False
    assert health.writable is True
    assert health.manifest_recovery is not None
    assert health.exit_code == 1
