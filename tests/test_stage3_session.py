"""Hermetic tests for the interactive Stage 3 session store/facade."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tournament_scheduler.application.stage3_session import (
    SCOPE_CANDIDATE,
    SCOPE_RUN,
    STAGE3_SESSION_SCHEMA_VERSION,
    STATUS_AWAITING_SHARED_HOST,
    STATUS_FINALIZED,
    Stage3Session,
    candidate_content_fingerprint,
)
from tournament_scheduler.application.stage3_session_store import (
    Stage3SessionStore,
    status_for_session,
)


def _candidate(seed: int) -> dict:
    return {"tournaments": [{"id": f"t{seed}", "date": "2026-10-05"}]}


def _plan(seed: int) -> dict:
    return {"plan": _candidate(seed), "warnings": []}


def _context(capability: str, *, fingerprint: str = "") -> dict:
    return {
        "schema_version": 1,
        "run_id": "run-1",
        "capability": capability,
        "stage": "planning",
        "facts": {"candidate_fingerprint": fingerprint},
        "available_actions": ["keep_baseline"],
    }


class TestSessionSerialization:
    def test_round_trip(self):
        session = Stage3Session(run_id="run-1")
        session.attempts = {"attempts_used": 2, "best_attempt": 1}
        session.advance_candidate(
            _plan(2),
            fingerprint="fp-2",
            source="stage3_interactive:attempt_2",
            transition="create_baseline",
            action_id="proceed",
            rationale="initial",
            at="2026-01-01T00:00:00+00:00",
        )
        session.set_pending(capability="stage3_interactive", context=_context("stage3_interactive"))

        restored = Stage3Session.from_dict(session.to_dict())

        assert restored.run_id == "run-1"
        assert restored.candidate_revision == 1
        assert restored.candidate_fingerprint == "fp-2"
        assert restored.pending_scope() == SCOPE_CANDIDATE
        assert restored.decision_history[0]["from_revision"] == 0
        assert restored.decision_history[0]["to_revision"] == 1

    def test_rejects_future_schema_version(self):
        payload = Stage3Session().to_dict()
        payload["schema_version"] = STAGE3_SESSION_SCHEMA_VERSION + 1
        with pytest.raises(Exception):
            Stage3Session.from_dict(payload)


class TestMigrationFromLegacySideFiles:
    def test_migrates_legacy_interactive_state(self, tmp_path: Path):
        store = Stage3SessionStore(tmp_path)
        best_plan = _plan(1)
        pending = _plan(2)
        store.interactive_path.write_text(
            json.dumps(
                {
                    "run_id": "run-1",
                    "attempts_used": 2,
                    "best_attempt": 1,
                    "best_plan": best_plan,
                    "pending_candidate": pending,
                    "pending_attempt": 2,
                    "last_context": _context("stage3_interactive"),
                }
            ),
            encoding="utf-8",
        )

        session = store.load(expected_run_id="run-1")

        assert session.run_id == "run-1"
        assert session.candidate == best_plan
        assert session.candidate_revision == 2
        assert session.pending_scope() == SCOPE_CANDIDATE
        assert session.pending_fingerprint() == candidate_content_fingerprint(pending["plan"])
        assert session.attempts["best_attempt"] == 1

    def test_migrates_pending_shared_host_as_run_scoped(self, tmp_path: Path):
        store = Stage3SessionStore(tmp_path)
        store.shared_host_path.write_text(
            json.dumps(
                {
                    "run_id": "run-1",
                    "decisions": [{"registration": "A/B", "age_group": "U10", "chosen_club": "A"}],
                    "unresolved": [],
                    "pending": {"registration": "C/D", "age_group": "U11"},
                    "last_context": _context("shared_host_assignment"),
                }
            ),
            encoding="utf-8",
        )

        session = store.load(expected_run_id="run-1")

        assert session.status == STATUS_AWAITING_SHARED_HOST
        assert session.pending_scope() == SCOPE_RUN
        assert session.shared_host_decisions[0]["chosen_club"] == "A"

    def test_new_run_does_not_inherit_prior_session(self, tmp_path: Path):
        store = Stage3SessionStore(tmp_path)
        session = Stage3Session(run_id="old-run")
        session.attempts = {"attempts_used": 3}
        store.save(session)

        fresh = store.load(expected_run_id="new-run")

        assert fresh.run_id == "new-run"
        assert fresh.attempts == {}
        assert fresh.candidate is None

    def test_clear_removes_all_side_state(self, tmp_path: Path):
        store = Stage3SessionStore(tmp_path)
        session = Stage3Session(run_id="run-1")
        store.save(session)
        store.shared_host_path.write_text("{}", encoding="utf-8")
        store.arena_path.write_text("{}", encoding="utf-8")

        store.clear()

        assert not store.session_path.exists()
        assert not store.interactive_path.exists()
        assert not store.shared_host_path.exists()
        assert not store.arena_path.exists()


class TestProjection:
    def test_save_writes_legacy_projection_while_pending(self, tmp_path: Path):
        store = Stage3SessionStore(tmp_path)
        session = Stage3Session(run_id="run-1")
        session.candidate = _plan(1)
        session.attempts = {"attempts_used": 1, "best_attempt": 1}
        session.set_pending(
            capability="stage3_interactive",
            context=_context("stage3_interactive"),
            candidates=[{"candidate": _plan(2), "candidate_ref": "stage3_interactive:attempt_2"}],
            attempt=2,
        )

        store.save(session)

        projection = json.loads(store.interactive_path.read_text(encoding="utf-8"))
        assert projection["run_id"] == "run-1"
        assert projection["best_plan"] == _plan(1)
        assert projection["pending_candidate"] == _plan(2)
        assert projection["last_context"]["capability"] == "stage3_interactive"

    def test_finalized_session_clears_transient_projection(self, tmp_path: Path):
        store = Stage3SessionStore(tmp_path)
        session = Stage3Session(run_id="run-1")
        session.candidate = _plan(1)
        session.candidate_fingerprint = candidate_content_fingerprint(_candidate(1))
        session.set_pending(capability="stage3_interactive", context=_context("stage3_interactive"))
        store.save(session)

        session.finalize(
            transition="select_candidate", action_id="apply_candidate", rationale="adopt", at="now"
        )
        store.save(session)

        assert not store.interactive_path.exists()
        assert json.loads(store.session_path.read_text(encoding="utf-8"))["status"] == STATUS_FINALIZED

    def test_finalized_candidate_matches_only_exact_revision(self, tmp_path: Path):
        store = Stage3SessionStore(tmp_path)
        session = Stage3Session(run_id="run-1")
        session.candidate = _plan(1)
        session.candidate_fingerprint = candidate_content_fingerprint(_candidate(1))
        session.finalize(transition="keep_baseline", action_id="keep_baseline", rationale="keep", at="now")
        store.save(session)

        assert store.finalized_candidate_matches(_plan(1)) is True
        assert store.finalized_candidate_matches(_plan(2)) is False

    def test_no_finalized_session_does_not_block(self, tmp_path: Path):
        store = Stage3SessionStore(tmp_path)
        assert store.finalized_candidate_matches(_plan(9)) is True


class TestStatusFacade:
    def test_status_view_is_compact_and_machine_readable(self, tmp_path: Path):
        store = Stage3SessionStore(tmp_path)
        session = Stage3Session(run_id="run-1")
        session.candidate = _plan(1)
        session.candidate_revision = 2
        session.set_pending(capability="stage3_interactive", context=_context("stage3_interactive"))

        view = status_for_session(session)

        assert view["run_id"] == "run-1"
        assert view["status"] == "awaiting_adoption"
        assert view["candidate_revision"] == 2
        assert view["pending_decision"]["capability"] == "stage3_interactive"
        assert "run_search" in view["legal_transitions"]
        assert view["finalized_revision"] is None
