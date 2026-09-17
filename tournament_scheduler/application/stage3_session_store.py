"""Persistence/facade for the interactive Stage 3 session state.

One repository API (:class:`Stage3SessionStore`) reads and writes the
interactive Stage 3 state for a run directory. The canonical, versioned
object lives in ``stage3_session.json``. For compatibility during the
migration away from the older ad-hoc side files, the store also

- migrates an existing ``stage3_interactive_state.json`` /
  ``shared_host_decision_state.json`` /
  ``arena_conflict_decision_state.json`` triple into a session on first load,
  and
- projects a loaded session back onto ``stage3_interactive_state.json`` while
  a decision is still pending, so callers that still read that file (and the
  accumulated evidence around it) keep working unchanged.

Callers must not coordinate the individual files themselves: they load a
session, hand it to :mod:`stage3_controller`, and save the result.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from .stage3_session import (
    SCOPE_CANDIDATE,
    STATUS_FINALIZED,
    STATUS_NEW,
    Stage3Session,
    candidate_content_fingerprint,
    scope_for_capability,
)

SESSION_FILENAME = "stage3_session.json"
INTERACTIVE_FILENAME = "stage3_interactive_state.json"
SHARED_HOST_FILENAME = "shared_host_decision_state.json"
ARENA_FILENAME = "arena_conflict_decision_state.json"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _run_scoped(data: Mapping[str, Any], expected_run_id: str | None) -> dict[str, Any]:
    if not data:
        return {}
    if expected_run_id and data.get("run_id") != expected_run_id:
        return {}
    return dict(data)


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def extract_candidate_body(plan: Any) -> dict[str, Any] | None:
    """Return the candidate body (``{"tournaments": [...]}``) inside *plan*.

    Shape handling only -- the same two checkpoint shapes
    ``planning_contract.extract_candidate`` accepts. Kept here so the
    application layer does not need to import planner/domain internals just
    to fingerprint a candidate.
    """
    if not isinstance(plan, dict):
        return None
    if "tournaments" in plan:
        return plan
    inner = plan.get("plan")
    if isinstance(inner, dict) and "tournaments" in inner:
        return inner
    return None


def fingerprint_plan(plan: Any) -> str:
    body = extract_candidate_body(plan)
    if body is None:
        return ""
    return candidate_content_fingerprint(body)


class Stage3SessionStore:
    """Canonical read/write API for one work directory's Stage 3 session."""

    def __init__(self, work_dir: "str | Path") -> None:
        self.work_dir = Path(work_dir)

    # -- paths ------------------------------------------------------------

    @property
    def session_path(self) -> Path:
        return self.work_dir / SESSION_FILENAME

    @property
    def interactive_path(self) -> Path:
        return self.work_dir / INTERACTIVE_FILENAME

    @property
    def shared_host_path(self) -> Path:
        return self.work_dir / SHARED_HOST_FILENAME

    @property
    def arena_path(self) -> Path:
        return self.work_dir / ARENA_FILENAME

    # -- load / save ------------------------------------------------------

    def load(self, expected_run_id: str | None = None) -> Stage3Session:
        canonical = _read_json(self.session_path)
        if canonical:
            session = Stage3Session.from_dict(canonical)
            if expected_run_id and session.run_id and session.run_id != expected_run_id:
                # A genuinely new run must never inherit a superseded run's
                # interactive Stage 3 state.
                return Stage3Session(run_id=expected_run_id)
            return session
        return self._migrate(expected_run_id)

    def save(self, session: Stage3Session) -> None:
        _write_json(self.session_path, session.to_dict())
        if session.is_finalized():
            # The legacy per-attempt side file is transient decision state;
            # the finalized revision/fingerprint survive in the canonical
            # session file for the Stage 4 handoff.
            self.interactive_path.unlink(missing_ok=True)
            return
        _write_json(self.interactive_path, self._projection(session))

    def clear(self) -> None:
        for path in (self.session_path, self.interactive_path, self.shared_host_path, self.arena_path):
            path.unlink(missing_ok=True)

    # -- emission overlay -------------------------------------------------

    def record_emission(
        self,
        interactive_state: Mapping[str, Any],
        *,
        candidate_revision: int | None = None,
        run_id: str | None = None,
        transition: str = "create_baseline",
        action_id: str = "",
        rationale: str = "",
    ) -> Stage3Session:
        """Fold a freshly emitted Stage 3 decision context into the session.

        The emission path historically writes ``stage3_interactive_state.json``
        directly to publish a pending decision. This overlay mirrors that
        pending decision (and the candidate revision it belongs to) into the
        canonical session without forcing the emission code to build a
        session itself.
        """
        shared = self._read_legacy(self.shared_host_path, run_id)
        arena = self._read_legacy(self.arena_path, run_id)
        migrated = self._from_legacy(dict(interactive_state), shared, arena, run_id=run_id or "")
        prior = _read_json(self.session_path)
        existing = None
        if prior:
            try:
                existing = Stage3Session.from_dict(prior)
            except Exception:
                existing = None
            if existing is not None and existing.run_id == migrated.run_id:
                migrated.decision_history = existing.decision_history
                migrated.shared_host_decisions = migrated.shared_host_decisions or existing.shared_host_decisions
        prior_revision = existing.candidate_revision if existing is not None and existing.run_id == migrated.run_id else 0
        if candidate_revision is not None and migrated.pending_decision:
            revision = max(int(candidate_revision), migrated.candidate_revision)
            migrated.candidate_revision = revision
            if migrated.pending_scope() == SCOPE_CANDIDATE:
                migrated.pending_decision["candidate_revision"] = revision
            if revision != prior_revision:
                from datetime import datetime, timezone

                migrated.record_history(
                    transition=transition,
                    action_id=action_id or transition,
                    rationale=rationale,
                    from_revision=prior_revision,
                    to_revision=revision,
                    at=datetime.now(timezone.utc).isoformat(),
                )
        self.save(migrated)
        return migrated

    # -- finalization handoff --------------------------------------------

    def finalized_candidate_matches(self, plan: Any) -> bool:
        """True when *plan* is the exact candidate the session finalized.

        Returns ``True`` when there is no finalized session to enforce, so
        callers that never used the session are unaffected.
        """
        canonical = _read_json(self.session_path)
        if not canonical:
            return True
        session = Stage3Session.from_dict(canonical)
        if session.status != STATUS_FINALIZED or not session.finalized_fingerprint:
            return True
        return fingerprint_plan(plan) == session.finalized_fingerprint

    # -- internals --------------------------------------------------------

    def _read_legacy(self, path: Path, run_id: str | None) -> dict[str, Any]:
        return _run_scoped(_read_json(path), run_id)

    def _migrate(self, expected_run_id: str | None) -> Stage3Session:
        interactive = self._read_legacy(self.interactive_path, expected_run_id)
        shared = self._read_legacy(self.shared_host_path, expected_run_id)
        arena = self._read_legacy(self.arena_path, expected_run_id)
        if not (interactive or shared or arena):
            return Stage3Session(run_id=expected_run_id or "")
        return self._from_legacy(interactive, shared, arena, run_id=expected_run_id or "")

    def _from_legacy(
        self,
        interactive: dict[str, Any],
        shared: dict[str, Any],
        arena: dict[str, Any],
        *,
        run_id: str,
    ) -> Stage3Session:
        session = Stage3Session(
            run_id=run_id
            or str(interactive.get("run_id") or shared.get("run_id") or arena.get("run_id") or "")
        )
        best_plan = interactive.get("best_plan")
        session.candidate = dict(best_plan) if isinstance(best_plan, dict) else None
        session.attempts = {
            "attempts_used": int(interactive.get("attempts_used", 0) or 0),
            "best_attempt": int(interactive.get("best_attempt", 0) or 0),
        }
        session.candidate_revision = int(interactive.get("attempts_used", 0) or 0)
        session.shared_host_decisions = [dict(item) for item in (shared.get("decisions") or [])]
        session.arena_decisions = [dict(item) for item in (arena.get("decisions") or [])]
        session.unresolved = [dict(item) for item in (shared.get("unresolved") or [])] + [
            dict(item) for item in (arena.get("unresolved") or [])
        ]
        if session.candidate is not None:
            session.candidate_fingerprint = fingerprint_plan(session.candidate)
            source = session.candidate.get("source")
            session.candidate_source = str(source) if isinstance(source, str) else ""

        pending_capability, context = self._pending_context(interactive, shared, arena)
        candidates = self._pending_candidates(interactive, context)
        if pending_capability:
            pending_reference = candidates[0]["candidate"] if candidates else session.candidate
            session.set_pending(
                capability=pending_capability,
                context=context,
                scope=scope_for_capability(pending_capability),
                candidates=candidates,
                attempt=int(interactive.get("pending_attempt", 0) or 0) or None,
                search_exhausted=int(session.attempts.get("attempts_used", 0)) >= 3
                and pending_capability
                in {"stage3_interactive", "stage3_optimize", "stage3_pareto"},
            )
            if session.pending_scope() == SCOPE_CANDIDATE:
                session.pending_decision["candidate_fingerprint"] = fingerprint_plan(pending_reference)
                session.pending_decision["candidate_revision"] = session.candidate_revision
        else:
            session.status = STATUS_NEW if session.candidate is None else STATUS_FINALIZED
        return session

    def _pending_context(
        self,
        interactive: dict[str, Any],
        shared: dict[str, Any],
        arena: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        if shared.get("pending"):
            context = shared.get("last_context")
            if isinstance(context, dict):
                return "shared_host_assignment", dict(context)
        if arena.get("pending"):
            context = arena.get("last_context")
            if isinstance(context, dict):
                return "arena_conflict_resolution", dict(context)
        context = interactive.get("last_context")
        if isinstance(context, dict) and context.get("capability"):
            return str(context["capability"]), dict(context)
        return "", {}

    def _pending_candidates(
        self, interactive: dict[str, Any], context: Mapping[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        plural = interactive.get("pending_candidates")
        if isinstance(plural, list) and plural:
            return [dict(item) for item in plural if isinstance(item, dict)]
        single = interactive.get("pending_candidate")
        if isinstance(single, dict):
            entry: dict[str, Any] = {"candidate": dict(single)}
            # The legacy single-candidate side file never stored the ref; the
            # decision context did (e.g. ``stage3_interactive:attempt_2`` or
            # ``stage3_cp_sat:...``), so carry it over for exact-ref checks.
            context_ref = (context or {}).get("candidate_ref")
            if context_ref:
                entry["candidate_ref"] = str(context_ref)
            return [entry]
        return []

    def _projection(self, session: Stage3Session) -> dict[str, Any]:
        state: dict[str, Any] = {}
        if session.run_id:
            state["run_id"] = session.run_id
        attempts = session.attempts or {}
        if "attempts_used" in attempts:
            state["attempts_used"] = attempts["attempts_used"]
        if "best_attempt" in attempts:
            state["best_attempt"] = attempts["best_attempt"]
        if session.candidate is not None:
            state["best_plan"] = session.candidate
        pending = session.pending_decision or {}
        candidates = pending.get("candidates") or []
        if len(candidates) == 1:
            state["pending_candidate"] = candidates[0].get("candidate")
        elif len(candidates) > 1:
            state["pending_candidates"] = list(candidates)
        attempt = pending.get("attempt")
        if attempt:
            state["pending_attempt"] = attempt
        if isinstance(pending.get("context"), dict):
            state["last_context"] = pending["context"]
        return state


def load_stage3_session(work_dir: "str | Path", expected_run_id: str | None = None) -> Stage3Session:
    return Stage3SessionStore(work_dir).load(expected_run_id)


def finalize_stage3_plan(
    work_dir: "str | Path",
    plan: Any,
    *,
    action_id: str,
    rationale: str,
    run_id: str | None = None,
    candidate_source: str = "",
) -> Stage3Session:
    """Persist the exact candidate revision/fingerprint Stage 4 must consume.

    Used by the CLI/harness adapter after a ``select_candidate``,
    ``keep_baseline`` or ``apply_repair`` transition has already committed
    *plan* to the Stage 3 checkpoint. Records a new revision when the
    finalized candidate differs from the session's current revision.
    """
    from datetime import datetime, timezone

    from .stage3_session import TRANSITION_FOR_ACTION, TRANSITION_KEEP_BASELINE

    store = Stage3SessionStore(work_dir)
    session = store.load(expected_run_id=run_id)
    body = extract_candidate_body(plan)
    fingerprint = candidate_content_fingerprint(body) if body is not None else session.candidate_fingerprint
    transition = TRANSITION_FOR_ACTION.get(action_id, TRANSITION_KEEP_BASELINE)
    at = datetime.now(timezone.utc).isoformat()
    if fingerprint and fingerprint != session.candidate_fingerprint:
        session.advance_candidate(
            plan if isinstance(plan, dict) else {},
            fingerprint=fingerprint,
            source=candidate_source,
            transition=transition,
            action_id=action_id,
            rationale=rationale,
            at=at,
        )
    session.finalize(transition=transition, action_id=action_id, rationale=rationale, at=at)
    store.save(session)
    return session


def save_stage3_session(work_dir: "str | Path", session: Stage3Session) -> None:
    Stage3SessionStore(work_dir).save(session)


def status_for_session(session: Stage3Session) -> dict[str, Any]:
    """Compact, machine-readable view for the ``stage3 session`` facade."""
    return {
        "schema_version": session.schema_version,
        "run_id": session.run_id,
        "status": session.status,
        "candidate_revision": session.candidate_revision,
        "candidate_fingerprint": session.candidate_fingerprint,
        "candidate_source": session.candidate_source,
        "pending_decision": (
            {
                "capability": session.pending_decision.get("capability"),
                "scope": session.pending_decision.get("scope"),
                "candidate_revision": session.pending_decision.get("candidate_revision"),
                "candidate_fingerprint": session.pending_decision.get("candidate_fingerprint"),
            }
            if session.pending_decision
            else None
        ),
        "shared_host_decisions": len(session.shared_host_decisions),
        "attempts": dict(session.attempts),
        "finalized_revision": session.finalized_revision,
        "finalized_fingerprint": session.finalized_fingerprint,
        "legal_transitions": session.legal_transitions(),
    }
