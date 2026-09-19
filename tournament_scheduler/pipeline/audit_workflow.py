"""Typed persisted audit/convergence workflow state for a reviewed export.

The semantic safety-net audit used to be a convention: a harness could read
``operator audit-context``, conclude ``REVIEW_REQUIRED`` in prose and then stop,
treating its own summary as a terminal run result. The automatic
``REVIEW_REQUIRED -> bounded convergence`` bridge only existed *after*
``operator audit-submit`` had been invoked, so lifecycle correctness depended on
the harness remembering to submit and then remembering to start another audit
cycle.

This module defines the missing explicit workflow state. It is a pure, typed
value object (the transitions/persistence are owned by the Stage 3 session
lifecycle in :mod:`tournament_scheduler.application.audit_lifecycle`):

    Stage 4 export -> AUDIT_REQUIRED
        -> audit verdict submitted
             PASS              -> COMPLETE (pass)
             REVIEW_REQUIRED   -> CONVERGENCE_REQUIRED
        -> bounded convergence
             internal revisions -> CONVERGENCE_REQUIRED (prior audit kept;
                                   no new Stage 4 handoff yet)
             batch export       -> AUDIT_REQUIRED (fresh export fingerprint)
             export owed        -> CONVERGENCE_REQUIRED (export_required;
                                   candidate persisted, not yet auditable)
             terminal reached   -> COMPLETE (pareto_stable /
                                            bounded_search_exhausted /
                                            operator_required) when no
                                            handoff is owed
             budget paused      -> CONVERGENCE_REQUIRED (resumable, not complete)

A run must not be considered complete while ``AUDIT_REQUIRED`` or
``CONVERGENCE_REQUIRED`` is pending, and a stale audit can never satisfy a newer
export. :func:`workflow_view` exposes the canonical next transition/command so
the harness never has to infer it from prose or from a boolean buried in a prior
command result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

PHASE_AUDIT_REQUIRED = "audit_required"
PHASE_CONVERGENCE_REQUIRED = "convergence_required"
PHASE_COMPLETE = "complete"

PENDING_PHASES = frozenset({PHASE_AUDIT_REQUIRED, PHASE_CONVERGENCE_REQUIRED})
ALL_PHASES = frozenset({PHASE_AUDIT_REQUIRED, PHASE_CONVERGENCE_REQUIRED, PHASE_COMPLETE})

# Terminal reasons a completed workflow may report. These mirror the bounded
# convergence terminal vocabulary and stay distinct: ``operator_required`` is
# not the same thing as a Pareto-stable automatic stop.
TERMINAL_PASS = "pass"
TERMINAL_OPERATOR_REQUIRED = "operator_required"
TERMINAL_BOUNDED_SEARCH_EXHAUSTED = "bounded_search_exhausted"
TERMINAL_PARETO_STABLE = "pareto_stable"
TERMINAL_REASONS = frozenset(
    {
        TERMINAL_PASS,
        TERMINAL_OPERATOR_REQUIRED,
        TERMINAL_BOUNDED_SEARCH_EXHAUSTED,
        TERMINAL_PARETO_STABLE,
    }
)

# Resumable pause reasons. A budget pause keeps the workflow pending in
# ``CONVERGENCE_REQUIRED``; it must never be recorded as a completed workflow.
PAUSE_BUDGET_EXHAUSTED = "budget_exhausted"
PAUSE_PAUSED = "paused"
PAUSE_REASONS = frozenset({PAUSE_BUDGET_EXHAUSTED, PAUSE_PAUSED})

# Bounded transition log; the workflow only needs enough provenance to explain
# how the current phase was reached, not an unbounded audit trail.
_MAX_TRANSITIONS = 20

_NEXT_STEP_TEXT = {
    PHASE_AUDIT_REQUIRED: (
        "Perform the semantic safety-net audit for the current export and submit the verdict "
        "with 'operator audit-submit'."
    ),
    PHASE_CONVERGENCE_REQUIRED: (
        "Continue the bounded Pareto convergence over the reviewed candidate "
        "('stage3 converge'); the batch materializes one Stage 4 review export that "
        "requires a fresh audit."
    ),
    PHASE_COMPLETE: "No further automatic transition is pending.",
}

_CANONICAL_SEASON_CONVERGENCE_STEP = (
    "The audited export is the promoted canonical season's own export, not an "
    "unpromoted Stage 3 candidate: 'stage3 converge' has no candidate to converge "
    "and must not be used here. Continue promoted-season maintenance with "
    "'season findings' -> 'season repair-options' / bounded 'season search' -> "
    "'season apply-repair' for one selected finding, then re-export and re-audit."
)


@dataclass
class AuditWorkflow:
    """Explicit lifecycle phase of the semantic audit/convergence outer loop.

    Stored on the Stage 3 session so it survives process boundaries and always
    describes the exact export fingerprint it refers to.
    """

    phase: str = ""
    export_fingerprint: str = ""
    export_dir: str = ""
    candidate_revision: int | None = None
    candidate_fingerprint: str = ""
    last_audit_status: str = ""
    terminal_reason: str = ""
    terminal_detail: str = ""
    updated_at: str = ""
    transitions: list[dict[str, Any]] = field(default_factory=list)
    # Non-empty exactly when the current export is the promoted canonical
    # season's own export (produced through `season export`), never a Stage 3
    # pipeline candidate -- even one that happened to adopt the same season as
    # its baseline. `season export` never touches the Stage3Session/candidate
    # store, so `candidate_revision`/`candidate_fingerprint` above describe a
    # *different*, unrelated run whenever this is set; a harness/command must
    # not resume/converge that candidate on this workflow's behalf.
    canonical_season: str = ""

    @property
    def is_pending(self) -> bool:
        return self.phase in PENDING_PHASES

    @property
    def is_complete(self) -> bool:
        return self.phase == PHASE_COMPLETE

    def record(
        self,
        phase: str,
        *,
        at: str = "",
        export_fingerprint: str | None = None,
        export_dir: str | None = None,
        candidate_revision: int | None = None,
        candidate_fingerprint: str | None = None,
        last_audit_status: str | None = None,
        canonical_season: str | None = None,
        terminal_reason: str = "",
        terminal_detail: str = "",
    ) -> "AuditWorkflow":
        """Move to *phase* and append a concise transition record in place."""
        self.phase = phase
        if export_fingerprint is not None:
            self.export_fingerprint = str(export_fingerprint)
        if export_dir is not None:
            self.export_dir = str(export_dir)
        if candidate_revision is not None:
            self.candidate_revision = candidate_revision
        if candidate_fingerprint is not None:
            self.candidate_fingerprint = str(candidate_fingerprint)
        if last_audit_status is not None:
            self.last_audit_status = str(last_audit_status)
        if canonical_season is not None:
            self.canonical_season = str(canonical_season)
        # A non-terminal phase never carries a stale terminal reason.
        if phase == PHASE_COMPLETE:
            self.terminal_reason = str(terminal_reason or self.terminal_reason)
            self.terminal_detail = str(terminal_detail or self.terminal_detail)
        else:
            self.terminal_reason = ""
            self.terminal_detail = ""
        self.updated_at = str(at or self.updated_at)
        self.transitions.append(
            {
                "phase": phase,
                "at": self.updated_at,
                "export_fingerprint": self.export_fingerprint,
                "candidate_fingerprint": self.candidate_fingerprint,
                "audit_status": self.last_audit_status,
                "terminal_reason": self.terminal_reason,
            }
        )
        if len(self.transitions) > _MAX_TRANSITIONS:
            self.transitions = self.transitions[-_MAX_TRANSITIONS:]
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "export_fingerprint": self.export_fingerprint,
            "export_dir": self.export_dir,
            "candidate_revision": self.candidate_revision,
            "candidate_fingerprint": self.candidate_fingerprint,
            "last_audit_status": self.last_audit_status,
            "terminal_reason": self.terminal_reason,
            "terminal_detail": self.terminal_detail,
            "updated_at": self.updated_at,
            "transitions": [dict(item) for item in self.transitions],
            "canonical_season": self.canonical_season,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "AuditWorkflow":
        if not data:
            return cls()
        revision = data.get("candidate_revision")
        return cls(
            phase=str(data.get("phase") or ""),
            export_fingerprint=str(data.get("export_fingerprint") or ""),
            export_dir=str(data.get("export_dir") or ""),
            candidate_revision=int(revision) if revision is not None else None,
            candidate_fingerprint=str(data.get("candidate_fingerprint") or ""),
            last_audit_status=str(data.get("last_audit_status") or ""),
            terminal_reason=str(data.get("terminal_reason") or ""),
            terminal_detail=str(data.get("terminal_detail") or ""),
            updated_at=str(data.get("updated_at") or ""),
            transitions=[dict(item) for item in (data.get("transitions") or []) if item],
            canonical_season=str(data.get("canonical_season") or ""),
        )


def next_command(phase: str, work_dir: Any, *, canonical_season: str = "") -> str | None:
    """Canonical repository command for the mandatory next transition.

    ``None`` for a completed workflow. The command is derived from the phase
    (and, for ``CONVERGENCE_REQUIRED``, whether the audited export is the
    promoted canonical season's own export) so it cannot drift from the state
    machine. A canonical-season-scoped export never has a Stage 3 candidate to
    converge, so it is routed to promoted-season maintenance instead of
    ``stage3 converge``.
    """
    work_dir = str(work_dir)
    if phase == PHASE_AUDIT_REQUIRED:
        return f"scripts/rvv-miniputt operator audit-context --work-dir {work_dir}"
    if phase == PHASE_CONVERGENCE_REQUIRED:
        if canonical_season:
            return f"scripts/rvv-miniputt season findings --season {canonical_season}"
        return f"scripts/rvv-miniputt stage3 converge --work-dir {work_dir} --json"
    return None


def workflow_view(
    workflow: "AuditWorkflow | Mapping[str, Any] | None", work_dir: Any
) -> dict[str, Any]:
    """Machine-readable projection a harness/operator can act on.

    Carries the phase, whether the run may be reported complete, the canonical
    next command, and the exact export/candidate identity the phase refers to.
    """
    resolved = (
        workflow
        if isinstance(workflow, AuditWorkflow)
        else AuditWorkflow.from_dict(workflow)
    )
    view: dict[str, Any] = {
        "phase": resolved.phase,
        "pending": resolved.is_pending,
        "complete": resolved.is_complete,
        "export_fingerprint": resolved.export_fingerprint,
        "export_dir": resolved.export_dir,
        "candidate_revision": resolved.candidate_revision,
        "candidate_fingerprint": resolved.candidate_fingerprint,
        "last_audit_status": resolved.last_audit_status,
        "terminal_reason": resolved.terminal_reason,
        "terminal_detail": resolved.terminal_detail,
        "updated_at": resolved.updated_at,
        "canonical_season": resolved.canonical_season,
        "next_command": next_command(
            resolved.phase, work_dir, canonical_season=resolved.canonical_season
        ),
        "next_step": (
            _CANONICAL_SEASON_CONVERGENCE_STEP
            if resolved.phase == PHASE_CONVERGENCE_REQUIRED and resolved.canonical_season
            else _NEXT_STEP_TEXT.get(resolved.phase, "")
        ),
    }
    return view


def completion_blockers(workflow: "AuditWorkflow | Mapping[str, Any] | None") -> list[str]:
    """Reasons the workflow may not be reported complete (empty when allowed)."""
    resolved = (
        workflow
        if isinstance(workflow, AuditWorkflow)
        else AuditWorkflow.from_dict(workflow)
    )
    if not resolved.phase:
        return ["No audit/convergence workflow has been started for the current export."]
    if resolved.phase == PHASE_AUDIT_REQUIRED:
        return ["A semantic audit is required for the current export."]
    if resolved.phase == PHASE_CONVERGENCE_REQUIRED:
        return ["Bounded convergence is required before the run can complete."]
    if resolved.phase == PHASE_COMPLETE and not resolved.terminal_reason:
        return ["The workflow is marked complete without a terminal reason."]
    return []


def terminal_is_distinct(reason: str) -> bool:
    """True for a recognized, distinct terminal reason."""
    return str(reason) in TERMINAL_REASONS


def is_pause_reason(reason: str) -> bool:
    """True for a resumable budget pause (never a completed workflow)."""
    return str(reason) in PAUSE_REASONS


def phase_transitions(workflow: "AuditWorkflow | None") -> Sequence[str]:
    return [str(item.get("phase") or "") for item in (workflow.transitions if workflow else [])]


__all__ = [
    "ALL_PHASES",
    "AuditWorkflow",
    "PAUSE_BUDGET_EXHAUSTED",
    "PAUSE_PAUSED",
    "PAUSE_REASONS",
    "PENDING_PHASES",
    "PHASE_AUDIT_REQUIRED",
    "PHASE_COMPLETE",
    "PHASE_CONVERGENCE_REQUIRED",
    "TERMINAL_BOUNDED_SEARCH_EXHAUSTED",
    "TERMINAL_OPERATOR_REQUIRED",
    "TERMINAL_PARETO_STABLE",
    "TERMINAL_PASS",
    "TERMINAL_REASONS",
    "completion_blockers",
    "is_pause_reason",
    "next_command",
    "phase_transitions",
    "terminal_is_distinct",
    "workflow_view",
]
