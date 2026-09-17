"""Explicit, typed, versioned state for one interactive Stage 3 run.

Interactive Stage 3 is a resumable workflow (build candidate -> pause for a
decision -> persist -> resume in a new process -> repair/search/adopt ->
finalize -> Stage 4), not a one-shot batch stage. Historically "where Stage 3
is" and "which candidate is current" were inferred by coordinating several
side files (the planning checkpoint, ``stage3_interactive_state.json``,
``shared_host_decision_state.json``, ``arena_conflict_decision_state.json``
and the CP-SAT cache) plus fall-through branches in
``run_command_interactive.py``. Every new capability had to decide which of
those to read/preserve/rewrite/clear, which is exactly where repeated
lifecycle defects came from.

This module introduces :class:`Stage3Session`: one authoritative typed object
that owns

- candidate identity (``candidate_revision`` + ``candidate_fingerprint``),
- run-scoped decisions (shared-host choices) versus candidate-scoped
  decisions (arena conflicts, local repairs),
- the currently pending interaction (with its exact candidate scope),
- concise transition provenance, and
- the finalized revision/fingerprint Stage 4 must consume.

It deliberately contains no hockey legality and no repair/search selection:
domain capabilities decide what is legal and what a mutation does; the
session/controller decide how that operation is applied, resumed and
persisted over a run/candidate revision (see ``stage3_controller``).

The persisted schema is versioned and serialized by
:class:`stage3_session_store.Stage3SessionStore`. ``from_legacy_state``
migrates the pre-session side-file shape so existing work directories keep
resuming without a big-bang rewrite.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

STAGE3_SESSION_SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Status / scope / transition vocabulary
# ---------------------------------------------------------------------------

STATUS_NEW = "new"
STATUS_AWAITING_SHARED_HOST = "awaiting_shared_host"
STATUS_AWAITING_PLACEMENT_CONFLICT = "awaiting_placement_conflict"
STATUS_AWAITING_ADOPTION = "awaiting_adoption"
STATUS_AWAITING_OPERATOR = "awaiting_operator"
STATUS_FINALIZED = "finalized"

# A run-scoped decision stays valid across later candidate revisions where its
# facts remain valid (e.g. a shared/joint-club hosting choice). A
# candidate-scoped decision belongs to exactly one revision/fingerprint.
SCOPE_RUN = "run"
SCOPE_CANDIDATE = "candidate"

# Stable transition vocabulary. New #348 repair providers vary their option
# ids underneath ``apply_repair``; they do not add a new transition type.
TRANSITION_CREATE_BASELINE = "create_baseline"
TRANSITION_ASSIGN_SHARED_HOST = "assign_shared_host"
TRANSITION_RESOLVE_PLACEMENT_CONFLICT = "resolve_placement_conflict"
TRANSITION_APPLY_REPAIR = "apply_repair"
TRANSITION_RUN_SEARCH = "run_search"
TRANSITION_SELECT_CANDIDATE = "select_candidate"
TRANSITION_KEEP_BASELINE = "keep_baseline"
TRANSITION_REQUEST_OPERATOR = "request_operator"
TRANSITION_FINALIZE_STAGE3 = "finalize_stage3"

ALL_TRANSITIONS: tuple[str, ...] = (
    TRANSITION_CREATE_BASELINE,
    TRANSITION_ASSIGN_SHARED_HOST,
    TRANSITION_RESOLVE_PLACEMENT_CONFLICT,
    TRANSITION_APPLY_REPAIR,
    TRANSITION_RUN_SEARCH,
    TRANSITION_SELECT_CANDIDATE,
    TRANSITION_KEEP_BASELINE,
    TRANSITION_REQUEST_OPERATOR,
    TRANSITION_FINALIZE_STAGE3,
)

# Which DecisionAction maps to which lifecycle transition. This is the one
# place the CLI/controller translate the action vocabulary into lifecycle.
TRANSITION_FOR_ACTION: Mapping[str, str] = {
    "assign_shared_host": TRANSITION_ASSIGN_SHARED_HOST,
    "resolve_arena_conflict": TRANSITION_RESOLVE_PLACEMENT_CONFLICT,
    "apply_repair_option": TRANSITION_APPLY_REPAIR,
    "optimize_plan": TRANSITION_RUN_SEARCH,
    "apply_candidate": TRANSITION_SELECT_CANDIDATE,
    "keep_baseline": TRANSITION_KEEP_BASELINE,
    "request_operator": TRANSITION_REQUEST_OPERATOR,
}

# Capability -> status/serving scope for a pending DecisionContext. Kept here
# so the session (not each caller) decides what "pending" means.
_CAPABILITY_STATUS = {
    "shared_host_assignment": STATUS_AWAITING_SHARED_HOST,
    "arena_conflict_resolution": STATUS_AWAITING_PLACEMENT_CONFLICT,
    "stage3_interactive": STATUS_AWAITING_ADOPTION,
    "stage3_optimize": STATUS_AWAITING_ADOPTION,
    "stage3_pareto": STATUS_AWAITING_ADOPTION,
    "host_team_missing_repair": STATUS_AWAITING_ADOPTION,
    "underfilled_roster_repair": STATUS_AWAITING_ADOPTION,
    "host_placement_repair": STATUS_AWAITING_ADOPTION,
    "search_neighborhood_repair": STATUS_AWAITING_ADOPTION,
}

_RUN_SCOPED_CAPABILITIES = frozenset({"shared_host_assignment"})


class Stage3SessionVersionError(ValueError):
    """Raised when persisted session state uses an unsupported schema version."""


def candidate_content_fingerprint(candidate: Mapping[str, Any]) -> str:
    """Deterministic content fingerprint for a Stage 3 candidate body.

    Mirrors the domain-side fingerprint used by the repair providers so a
    session revision and a provider-validated option fingerprint agree.
    """
    payload = json.dumps(candidate, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class Stage3Session:
    """Authoritative interactive Stage 3 state for one ``run_id``."""

    run_id: str = ""
    schema_version: int = STAGE3_SESSION_SCHEMA_VERSION
    status: str = STATUS_NEW
    candidate_revision: int = 0
    candidate_fingerprint: str = ""
    candidate: dict[str, Any] | None = None
    candidate_source: str = ""
    shared_host_decisions: list[dict[str, Any]] = field(default_factory=list)
    arena_decisions: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    pending_decision: dict[str, Any] | None = None
    decision_history: list[dict[str, Any]] = field(default_factory=list)
    attempts: dict[str, Any] = field(default_factory=dict)
    finalized_revision: int | None = None
    finalized_fingerprint: str | None = None

    # -- identity helpers -------------------------------------------------

    def pending_scope(self) -> str:
        if not self.pending_decision:
            return ""
        return str(self.pending_decision.get("scope") or SCOPE_CANDIDATE)

    def pending_revision(self) -> int | None:
        if not self.pending_decision:
            return None
        revision = self.pending_decision.get("candidate_revision")
        return int(revision) if revision is not None else None

    def pending_fingerprint(self) -> str:
        if not self.pending_decision:
            return ""
        return str(self.pending_decision.get("candidate_fingerprint") or "")

    def is_finalized(self) -> bool:
        return self.status == STATUS_FINALIZED

    def legal_transitions(self) -> list[str]:
        """Return the transition types valid from the current session state."""
        if self.is_finalized():
            return []
        if not self.pending_decision:
            return [TRANSITION_CREATE_BASELINE]
        capability = str(self.pending_decision.get("capability") or "")
        if capability == "shared_host_assignment":
            return [TRANSITION_ASSIGN_SHARED_HOST, TRANSITION_REQUEST_OPERATOR]
        if capability == "arena_conflict_resolution":
            return [TRANSITION_RESOLVE_PLACEMENT_CONFLICT, TRANSITION_REQUEST_OPERATOR]
        # Candidate-scoped comparison/repair context. Search is only offered
        # while an attempt budget remains (the caller caps it).
        legal = [
            TRANSITION_RUN_SEARCH,
            TRANSITION_APPLY_REPAIR,
            TRANSITION_SELECT_CANDIDATE,
            TRANSITION_KEEP_BASELINE,
            TRANSITION_REQUEST_OPERATOR,
        ]
        if self.pending_decision.get("search_exhausted"):
            legal.remove(TRANSITION_RUN_SEARCH)
        return legal

    # -- mutation ---------------------------------------------------------

    def record_history(
        self,
        *,
        transition: str,
        action_id: str,
        rationale: str,
        from_revision: int,
        to_revision: int,
        at: str,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "transition": transition,
            "action_id": action_id,
            "rationale": rationale,
            "from_revision": from_revision,
            "to_revision": to_revision,
            "candidate_fingerprint": self.candidate_fingerprint,
            "at": at,
        }
        if extra:
            entry.update(dict(extra))
        self.decision_history.append(entry)
        return entry

    def advance_candidate(
        self,
        candidate: dict[str, Any],
        *,
        fingerprint: str,
        source: str,
        transition: str,
        action_id: str,
        rationale: str,
        at: str,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Explicitly move the session to a new candidate revision.

        Every candidate-changing transition goes through here, so a new
        revision/fingerprint always exists and the prior candidate-scoped
        pending decision is invalidated rather than silently replayed.
        """
        from_revision = self.candidate_revision
        self.candidate_revision = from_revision + 1
        self.candidate_fingerprint = fingerprint
        self.candidate = candidate
        self.candidate_source = source
        self.pending_decision = None
        self.status = STATUS_AWAITING_ADOPTION
        # A candidate change invalidates any pending placement/repair scope
        # that referred to the previous revision.
        return self.record_history(
            transition=transition,
            action_id=action_id,
            rationale=rationale,
            from_revision=from_revision,
            to_revision=self.candidate_revision,
            at=at,
            extra=extra,
        )

    def set_pending(
        self,
        *,
        capability: str,
        context: Mapping[str, Any],
        scope: str | None = None,
        candidates: list[dict[str, Any]] | None = None,
        attempt: int | None = None,
        search_exhausted: bool = False,
    ) -> None:
        resolved_scope = scope or (
            SCOPE_RUN if capability in _RUN_SCOPED_CAPABILITIES else SCOPE_CANDIDATE
        )
        self.pending_decision = {
            "capability": capability,
            "scope": resolved_scope,
            "candidate_revision": self.candidate_revision if resolved_scope == SCOPE_CANDIDATE else None,
            "candidate_fingerprint": self.candidate_fingerprint if resolved_scope == SCOPE_CANDIDATE else "",
            "context": dict(context),
            "candidates": list(candidates or []),
            "attempt": attempt,
            "search_exhausted": search_exhausted,
        }
        self.status = _CAPABILITY_STATUS.get(capability, STATUS_AWAITING_ADOPTION)

    def finalize(self, *, transition: str, action_id: str, rationale: str, at: str) -> None:
        self.finalized_revision = self.candidate_revision
        self.finalized_fingerprint = self.candidate_fingerprint
        self.status = STATUS_FINALIZED
        self.pending_decision = None
        self.record_history(
            transition=transition,
            action_id=action_id,
            rationale=rationale,
            from_revision=self.candidate_revision,
            to_revision=self.candidate_revision,
            at=at,
        )

    # -- serialization ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "status": self.status,
            "candidate_revision": self.candidate_revision,
            "candidate_fingerprint": self.candidate_fingerprint,
            "candidate": self.candidate,
            "candidate_source": self.candidate_source,
            "shared_host_decisions": list(self.shared_host_decisions),
            "arena_decisions": list(self.arena_decisions),
            "unresolved": list(self.unresolved),
            "pending_decision": self.pending_decision,
            "decision_history": list(self.decision_history),
            "attempts": dict(self.attempts),
            "finalized_revision": self.finalized_revision,
            "finalized_fingerprint": self.finalized_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Stage3Session":
        version = int(data.get("schema_version") or 1)
        if version > STAGE3_SESSION_SCHEMA_VERSION:
            raise Stage3SessionVersionError(
                f"unsupported Stage 3 session schema_version {version} "
                f"(this build understands <= {STAGE3_SESSION_SCHEMA_VERSION})"
            )
        return cls(
            run_id=str(data.get("run_id") or ""),
            schema_version=STAGE3_SESSION_SCHEMA_VERSION,
            status=str(data.get("status") or STATUS_NEW),
            candidate_revision=int(data.get("candidate_revision") or 0),
            candidate_fingerprint=str(data.get("candidate_fingerprint") or ""),
            candidate=dict(data["candidate"]) if isinstance(data.get("candidate"), dict) else None,
            candidate_source=str(data.get("candidate_source") or ""),
            shared_host_decisions=[dict(item) for item in (data.get("shared_host_decisions") or [])],
            arena_decisions=[dict(item) for item in (data.get("arena_decisions") or [])],
            unresolved=[dict(item) for item in (data.get("unresolved") or [])],
            pending_decision=dict(data["pending_decision"]) if isinstance(data.get("pending_decision"), dict) else None,
            decision_history=[dict(item) for item in (data.get("decision_history") or [])],
            attempts=dict(data.get("attempts") or {}),
            finalized_revision=(
                int(data["finalized_revision"]) if data.get("finalized_revision") is not None else None
            ),
            finalized_fingerprint=(
                str(data["finalized_fingerprint"]) if data.get("finalized_fingerprint") is not None else None
            ),
        )


def status_for_capability(capability: str) -> str:
    return _CAPABILITY_STATUS.get(capability, STATUS_AWAITING_ADOPTION)


def scope_for_capability(capability: str) -> str:
    return SCOPE_RUN if capability in _RUN_SCOPED_CAPABILITIES else SCOPE_CANDIDATE
