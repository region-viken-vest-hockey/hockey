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

from dataclasses import dataclass, field
from typing import Any, Mapping

# Schema 4 adds ``operator_request`` (an explicit awaiting-operator pause that
# preserves the current candidate revision/fingerprint). Schema 5 adds
# ``candidate_attempts``: a bounded portfolio of verified Stage 3 attempts with
# stable refs that survive later attempts, so a previously generated good
# candidate stays selectable instead of being lost when another attempt is
# generated. Schema 6 adds ``pareto_archive``/``convergence``: the bounded
# non-dominated frontier and the outer-loop convergence state, so an audited
# ``REVIEW_REQUIRED`` candidate can be refined autonomously across epochs
# instead of falling through to human escalation. Older payloads load unchanged
# because both fields default to empty.
STAGE3_SESSION_SCHEMA_VERSION = 6

# Bounded retention window for the verified-attempt portfolio. Deliberately
# small: it exists so the controller can pick the better of a few recently
# compared attempts, not as an unbounded candidate archive.
RETAINED_ATTEMPT_LIMIT = 6

# ---------------------------------------------------------------------------
# Status / scope / transition vocabulary
# ---------------------------------------------------------------------------

STATUS_NEW = "new"
STATUS_AWAITING_SHARED_HOST = "awaiting_shared_host"
STATUS_AWAITING_PLACEMENT_CONFLICT = "awaiting_placement_conflict"
STATUS_AWAITING_ADOPTION = "awaiting_adoption"
STATUS_AWAITING_OPERATOR = "awaiting_operator"
# A finalized (already exported/reviewed) unpromoted candidate that was
# explicitly reopened for finding-directed refinement. The reviewed candidate
# remains the baseline ``keep_baseline`` restores; a successful repair
# advances to a new revision and re-finalizes.
STATUS_REFINING = "refining"
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
# Explicitly reopen a finalized-but-unpromoted candidate for refinement. It is
# deliberately distinct from ``create_baseline`` (a fresh planning baseline),
# ``finalize_stage3`` (the Stage 4 handoff) and the recovery-only Stage 3
# reset: it keeps the exact reviewed candidate as the refinement baseline.
TRANSITION_REFINE_CANDIDATE = "refine_candidate"

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
    TRANSITION_REFINE_CANDIDATE,
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
    """Deterministic fingerprint for the canonical Stage 3 candidate shape.

    Candidate identity is a lifecycle contract shared by the Stage 3 session
    and every repair/arena provider. The one implementation lives in
    ``planning_contract`` (which owns the candidate schema version); this
    session-facing name only delegates to it, so the same schedule can never
    carry two fingerprints across the lifecycle and repair boundaries.
    """
    from ..planning_contract import candidate_content_fingerprint as _canonical

    return _canonical(candidate)


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
    # The candidate that ``keep_baseline`` restores. When a search produces a
    # new *current* candidate, the previously current one is retained here so
    # ``keep_baseline`` keeps the best/adopted plan while candidate-scoped
    # decisions (arena/repair) mutate only the current attempt. It is a
    # candidate body, not a second lifecycle status.
    baseline_candidate: dict[str, Any] | None = None
    baseline_fingerprint: str = ""
    baseline_revision: int | None = None
    shared_host_decisions: list[dict[str, Any]] = field(default_factory=list)
    arena_decisions: list[dict[str, Any]] = field(default_factory=list)
    # Unresolved sub-decisions are kept per scope so a shared-host ask never
    # leaks into the arena view and vice versa. The run-scoped versus
    # candidate-scoped split mirrors the decision scopes below.
    shared_host_unresolved: list[dict[str, Any]] = field(default_factory=list)
    arena_unresolved: list[dict[str, Any]] = field(default_factory=list)
    pending_decision: dict[str, Any] | None = None
    # Set when a candidate-scoped ``request_operator`` pauses the session: the
    # exact question/rationale and the candidate revision/fingerprint it refers
    # to. Asking the operator must never select, mutate or finalize a
    # candidate; the pending decision and current revision are preserved so the
    # operator answer continues from the same candidate.
    operator_request: dict[str, Any] | None = None
    # Set while (and after) an explicit refinement of an already-finalized
    # unpromoted candidate: the reviewed revision/fingerprint this refinement
    # descends from and the export it supersedes. Provenance, not a second
    # candidate authority.
    refinement: dict[str, Any] | None = None
    decision_history: list[dict[str, Any]] = field(default_factory=list)
    attempts: dict[str, Any] = field(default_factory=dict)
    # Bounded, stable-ref portfolio of independently verified Stage 3 attempt
    # candidates (see :meth:`retain_candidate_attempt`). Each entry carries the
    # candidate body plus its concise verification/quality evidence so a
    # non-current attempt can be re-selected without reset, re-solve or
    # reproduction. Retention is lifecycle state, not a second candidate
    # authority: an entry is only ever adopted through the explicit
    # ``select_candidate`` transition after re-validating staleness.
    candidate_attempts: list[dict[str, Any]] = field(default_factory=list)
    # One concise record per emitted Stage 3 attempt (action signature,
    # candidate scope and whether it made progress). This is the canonical
    # continuation evidence -- not a raw attempt cap -- the LLM/controller
    # reasons over; see ``stage3_progress``.
    search_attempts: list[dict[str, Any]] = field(default_factory=list)
    # Bounded, dominance-pruned frontier of independently verified candidates
    # (see ``application.pareto_convergence``). Each entry is a candidate ref +
    # objective vector/evidence; the candidate body (when retained) lives in
    # ``candidate_attempts``. It is lifecycle state, not a second candidate
    # authority: an entry is only ever adopted through the explicit
    # ``select_candidate`` transition.
    pareto_archive: list[dict[str, Any]] = field(default_factory=list)
    # Outer-loop convergence state (epochs, explored directions, remaining
    # findings, search coverage, plateau counter, terminal reason). Stored on
    # the session so it resumes with the revision/fingerprint it describes.
    convergence: dict[str, Any] | None = None
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

    def pending_marker(self) -> dict[str, Any]:
        """Domain-specific identity of the pending sub-decision, if any.

        The session stores it opaquely so a caller-scoped view (for example
        the legacy shared-host/arena projections) can recover the exact
        ``{"registration", "age_group"}`` / ``{"key"}`` marker without the
        lifecycle layer owning any of those domain shapes.
        """
        if not self.pending_decision:
            return {}
        marker = self.pending_decision.get("marker")
        return dict(marker) if isinstance(marker, dict) else {}

    def is_finalized(self) -> bool:
        return self.status == STATUS_FINALIZED

    def legal_transitions(self) -> list[str]:
        """Return the transition types valid from the current session state."""
        if self.is_finalized():
            # A finalized unpromoted candidate may be reopened for refinement
            # (new revision over the exact reviewed candidate) instead of
            # requiring promotion, a Stage 3 reset or a Stage 1/2 rerun.
            return [TRANSITION_REFINE_CANDIDATE]
        if self.status == STATUS_REFINING:
            return [
                TRANSITION_APPLY_REPAIR,
                TRANSITION_RUN_SEARCH,
                TRANSITION_SELECT_CANDIDATE,
                TRANSITION_KEEP_BASELINE,
            ]
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

    # -- retained verified-attempt portfolio ------------------------------

    def retain_candidate_attempt(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Add/refresh one verified candidate attempt in the bounded portfolio.

        Identity is the stable ``candidate_ref``; re-recording the same ref
        refreshes its evidence instead of duplicating it. The most recent
        :data:`RETAINED_ATTEMPT_LIMIT` entries are kept. The current
        candidate's own entry is never dropped to make room for a different
        one.
        """
        ref = str(record.get("candidate_ref") or "")
        if not ref:
            return {}
        entry = dict(record)
        entry["candidate_ref"] = ref
        self.candidate_attempts = [
            item for item in self.candidate_attempts if str(item.get("candidate_ref") or "") != ref
        ]
        self.candidate_attempts.append(entry)
        if len(self.candidate_attempts) > RETAINED_ATTEMPT_LIMIT:
            current_ref = self._current_attempt_ref()
            retained = self.candidate_attempts[-RETAINED_ATTEMPT_LIMIT:]
            if current_ref and not any(
                str(item.get("candidate_ref") or "") == current_ref for item in retained
            ):
                current = next(
                    (
                        item
                        for item in self.candidate_attempts
                        if str(item.get("candidate_ref") or "") == current_ref
                    ),
                    None,
                )
                if current is not None:
                    retained = [current, *retained[1:]]
            self.candidate_attempts = retained
        return entry

    def _current_attempt_ref(self) -> str:
        fingerprint = self.candidate_fingerprint
        for item in self.candidate_attempts:
            if str(item.get("candidate_fingerprint") or "") == fingerprint:
                return str(item.get("candidate_ref") or "")
        return ""

    def find_candidate_attempt(self, candidate_ref: str) -> dict[str, Any] | None:
        ref = str(candidate_ref or "")
        if not ref:
            return None
        return next(
            (
                item
                for item in self.candidate_attempts
                if str(item.get("candidate_ref") or "") == ref
            ),
            None,
        )

    def retained_candidate_refs(self) -> list[str]:
        return [str(item.get("candidate_ref") or "") for item in self.candidate_attempts]

    def retained_candidate_view(self) -> list[dict[str, Any]]:
        """Concise, body-free projection of the retained portfolio."""
        view: list[dict[str, Any]] = []
        for item in self.candidate_attempts:
            view.append(
                {
                    key: value
                    for key, value in item.items()
                    if key != "candidate"
                }
            )
        return view

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
        self.clear_pending()
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
        marker: Mapping[str, Any] | None = None,
    ) -> None:
        resolved_scope = scope or (
            SCOPE_RUN if capability in _RUN_SCOPED_CAPABILITIES else SCOPE_CANDIDATE
        )
        # A candidate-scoped context that carries its own candidate
        # fingerprint (arena/repair facts) binds to *that* exact candidate,
        # not to whatever the session's adopted baseline happens to be. This
        # keeps ``pending_decision.candidate_fingerprint`` identical to the
        # candidate the capability will mutate.
        fact_fingerprint = ""
        if isinstance(context, Mapping):
            facts = context.get("facts")
            if isinstance(facts, Mapping):
                fact_fingerprint = str(facts.get("candidate_fingerprint") or "")
        pending_fingerprint = (
            fact_fingerprint
            if resolved_scope == SCOPE_CANDIDATE and fact_fingerprint
            else self.candidate_fingerprint
        )
        # A fresh pending decision supersedes any operator pause it is a
        # continuation of.
        self.operator_request = None
        self.pending_decision = {
            "capability": capability,
            "scope": resolved_scope,
            "candidate_revision": self.candidate_revision if resolved_scope == SCOPE_CANDIDATE else None,
            "candidate_fingerprint": pending_fingerprint,
            "context": dict(context),
            "candidates": list(candidates or []),
            "attempt": attempt,
            "search_exhausted": search_exhausted,
            "marker": dict(marker or {}),
        }
        self.status = _CAPABILITY_STATUS.get(capability, STATUS_AWAITING_ADOPTION)

    def raise_operator_request(
        self,
        *,
        question: str,
        rationale: str,
        at: str,
        capability: str = "",
    ) -> dict[str, Any]:
        """Pause for a human/operator answer without changing candidate selection.

        This is the explicit ``request_operator`` transition for a
        candidate-scoped decision: it preserves the current candidate
        revision/fingerprint and the pending decision (so the operator answer
        still targets the same revision) and only records that the session is
        waiting for an operator question to be answered.
        """
        request = {
            "question": str(question),
            "rationale": str(rationale),
            "capability": str(capability or (self.pending_decision or {}).get("capability") or ""),
            "candidate_revision": self.candidate_revision,
            "candidate_fingerprint": self.candidate_fingerprint,
            "at": at,
        }
        self.operator_request = request
        self.status = STATUS_AWAITING_OPERATOR
        self.record_history(
            transition=TRANSITION_REQUEST_OPERATOR,
            action_id="request_operator",
            rationale=str(rationale),
            from_revision=self.candidate_revision,
            to_revision=self.candidate_revision,
            at=at,
            extra={"detail": {"question": str(question), "awaiting_operator": True}},
        )
        return request

    # -- continuation / progress evidence --------------------------------

    def record_search_attempt(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Fold one emitted attempt into the concise search history."""
        from .stage3_progress import upsert_attempt

        entry = dict(record)
        self.search_attempts = upsert_attempt(self.search_attempts, entry)
        return entry

    def latest_hard_violations(self) -> int | None:
        for item in reversed(self.search_attempts):
            if item.get("hard_violations") is not None:
                return int(item["hard_violations"])
        return None

    def search_history(self) -> dict[str, Any]:
        from .stage3_progress import build_search_history

        return build_search_history(self.search_attempts)

    def circuit_breaker_tripped(self) -> bool:
        return bool(self.search_history().get("circuit_breaker_tripped"))

    def clear_pending(self) -> None:
        """Drop the pending decision and recompute the non-pending status.

        A session without a pending interaction is either brand new (no
        candidate) or waiting for adoption of an already-built candidate; it
        must never stay stuck reporting the cleared sub-decision's status.
        """
        self.pending_decision = None
        self.operator_request = None
        if self.candidate is None:
            self.status = STATUS_NEW
        else:
            self.status = STATUS_AWAITING_ADOPTION

    def finalize(self, *, transition: str, action_id: str, rationale: str, at: str) -> None:
        self.finalized_revision = self.candidate_revision
        self.finalized_fingerprint = self.candidate_fingerprint
        self.status = STATUS_FINALIZED
        self.pending_decision = None
        self.operator_request = None
        self.record_history(
            transition=transition,
            action_id=action_id,
            rationale=rationale,
            from_revision=self.candidate_revision,
            to_revision=self.candidate_revision,
            at=at,
        )

    def begin_refinement(
        self,
        *,
        export_provenance: Mapping[str, Any] | None,
        rationale: str,
        at: str,
        action_id: str = TRANSITION_REFINE_CANDIDATE,
    ) -> dict[str, Any]:
        """Reopen a finalized unpromoted candidate for in-place refinement.

        The exact reviewed candidate stays the session's current candidate and
        becomes the baseline ``keep_baseline`` restores, so refinement always
        starts from what was reviewed (never from a rebuilt/re-scraped
        baseline). Stage 1/2 evidence is untouched; nothing is promoted; this
        is not the recovery-only Stage 3 reset.
        """
        if not self.is_finalized():
            raise ValueError("Only a finalized candidate can be refined")
        if self.candidate is None:
            raise ValueError("The finalized session carries no candidate to refine")
        self.refinement = {
            "origin_finalized_revision": self.finalized_revision
            if self.finalized_revision is not None
            else self.candidate_revision,
            "origin_finalized_fingerprint": self.finalized_fingerprint
            or self.candidate_fingerprint,
            "origin_export": dict(export_provenance) if export_provenance else None,
            "started_at": at,
            "revision": self.candidate_revision,
        }
        self.baseline_candidate = self.candidate
        self.baseline_fingerprint = self.candidate_fingerprint
        self.baseline_revision = self.candidate_revision
        self.finalized_revision = None
        self.finalized_fingerprint = None
        self.status = STATUS_REFINING
        self.record_history(
            transition=TRANSITION_REFINE_CANDIDATE,
            action_id=action_id,
            rationale=rationale,
            from_revision=self.candidate_revision,
            to_revision=self.candidate_revision,
            at=at,
            extra={"detail": dict(self.refinement)},
        )
        return dict(self.refinement)

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
            "baseline_candidate": self.baseline_candidate,
            "baseline_fingerprint": self.baseline_fingerprint,
            "baseline_revision": self.baseline_revision,
            "shared_host_decisions": list(self.shared_host_decisions),
            "arena_decisions": list(self.arena_decisions),
            "shared_host_unresolved": list(self.shared_host_unresolved),
            "arena_unresolved": list(self.arena_unresolved),
            "pending_decision": self.pending_decision,
            "operator_request": self.operator_request,
            "refinement": dict(self.refinement) if self.refinement else None,
            "decision_history": list(self.decision_history),
            "attempts": dict(self.attempts),
            "candidate_attempts": [dict(item) for item in self.candidate_attempts],
            "search_attempts": [dict(item) for item in self.search_attempts],
            "pareto_archive": [dict(item) for item in self.pareto_archive],
            "convergence": dict(self.convergence) if self.convergence else None,
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
        shared_unresolved = [dict(item) for item in (data.get("shared_host_unresolved") or [])]
        arena_unresolved = [dict(item) for item in (data.get("arena_unresolved") or [])]
        legacy_unresolved = [dict(item) for item in (data.get("unresolved") or [])]
        if legacy_unresolved and not (shared_unresolved or arena_unresolved):
            # Schema v1 stored one combined list. Split it back by the shape
            # of each entry so an old session keeps its unresolved asks.
            for item in legacy_unresolved:
                if "registration" in item:
                    shared_unresolved.append(item)
                else:
                    arena_unresolved.append(item)
        return cls(
            run_id=str(data.get("run_id") or ""),
            schema_version=STAGE3_SESSION_SCHEMA_VERSION,
            status=str(data.get("status") or STATUS_NEW),
            candidate_revision=int(data.get("candidate_revision") or 0),
            candidate_fingerprint=str(data.get("candidate_fingerprint") or ""),
            candidate=dict(data["candidate"]) if isinstance(data.get("candidate"), dict) else None,
            candidate_source=str(data.get("candidate_source") or ""),
            baseline_candidate=(
                dict(data["baseline_candidate"]) if isinstance(data.get("baseline_candidate"), dict) else None
            ),
            baseline_fingerprint=str(data.get("baseline_fingerprint") or ""),
            baseline_revision=(
                int(data["baseline_revision"]) if data.get("baseline_revision") is not None else None
            ),
            shared_host_decisions=[dict(item) for item in (data.get("shared_host_decisions") or [])],
            arena_decisions=[dict(item) for item in (data.get("arena_decisions") or [])],
            shared_host_unresolved=shared_unresolved,
            arena_unresolved=arena_unresolved,
            pending_decision=dict(data["pending_decision"]) if isinstance(data.get("pending_decision"), dict) else None,
            operator_request=(
                dict(data["operator_request"]) if isinstance(data.get("operator_request"), dict) else None
            ),
            refinement=(
                dict(data["refinement"]) if isinstance(data.get("refinement"), dict) else None
            ),
            decision_history=[dict(item) for item in (data.get("decision_history") or [])],
            attempts=dict(data.get("attempts") or {}),
            candidate_attempts=[dict(item) for item in (data.get("candidate_attempts") or [])],
            search_attempts=[dict(item) for item in (data.get("search_attempts") or [])],
            pareto_archive=[dict(item) for item in (data.get("pareto_archive") or [])],
            convergence=(
                dict(data["convergence"]) if isinstance(data.get("convergence"), dict) else None
            ),
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
