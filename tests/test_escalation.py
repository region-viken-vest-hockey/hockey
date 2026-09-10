"""Tests for the human escalation and approval protocol (pipeline/escalation.py)."""

from __future__ import annotations

import argparse
import json

import pytest

from tournament_scheduler.pipeline.capability_result import CapabilityResult
from tournament_scheduler.pipeline.escalation import (
    EscalationType,
    Question,
    all_questions,
    answer_question,
    from_capability_result,
    infer_escalation_type,
    promote_question,
    raise_question,
    scope_key_for,
    scope_order,
    unanswered_questions,
)
from tournament_scheduler.pipeline.run_manifest import RunManifest


class TestQuestion:
    def test_valid_type_accepted(self):
        q = Question(type="credentials", capability="scraping", summary="x")
        assert q.type == "credentials"

    def test_accepts_enum_member(self):
        q = Question(type=EscalationType.CREDENTIALS, capability="scraping", summary="x")
        assert q.type == "credentials"

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError):
            Question(type="not-a-real-type", capability="scraping", summary="x")

    def test_id_is_stable_for_identical_fields(self):
        a = Question(type="credentials", capability="scraping", summary="Kongsberg blocked")
        b = Question(type="credentials", capability="scraping", summary="Kongsberg blocked")
        assert a.id == b.id

    def test_id_differs_for_different_summary(self):
        a = Question(type="credentials", capability="scraping", summary="Kongsberg blocked")
        b = Question(type="credentials", capability="scraping", summary="Skien blocked")
        assert a.id != b.id

    def test_to_dict_has_unanswered_shape(self):
        q = Question(
            type="incomplete_data",
            capability="scraping",
            summary="0 sources",
            context="ctx",
            alternatives=["a", "b"],
            recommendation="a",
            impact="plan cannot be trusted",
        )
        data = q.to_dict()
        assert data["answered"] is False
        assert data["answer"] is None
        assert data["alternatives"] == ["a", "b"]
        assert data["recommendation"] == "a"
        assert data["impact"] == "plan cannot be trusted"
        assert "id" in data and "created_at" in data


class TestInferEscalationType:
    def test_detects_credentials(self):
        result = CapabilityResult.blocked("x", suggested_actions=["Sett legitimasjon via ENV_VAR"])
        assert infer_escalation_type(result) == "credentials"

    def test_detects_impossible_constraints(self):
        result = CapabilityResult.blocked("x", problems=["Arena/dag-kollisjon oppdaget"])
        assert infer_escalation_type(result) == "impossible_constraints"

    def test_detects_ambiguous_policy(self):
        result = CapabilityResult.blocked("x", problems=["LLM approval gate rejected the plan"])
        assert infer_escalation_type(result) == "ambiguous_policy"

    def test_defaults_to_incomplete_data(self):
        result = CapabilityResult.blocked("x")
        assert infer_escalation_type(result) == "incomplete_data"


class TestFromCapabilityResult:
    def test_builds_question_from_result_fields(self):
        result = CapabilityResult(
            status="blocked",
            summary="Kongsberg ishall blocked",
            evidence=["event_count=0", "strategy=browser"],
            problems=["Timeout"],
            suggested_actions=["Set credentials via KONGSBERG_USER", "Try scrape-llm"],
            requires_human=True,
            capability="scraping",
        )
        question = from_capability_result(result)
        assert question.capability == "scraping"
        assert question.summary == "Kongsberg ishall blocked"
        assert question.context == "event_count=0; strategy=browser"
        assert question.alternatives == ["Set credentials via KONGSBERG_USER", "Try scrape-llm"]
        assert question.recommendation == "Set credentials via KONGSBERG_USER"
        assert question.impact == "Timeout"

    def test_explicit_escalation_type_overrides_inference(self):
        result = CapabilityResult.blocked("x", problems=["Timeout"])
        question = from_capability_result(result, escalation_type="destructive_repair")
        assert question.type == "destructive_repair"


class TestRaiseAndAnswerQuestion:
    def test_raise_records_question_in_manifest(self, tmp_path):
        RunManifest(tmp_path).start_run("objective")
        question = Question(type="credentials", capability="scraping", summary="x")
        entry = raise_question(str(tmp_path), question)
        assert entry["id"] == question.id
        assert RunManifest(tmp_path).read()["pending_questions"] == [entry]

    def test_raising_the_same_question_twice_does_not_duplicate(self, tmp_path):
        RunManifest(tmp_path).start_run("objective")
        question = Question(type="credentials", capability="scraping", summary="x")
        raise_question(str(tmp_path), question)
        raise_question(str(tmp_path), question)
        assert len(RunManifest(tmp_path).read()["pending_questions"]) == 1

    def test_answer_marks_question_answered(self, tmp_path):
        RunManifest(tmp_path).start_run("objective")
        question = Question(type="credentials", capability="scraping", summary="x")
        raise_question(str(tmp_path), question)

        entry = answer_question(str(tmp_path), question.id, "set the env var", decided_by="niclas")
        assert entry["answered"] is True
        assert entry["answer"] == "set the env var"
        assert entry["decided_by"] == "niclas"
        assert entry["decided_at"]

    def test_answer_unknown_id_raises(self, tmp_path):
        RunManifest(tmp_path).start_run("objective")
        with pytest.raises(ValueError):
            answer_question(str(tmp_path), "not-a-real-id", "answer")

    def test_unanswered_questions_excludes_answered_ones(self, tmp_path):
        RunManifest(tmp_path).start_run("objective")
        q1 = Question(type="credentials", capability="scraping", summary="q1")
        q2 = Question(type="incomplete_data", capability="planning", summary="q2")
        raise_question(str(tmp_path), q1)
        raise_question(str(tmp_path), q2)
        answer_question(str(tmp_path), q1.id, "done")

        remaining = unanswered_questions(str(tmp_path))
        assert [q["id"] for q in remaining] == [q2.id]

    def test_raising_a_question_already_answered_does_not_reopen_it(self, tmp_path):
        """The core anti-repetition guarantee: once answered, the exact same
        question raised again must stay answered, not reappear as pending."""
        RunManifest(tmp_path).start_run("objective")
        question = Question(type="credentials", capability="scraping", summary="x")
        raise_question(str(tmp_path), question)
        answer_question(str(tmp_path), question.id, "fixed it")

        raise_question(str(tmp_path), question)  # raised again by a later run

        assert unanswered_questions(str(tmp_path)) == []
        stored = RunManifest(tmp_path).read()["pending_questions"]
        assert len(stored) == 1
        assert stored[0]["answered"] is True


class TestPendingQuestionsSurviveNewRuns:
    """start_run() must carry pending_questions forward — a human answering
    a question happens *between* separate CLI invocations, so the manifest
    reset that begins every new run must not erase escalation history."""

    def test_answered_question_survives_a_new_start_run(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.start_run("first objective")
        question = Question(type="credentials", capability="scraping", summary="x")
        raise_question(str(tmp_path), question)
        answer_question(str(tmp_path), question.id, "fixed it")

        manifest.start_run("second objective, resumed run")

        data = manifest.read()
        assert data["objective"] == "second objective, resumed run"
        assert data["capabilities"] == []  # per-run history does reset
        assert len(data["pending_questions"]) == 1
        assert data["pending_questions"][0]["answered"] is True

    def test_unanswered_question_also_survives(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.start_run("first objective")
        question = Question(type="credentials", capability="scraping", summary="x")
        raise_question(str(tmp_path), question)

        manifest.start_run("second objective")

        assert len(unanswered_questions(str(tmp_path))) == 1

    def test_fresh_workspace_starts_with_no_pending_questions(self, tmp_path):
        manifest = RunManifest(tmp_path)
        data = manifest.start_run("objective")
        assert data["pending_questions"] == []


class TestOperatorEscalationIntegration:
    def test_raise_escalation_questions_scans_capabilities_requiring_human(self, tmp_path):
        from tournament_scheduler.cli.pipeline_orchestrator import _raise_escalation_questions

        manifest = RunManifest(tmp_path)
        manifest.start_run("objective")
        manifest.record_capability(
            CapabilityResult(
                status="blocked",
                summary="Kongsberg blocked",
                problems=["Timeout"],
                suggested_actions=["Set credentials via KONGSBERG_USER"],
                requires_human=True,
                capability="scraping",
            )
        )
        manifest.record_capability(CapabilityResult.ok("fine", capability="config"))

        _raise_escalation_questions(str(tmp_path))

        pending = unanswered_questions(str(tmp_path))
        assert len(pending) == 1
        assert pending[0]["capability"] == "scraping"

    def test_raise_escalation_questions_is_a_noop_when_nothing_requires_human(self, tmp_path):
        from tournament_scheduler.cli.pipeline_orchestrator import _raise_escalation_questions

        manifest = RunManifest(tmp_path)
        manifest.start_run("objective")
        manifest.record_capability(CapabilityResult.ok("fine", capability="config"))

        _raise_escalation_questions(str(tmp_path))

        assert unanswered_questions(str(tmp_path)) == []

    def test_raise_escalation_questions_never_raises_on_missing_manifest(self, tmp_path):
        from tournament_scheduler.cli.pipeline_orchestrator import _raise_escalation_questions

        _raise_escalation_questions(str(tmp_path / "does-not-exist"))  # should not raise


class TestOperatorQuestionsAndAnswerCli:
    def test_questions_json_output(self, tmp_path, capsys):
        from tournament_scheduler.cli.rvv_cli import _cmd_operator_questions

        RunManifest(tmp_path).start_run("objective")
        raise_question(str(tmp_path), Question(type="credentials", capability="scraping", summary="x"))

        args = argparse.Namespace(work_dir=str(tmp_path), json=True)
        rc = _cmd_operator_questions(args)
        assert rc == 0
        printed = json.loads(capsys.readouterr().out)
        assert len(printed) == 1
        assert printed[0]["type"] == "credentials"

    def test_questions_human_readable_shows_id_and_recommendation(self, tmp_path, capsys):
        from tournament_scheduler.cli.rvv_cli import _cmd_operator_questions

        RunManifest(tmp_path).start_run("objective")
        question = Question(
            type="credentials", capability="scraping", summary="x", recommendation="do this",
        )
        raise_question(str(tmp_path), question)

        args = argparse.Namespace(work_dir=str(tmp_path), json=False)
        rc = _cmd_operator_questions(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert question.id in out
        assert "do this" in out
        assert "(credentials)" in out

    def test_questions_empty_prints_friendly_message(self, tmp_path, capsys):
        from tournament_scheduler.cli.rvv_cli import _cmd_operator_questions

        args = argparse.Namespace(work_dir=str(tmp_path), json=False)
        rc = _cmd_operator_questions(args)
        assert rc == 0
        assert "Ingen ubesvarte" in capsys.readouterr().out

    def test_answer_records_decision_and_prints_confirmation(self, tmp_path, capsys):
        from tournament_scheduler.cli.rvv_cli import _cmd_operator_answer

        RunManifest(tmp_path).start_run("objective")
        question = Question(type="credentials", capability="scraping", summary="x")
        raise_question(str(tmp_path), question)

        args = argparse.Namespace(work_dir=str(tmp_path), question_id=question.id, answer="fixed", decided_by="niclas")
        rc = _cmd_operator_answer(args)
        assert rc == 0
        assert "Registrert svar" in capsys.readouterr().out
        assert unanswered_questions(str(tmp_path)) == []

    def test_answer_unknown_id_returns_1(self, tmp_path, capsys):
        from tournament_scheduler.cli.rvv_cli import _cmd_operator_answer

        RunManifest(tmp_path).start_run("objective")
        args = argparse.Namespace(work_dir=str(tmp_path), question_id="bogus", answer="x", decided_by=None)
        rc = _cmd_operator_answer(args)
        assert rc == 1

    def test_dispatch_routes_questions_and_answer(self):
        from unittest.mock import patch

        from tournament_scheduler.cli.rvv_cli import _cmd_operator

        with patch("tournament_scheduler.cli.rvv_cli._cmd_operator_questions", return_value=0) as mock_q:
            _cmd_operator(argparse.Namespace(operator_command="questions"))
        mock_q.assert_called_once()

        with patch("tournament_scheduler.cli.rvv_cli._cmd_operator_answer", return_value=0) as mock_a:
            _cmd_operator(argparse.Namespace(operator_command="answer"))
        mock_a.assert_called_once()


class TestArgParsing:
    def test_operator_questions_parses(self):
        from tournament_scheduler.cli.args import build_parser

        args = build_parser().parse_args(["operator", "questions"])
        assert args.operator_command == "questions"
        assert args.json is False

    def test_operator_answer_parses(self):
        from tournament_scheduler.cli.args import build_parser

        args = build_parser().parse_args(["operator", "answer", "abc123", "yes, proceed"])
        assert args.operator_command == "answer"
        assert args.question_id == "abc123"
        assert args.answer == "yes, proceed"
        assert args.decided_by is None

    def test_operator_questions_all_flag_parses(self):
        from tournament_scheduler.cli.args import build_parser

        args = build_parser().parse_args(["operator", "questions", "--all"])
        assert args.all is True

    def test_operator_promote_parses(self):
        from tournament_scheduler.cli.args import build_parser

        args = build_parser().parse_args(
            ["operator", "promote", "abc123", "season", "--scope-key", "2026-2027"]
        )
        assert args.operator_command == "promote"
        assert args.question_id == "abc123"
        assert args.scope == "season"
        assert args.scope_key == "2026-2027"


# ---------------------------------------------------------------------------
# Decision scoping (issue #12): run / input_version / season / workspace
# ---------------------------------------------------------------------------


class TestScopeOrderingAndKeys:
    def test_scope_order_is_narrowest_to_broadest(self):
        assert scope_order("run") < scope_order("input_version") < scope_order("season") < scope_order("workspace")

    def test_scope_order_rejects_unknown_scope(self):
        with pytest.raises(ValueError):
            scope_order("not-a-scope")

    def test_scope_key_for_workspace_is_always_empty(self, tmp_path):
        assert scope_key_for(str(tmp_path), "workspace") == ""

    def test_scope_key_for_run_reads_run_id(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.start_run("objective", run_id="run-42")
        assert scope_key_for(str(tmp_path), "run") == "run-42"

    def test_scope_key_for_input_version_reads_fingerprint_sha256(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.start_run("objective", input_fingerprint={"sha256": "abc123"})
        assert scope_key_for(str(tmp_path), "input_version") == "abc123"

    def test_scope_key_for_season_requires_explicit_key(self, tmp_path):
        with pytest.raises(ValueError):
            scope_key_for(str(tmp_path), "season")


class TestQuestionScopeField:
    def test_default_scope_is_workspace(self):
        q = Question(type="credentials", capability="scraping", summary="x")
        assert q.scope == "workspace"
        assert q.scope_key == ""

    def test_invalid_scope_raises(self):
        with pytest.raises(ValueError):
            Question(type="credentials", capability="scraping", summary="x", scope="not-a-scope")

    def test_workspace_scoped_id_matches_pre_scoping_id(self):
        """Backward compatibility: a workspace-scoped question's id must be
        identical to a Question built before scope/scope_key existed, since
        that's the id every pre-#12 questions.json entry was stored under."""
        q = Question(type="credentials", capability="scraping", summary="Kongsberg blocked")
        legacy_equivalent = Question(
            type="credentials", capability="scraping", summary="Kongsberg blocked", scope="workspace", scope_key=""
        )
        assert q.id == legacy_equivalent.id

    def test_id_differs_by_scope_for_identical_content(self):
        run_q = Question(type="credentials", capability="scraping", summary="x", scope="run", scope_key="run-1")
        input_q = Question(
            type="credentials", capability="scraping", summary="x", scope="input_version", scope_key="run-1"
        )
        workspace_q = Question(type="credentials", capability="scraping", summary="x")
        assert len({run_q.id, input_q.id, workspace_q.id}) == 3

    def test_id_differs_by_scope_key_within_same_scope(self):
        a = Question(type="credentials", capability="scraping", summary="x", scope="run", scope_key="run-1")
        b = Question(type="credentials", capability="scraping", summary="x", scope="run", scope_key="run-2")
        assert a.id != b.id

    def test_to_dict_includes_scope_fields(self):
        q = Question(
            type="credentials", capability="scraping", summary="x", scope="input_version", scope_key="sha-1"
        )
        data = q.to_dict()
        assert data["scope"] == "input_version"
        assert data["scope_key"] == "sha-1"
        assert data["stale"] is False
        assert data["stale_reason"] is None
        assert data["promoted_from"] is None


class TestRunScopedReuse:
    def test_same_run_context_reuses_the_answer(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.start_run("objective", run_id="run-1")
        q = Question(type="credentials", capability="scraping", summary="x", scope="run", scope_key="run-1")
        raise_question(str(tmp_path), q)
        answer_question(str(tmp_path), q.id, "fixed")

        # A later capability call within the *same* run raises the identical
        # question again — must not reopen it.
        raise_question(str(tmp_path), q)
        assert unanswered_questions(str(tmp_path)) == []

    def test_a_later_run_is_not_silently_answered(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.start_run("objective", run_id="run-1")
        q1 = Question(type="credentials", capability="scraping", summary="x", scope="run", scope_key="run-1")
        raise_question(str(tmp_path), q1)
        answer_question(str(tmp_path), q1.id, "fixed for run 1")

        manifest.start_run("objective", run_id="run-2")
        q2 = Question(type="credentials", capability="scraping", summary="x", scope="run", scope_key="run-2")
        entry = raise_question(str(tmp_path), q2)

        assert entry["answered"] is False
        assert entry["id"] != q1.id
        pending = [q["id"] for q in unanswered_questions(str(tmp_path))]
        assert q2.id in pending

    def test_superseded_run_scoped_entry_is_marked_stale_not_deleted(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.start_run("objective", run_id="run-1")
        q1 = Question(type="credentials", capability="scraping", summary="x", scope="run", scope_key="run-1")
        raise_question(str(tmp_path), q1)
        answer_question(str(tmp_path), q1.id, "fixed for run 1")

        manifest.start_run("objective", run_id="run-2")
        q2 = Question(type="credentials", capability="scraping", summary="x", scope="run", scope_key="run-2")
        raise_question(str(tmp_path), q2)

        all_entries = {e["id"]: e for e in all_questions(str(tmp_path))}
        assert len(all_entries) == 2
        assert all_entries[q1.id]["stale"] is True
        assert all_entries[q1.id]["answer"] == "fixed for run 1"  # audit history preserved
        assert all_entries[q2.id]["stale"] is False


class TestInputVersionScopedReuse:
    def test_same_workbook_reuses_the_answer(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.start_run("objective", input_fingerprint={"sha256": "sha-a"})
        q = Question(
            type="incomplete_data", capability="planning", summary="x", scope="input_version", scope_key="sha-a"
        )
        raise_question(str(tmp_path), q)
        answer_question(str(tmp_path), q.id, "accepted the gap")

        raise_question(str(tmp_path), q)
        assert unanswered_questions(str(tmp_path)) == []

    def test_workbook_fingerprint_change_makes_the_answer_stale(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.start_run("objective", input_fingerprint={"sha256": "sha-a"})
        q_a = Question(
            type="incomplete_data", capability="planning", summary="x", scope="input_version", scope_key="sha-a"
        )
        raise_question(str(tmp_path), q_a)
        answer_question(str(tmp_path), q_a.id, "accepted the gap")

        manifest.start_run("objective", input_fingerprint={"sha256": "sha-b"})
        q_b = Question(
            type="incomplete_data", capability="planning", summary="x", scope="input_version", scope_key="sha-b"
        )
        entry = raise_question(str(tmp_path), q_b)

        assert entry["answered"] is False
        entries = {e["id"]: e for e in all_questions(str(tmp_path))}
        assert entries[q_a.id]["stale"] is True
        assert entries[q_b.id]["stale"] is False


class TestSeasonScopedReuse:
    def test_same_season_reuses_the_answer_across_runs_and_inputs(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.start_run("objective", run_id="run-1", input_fingerprint={"sha256": "sha-a"})
        q = Question(
            type="ambiguous_policy", capability="planning", summary="skip christmas week", scope="season",
            scope_key="2026-2027",
        )
        raise_question(str(tmp_path), q)
        answer_question(str(tmp_path), q.id, "yes, always skip it")

        # A different run, a different workbook, same season.
        manifest.start_run("objective", run_id="run-2", input_fingerprint={"sha256": "sha-b"})
        q_again = Question(
            type="ambiguous_policy", capability="planning", summary="skip christmas week", scope="season",
            scope_key="2026-2027",
        )
        raise_question(str(tmp_path), q_again)

        assert q.id == q_again.id
        assert unanswered_questions(str(tmp_path)) == []

    def test_new_season_makes_the_answer_stale(self, tmp_path):
        q_2026 = Question(
            type="ambiguous_policy", capability="planning", summary="skip christmas week", scope="season",
            scope_key="2026-2027",
        )
        raise_question(str(tmp_path), q_2026)
        answer_question(str(tmp_path), q_2026.id, "yes")

        q_2027 = Question(
            type="ambiguous_policy", capability="planning", summary="skip christmas week", scope="season",
            scope_key="2027-2028",
        )
        entry = raise_question(str(tmp_path), q_2027)

        assert entry["answered"] is False
        entries = {e["id"]: e for e in all_questions(str(tmp_path))}
        assert entries[q_2026.id]["stale"] is True


class TestWorkspaceScopedReuse:
    def test_workspace_scope_persists_regardless_of_run_or_input_changes(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.start_run("objective", run_id="run-1", input_fingerprint={"sha256": "sha-a"})
        q = Question(type="credentials", capability="scraping", summary="Kongsberg needs LLM scraping")
        raise_question(str(tmp_path), q)
        answer_question(str(tmp_path), q.id, "always use scrape-llm for Kongsberg")

        manifest.start_run("objective", run_id="run-2", input_fingerprint={"sha256": "sha-b"})
        entry = raise_question(str(tmp_path), q)

        assert entry["answered"] is True
        assert entry["answer"] == "always use scrape-llm for Kongsberg"
        assert len(all_questions(str(tmp_path))) == 1  # no stale duplicate — genuinely one durable decision


class TestPromotion:
    def test_promote_run_scoped_answer_to_workspace(self, tmp_path):
        manifest = RunManifest(tmp_path)
        manifest.start_run("objective", run_id="run-1")
        q = Question(type="credentials", capability="scraping", summary="x", scope="run", scope_key="run-1")
        raise_question(str(tmp_path), q)
        answer_question(str(tmp_path), q.id, "fixed")

        promoted = promote_question(str(tmp_path), q.id, "workspace", decided_by="niclas")

        assert promoted["scope"] == "workspace"
        assert promoted["answer"] == "fixed"
        assert promoted["promoted_from"] == q.id
        assert promoted["decided_by"] == "niclas"

        # The promoted (workspace) decision is now reusable in a brand new run.
        manifest.start_run("objective", run_id="run-2")
        reused = raise_question(
            str(tmp_path), Question(type="credentials", capability="scraping", summary="x")
        )
        assert reused["answered"] is True
        assert reused["id"] == promoted["id"]

    def test_source_entry_untouched_and_linked_after_promotion(self, tmp_path):
        RunManifest(tmp_path).start_run("objective", run_id="run-1")
        q = Question(type="credentials", capability="scraping", summary="x", scope="run", scope_key="run-1")
        raise_question(str(tmp_path), q)
        answer_question(str(tmp_path), q.id, "fixed")

        promoted = promote_question(str(tmp_path), q.id, "workspace")

        entries = {e["id"]: e for e in all_questions(str(tmp_path))}
        assert entries[q.id]["answer"] == "fixed"  # untouched, still auditable
        assert entries[q.id]["promoted_to"] == promoted["id"]

    def test_promote_to_narrower_or_equal_scope_raises(self, tmp_path):
        RunManifest(tmp_path).start_run("objective", input_fingerprint={"sha256": "sha-a"})
        q = Question(
            type="incomplete_data", capability="planning", summary="x", scope="input_version", scope_key="sha-a"
        )
        raise_question(str(tmp_path), q)

        with pytest.raises(ValueError):
            promote_question(str(tmp_path), q.id, "input_version")
        with pytest.raises(ValueError):
            promote_question(str(tmp_path), q.id, "run", new_scope_key="run-1")

    def test_promote_unknown_id_raises(self, tmp_path):
        RunManifest(tmp_path).start_run("objective")
        with pytest.raises(ValueError):
            promote_question(str(tmp_path), "bogus-id", "workspace")

    def test_promoting_the_same_decision_twice_is_idempotent(self, tmp_path):
        RunManifest(tmp_path).start_run("objective", run_id="run-1")
        q = Question(type="credentials", capability="scraping", summary="x", scope="run", scope_key="run-1")
        raise_question(str(tmp_path), q)
        answer_question(str(tmp_path), q.id, "fixed")

        first = promote_question(str(tmp_path), q.id, "workspace")
        second = promote_question(str(tmp_path), q.id, "workspace")
        assert first["id"] == second["id"]
        # Still just source + one promoted entry, not source + two promotions.
        assert len(all_questions(str(tmp_path))) == 2

    def test_promote_to_season_requires_explicit_scope_key(self, tmp_path):
        RunManifest(tmp_path).start_run("objective", run_id="run-1")
        q = Question(type="credentials", capability="scraping", summary="x", scope="run", scope_key="run-1")
        raise_question(str(tmp_path), q)
        answer_question(str(tmp_path), q.id, "fixed")

        promoted = promote_question(str(tmp_path), q.id, "season", new_scope_key="2026-2027")
        assert promoted["scope"] == "season"
        assert promoted["scope_key"] == "2026-2027"


class TestFromCapabilityResultScoping:
    def test_default_scope_is_workspace_unchanged(self):
        result = CapabilityResult.blocked("x", capability="scraping")
        question = from_capability_result(result)
        assert question.scope == "workspace"

    def test_explicit_scope_is_passed_through(self):
        result = CapabilityResult.blocked("x", capability="scraping")
        question = from_capability_result(result, scope="input_version", scope_key="sha-a")
        assert question.scope == "input_version"
        assert question.scope_key == "sha-a"


class TestOperatorRunEscalationScoping:
    def test_capability_escalations_are_scoped_to_input_version_when_fingerprint_present(self, tmp_path):
        from tournament_scheduler.cli.pipeline_orchestrator import _raise_escalation_questions

        manifest = RunManifest(tmp_path)
        manifest.start_run("objective", input_fingerprint={"sha256": "sha-a"})
        manifest.record_capability(
            CapabilityResult(
                status="blocked",
                summary="0 turneringer mulig",
                requires_human=True,
                capability="planning",
            )
        )
        _raise_escalation_questions(str(tmp_path))
        pending = unanswered_questions(str(tmp_path))
        assert len(pending) == 1
        assert pending[0]["scope"] == "input_version"
        assert pending[0]["scope_key"] == "sha-a"

    def test_answer_becomes_stale_after_a_new_workbook_reruns(self, tmp_path):
        from tournament_scheduler.cli.pipeline_orchestrator import _raise_escalation_questions

        manifest = RunManifest(tmp_path)
        manifest.start_run("objective", input_fingerprint={"sha256": "sha-a"})
        manifest.record_capability(
            CapabilityResult(
                status="blocked", summary="0 turneringer mulig", requires_human=True, capability="planning",
            )
        )
        _raise_escalation_questions(str(tmp_path))
        first_pending = unanswered_questions(str(tmp_path))
        answer_question(str(tmp_path), first_pending[0]["id"], "accepted")

        manifest.start_run("objective", input_fingerprint={"sha256": "sha-b"})
        manifest.record_capability(
            CapabilityResult(
                status="blocked", summary="0 turneringer mulig", requires_human=True, capability="planning",
            )
        )
        _raise_escalation_questions(str(tmp_path))

        entries = all_questions(str(tmp_path))
        stale = [e for e in entries if e.get("scope_key") == "sha-a"]
        fresh = [e for e in entries if e.get("scope_key") == "sha-b"]
        assert stale and stale[0]["stale"] is True
        assert fresh and fresh[0]["answered"] is False

    def test_falls_back_to_workspace_scope_without_a_fingerprint(self, tmp_path):
        from tournament_scheduler.cli.pipeline_orchestrator import _raise_escalation_questions

        manifest = RunManifest(tmp_path)
        manifest.start_run("objective")  # no input_fingerprint
        manifest.record_capability(
            CapabilityResult(status="blocked", summary="x", requires_human=True, capability="scraping")
        )
        _raise_escalation_questions(str(tmp_path))
        pending = unanswered_questions(str(tmp_path))
        assert pending[0]["scope"] == "workspace"


class TestQuestionsCliAllFlagAndPromote:
    def test_questions_default_excludes_answered(self, tmp_path, capsys):
        from tournament_scheduler.cli.rvv_cli import _cmd_operator_questions

        RunManifest(tmp_path).start_run("objective")
        q = Question(type="credentials", capability="scraping", summary="x")
        raise_question(str(tmp_path), q)
        answer_question(str(tmp_path), q.id, "fixed")

        args = argparse.Namespace(work_dir=str(tmp_path), json=True, all=False)
        _cmd_operator_questions(args)
        printed = json.loads(capsys.readouterr().out)
        assert printed == []

    def test_questions_all_flag_includes_answered_and_stale(self, tmp_path, capsys):
        from tournament_scheduler.cli.rvv_cli import _cmd_operator_questions

        manifest = RunManifest(tmp_path)
        manifest.start_run("objective", run_id="run-1")
        q1 = Question(type="credentials", capability="scraping", summary="x", scope="run", scope_key="run-1")
        raise_question(str(tmp_path), q1)
        answer_question(str(tmp_path), q1.id, "fixed for run 1")
        manifest.start_run("objective", run_id="run-2")
        q2 = Question(type="credentials", capability="scraping", summary="x", scope="run", scope_key="run-2")
        raise_question(str(tmp_path), q2)

        args = argparse.Namespace(work_dir=str(tmp_path), json=True, all=True)
        _cmd_operator_questions(args)
        printed = json.loads(capsys.readouterr().out)
        assert len(printed) == 2
        assert any(p["stale"] for p in printed)

    def test_promote_cli_records_promotion(self, tmp_path, capsys):
        from tournament_scheduler.cli.rvv_cli import _cmd_operator_promote

        RunManifest(tmp_path).start_run("objective", run_id="run-1")
        q = Question(type="credentials", capability="scraping", summary="x", scope="run", scope_key="run-1")
        raise_question(str(tmp_path), q)
        answer_question(str(tmp_path), q.id, "fixed")

        args = argparse.Namespace(
            work_dir=str(tmp_path), question_id=q.id, scope="workspace", scope_key="", decided_by=None
        )
        rc = _cmd_operator_promote(args)
        assert rc == 0
        assert "Forfremmet" in capsys.readouterr().out

    def test_promote_cli_unknown_id_returns_1(self, tmp_path, capsys):
        from tournament_scheduler.cli.rvv_cli import _cmd_operator_promote

        RunManifest(tmp_path).start_run("objective")
        args = argparse.Namespace(
            work_dir=str(tmp_path), question_id="bogus", scope="workspace", scope_key="", decided_by=None
        )
        rc = _cmd_operator_promote(args)
        assert rc == 1

    def test_dispatch_routes_promote(self):
        from unittest.mock import patch

        from tournament_scheduler.cli.rvv_cli import _cmd_operator

        with patch("tournament_scheduler.cli.rvv_cli._cmd_operator_promote", return_value=0) as mock_p:
            _cmd_operator(argparse.Namespace(operator_command="promote"))
        mock_p.assert_called_once()
