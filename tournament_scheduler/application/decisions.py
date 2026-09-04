"""Structured LLM-directed decision contract (issue #260 Phase 1).

This module implements the harness-neutral ``DecisionContext`` /
``DecisionAction`` / ``DecisionResult`` protocol described in
``docs/adr/0002-llm-directed-decision-ownership-and-thin-adapters.md``.

The pattern mirrors the existing observe-decide-act loop
(``pipeline.operator_action`` / ``RunManifest.record_action_transition``,
issue #11), but that loop selects a deterministic :class:`OperatorAction`
via a stable ``policy_rule``. This module is for the complementary case: an
LLM/agent controller choosing a small, capability-oriented action over
contextual soft judgment. Deterministic validation here is what stops an
LLM response from silently becoming a correctness or safety authority — an
unknown action, a missing required argument, a hard violation, or a
declared human-approval requirement are all rejected before anything can
execute, matching ADR 0002's ownership rules.

Modules in this package are callable in-process by CLI, desktop, harness,
and future browser adapters. They must not import transport/rendering
layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..pipeline.run_manifest import RunManifest

# ---------------------------------------------------------------------------
# Schema versions
# ---------------------------------------------------------------------------

DECISION_CONTEXT_SCHEMA_VERSION = 1
DECISION_ACTION_SCHEMA_VERSION = 1
DECISION_RESULT_SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Action vocabulary
# ---------------------------------------------------------------------------

# Small, capability-oriented vocabulary from ADR 0002 / issue #260. Keep this
# frozen and short — soft policy expands via .agents/skills/rvv/, not by
# growing this enum.
DECISION_ACTION_IDS: frozenset[str] = frozenset(
    {
        "proceed",
        "abort",
        "retry_stage",
        "recover_source",
        "optimize_plan",
        "apply_candidate",
        "keep_baseline",
        "request_operator",
        "present_for_review",
    }
)

# Required argument keys per action_id. Actions not listed require none.
_REQUIRED_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "retry_stage": ("stage",),
    "recover_source": ("source",),
    "apply_candidate": ("candidate_ref",),
    "request_operator": ("question",),
}

# Actions that may proceed even when the context carries human-approval
# requirements — everything else must route through a human gate first.
_HUMAN_APPROVAL_SAFE_ACTIONS: frozenset[str] = frozenset(
    {"abort", "keep_baseline", "request_operator", "present_for_review"}
)

# Actions that could move the run past a hard violation and are therefore
# blocked while any hard violation is outstanding.
_HARD_VIOLATION_BLOCKED_ACTIONS: frozenset[str] = frozenset({"proceed", "apply_candidate"})


# ---------------------------------------------------------------------------
# Structured errors
# ---------------------------------------------------------------------------


class DecisionActionError(ValueError):
    """Base class for deterministic decision-action validation failures.

    Every failure mode has a stable ``code`` so a caller (or another agent)
    can branch on it without parsing the message text.
    """

    code = "decision_action_error"

    def __init__(self, action_id: str, reason: str) -> None:
        self.action_id = action_id
        self.reason = reason
        super().__init__(f"{self.code}: {action_id}: {reason}")

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "action_id": self.action_id, "reason": self.reason}


class UnknownDecisionActionError(DecisionActionError):
    """Raised when ``action_id`` is not part of :data:`DECISION_ACTION_IDS`."""

    code = "unknown_decision_action"


class DecisionActionNotAvailableError(DecisionActionError):
    """Raised when ``action_id`` is valid but not offered by this context."""

    code = "decision_action_not_available"


class InvalidDecisionArgumentsError(DecisionActionError):
    """Raised when a required argument for ``action_id`` is missing."""

    code = "invalid_decision_arguments"


class HardViolationBlocksActionError(DecisionActionError):
    """Raised when an action would advance past an outstanding hard violation.

    An LLM action must not bypass a hard rule/publication gate through
    prose (ADR 0002, "Deterministic code owns hard rules"). ``abort``,
    ``retry_stage``, ``recover_source``, ``optimize_plan``,
    ``keep_baseline``, ``request_operator``, and ``present_for_review``
    remain available while violations are outstanding; ``proceed`` and
    ``apply_candidate`` do not.
    """

    code = "hard_violation_blocks_action"


class HumanApprovalRequiredError(DecisionActionError):
    """Raised when the context requires human approval and the action skips it.

    Only ``abort``, ``keep_baseline``, ``request_operator``, and
    ``present_for_review`` are permitted while
    :attr:`DecisionContext.requires_human_approval` is ``True``.
    """

    code = "decision_requires_human_approval"


# ---------------------------------------------------------------------------
# DecisionContext
# ---------------------------------------------------------------------------

_CONTEXT_FIELDS = {
    "schema_version",
    "run_id",
    "capability",
    "stage",
    "objective",
    "facts",
    "hard_violations",
    "warnings",
    "scorecard",
    "baseline_ref",
    "candidate_ref",
    "available_actions",
    "prior_results",
    "requires_human_approval",
}


@dataclass(frozen=True)
class DecisionContext:
    """Decision-relevant, non-secret state offered to an LLM/agent controller.

    Contains facts, hard violations, warnings, a deterministic scorecard,
    candidate/baseline references, and the validated actions available in
    this context — never raw secrets or transport-specific state.
    """

    run_id: str
    capability: str
    stage: str = ""
    objective: str = ""
    facts: Mapping[str, Any] = field(default_factory=dict)
    hard_violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    scorecard: Mapping[str, Any] = field(default_factory=dict)
    baseline_ref: str | None = None
    candidate_ref: str | None = None
    available_actions: tuple[str, ...] = ()
    prior_results: tuple[Mapping[str, Any], ...] = ()
    requires_human_approval: bool = False
    schema_version: int = DECISION_CONTEXT_SCHEMA_VERSION
    extra: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionContext":
        prior_results = data.get("prior_results") or []
        return cls(
            run_id=str(data.get("run_id") or ""),
            capability=str(data.get("capability") or ""),
            stage=str(data.get("stage") or ""),
            objective=str(data.get("objective") or ""),
            facts=dict(data.get("facts") or {}),
            hard_violations=tuple(str(item) for item in (data.get("hard_violations") or ())),
            warnings=tuple(str(item) for item in (data.get("warnings") or ())),
            scorecard=dict(data.get("scorecard") or {}),
            baseline_ref=_optional_str(data.get("baseline_ref")),
            candidate_ref=_optional_str(data.get("candidate_ref")),
            available_actions=tuple(str(item) for item in (data.get("available_actions") or ())),
            prior_results=tuple(dict(item) for item in prior_results),
            requires_human_approval=bool(data.get("requires_human_approval", False)),
            schema_version=int(data.get("schema_version") or DECISION_CONTEXT_SCHEMA_VERSION),
            extra={key: value for key, value in data.items() if key not in _CONTEXT_FIELDS},
        )

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.extra)
        payload.update(
            {
                "schema_version": self.schema_version,
                "run_id": self.run_id,
                "capability": self.capability,
                "stage": self.stage,
                "objective": self.objective,
                "facts": dict(self.facts),
                "hard_violations": list(self.hard_violations),
                "warnings": list(self.warnings),
                "scorecard": dict(self.scorecard),
                "baseline_ref": self.baseline_ref,
                "candidate_ref": self.candidate_ref,
                "available_actions": list(self.available_actions),
                "prior_results": [dict(item) for item in self.prior_results],
                "requires_human_approval": self.requires_human_approval,
            }
        )
        return payload


# ---------------------------------------------------------------------------
# DecisionAction
# ---------------------------------------------------------------------------

_ACTION_FIELDS = {"schema_version", "action_id", "target", "arguments", "rationale"}


@dataclass(frozen=True)
class DecisionAction:
    """A single action chosen by the LLM/agent controller.

    ``action_id`` must be one of :data:`DECISION_ACTION_IDS`. ``rationale``
    is a concise audit summary, never chain-of-thought.
    """

    action_id: str
    target: str = ""
    arguments: Mapping[str, Any] = field(default_factory=dict)
    rationale: str = ""
    schema_version: int = DECISION_ACTION_SCHEMA_VERSION
    extra: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionAction":
        return cls(
            action_id=str(data.get("action_id") or ""),
            target=str(data.get("target") or ""),
            arguments=dict(data.get("arguments") or {}),
            rationale=str(data.get("rationale") or ""),
            schema_version=int(data.get("schema_version") or DECISION_ACTION_SCHEMA_VERSION),
            extra={key: value for key, value in data.items() if key not in _ACTION_FIELDS},
        )

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.extra)
        payload.update(
            {
                "schema_version": self.schema_version,
                "action_id": self.action_id,
                "target": self.target,
                "arguments": dict(self.arguments),
                "rationale": self.rationale,
            }
        )
        return payload


def validate_decision_action(context: DecisionContext, action: DecisionAction) -> None:
    """Deterministically validate *action* against *context*.

    Raises a :class:`DecisionActionError` subclass on any invalid or
    unsafe action. Returns ``None`` when the action is valid.
    """
    if action.action_id not in DECISION_ACTION_IDS:
        raise UnknownDecisionActionError(
            action.action_id,
            f"not one of: {', '.join(sorted(DECISION_ACTION_IDS))}",
        )

    if context.available_actions and action.action_id not in context.available_actions:
        raise DecisionActionNotAvailableError(
            action.action_id,
            f"not offered by this context; available: {', '.join(context.available_actions)}",
        )

    missing = [
        name for name in _REQUIRED_ARGUMENTS.get(action.action_id, ()) if not action.arguments.get(name)
    ]
    if missing:
        raise InvalidDecisionArgumentsError(
            action.action_id,
            f"missing required argument(s): {', '.join(missing)}",
        )

    if context.hard_violations and action.action_id in _HARD_VIOLATION_BLOCKED_ACTIONS:
        raise HardViolationBlocksActionError(
            action.action_id,
            f"outstanding hard violations: {', '.join(context.hard_violations)}",
        )

    if context.requires_human_approval and action.action_id not in _HUMAN_APPROVAL_SAFE_ACTIONS:
        raise HumanApprovalRequiredError(
            action.action_id,
            "context requires human approval; only "
            f"{', '.join(sorted(_HUMAN_APPROVAL_SAFE_ACTIONS))} are permitted",
        )


# ---------------------------------------------------------------------------
# DecisionResult
# ---------------------------------------------------------------------------

_RESULT_FIELDS = {
    "schema_version",
    "accepted",
    "action_id",
    "target",
    "result_ref",
    "rationale",
    "changed_violations",
    "changed_warnings",
    "changed_metrics",
    "next_available_actions",
    "rejection_reason",
}


@dataclass(frozen=True)
class DecisionResult:
    """Operational audit summary of one applied (or rejected) decision.

    Only a concise rationale and the deterministic before/after facts are
    persisted — never private chain-of-thought.
    """

    accepted: bool
    action_id: str
    target: str = ""
    result_ref: str | None = None
    rationale: str = ""
    rejection_reason: str | None = None
    changed_violations: tuple[str, ...] = ()
    changed_warnings: tuple[str, ...] = ()
    changed_metrics: Mapping[str, Any] = field(default_factory=dict)
    next_available_actions: tuple[str, ...] = ()
    schema_version: int = DECISION_RESULT_SCHEMA_VERSION
    extra: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionResult":
        return cls(
            accepted=bool(data.get("accepted", False)),
            action_id=str(data.get("action_id") or ""),
            target=str(data.get("target") or ""),
            result_ref=_optional_str(data.get("result_ref")),
            rationale=str(data.get("rationale") or ""),
            rejection_reason=_optional_str(data.get("rejection_reason")),
            changed_violations=tuple(str(item) for item in (data.get("changed_violations") or ())),
            changed_warnings=tuple(str(item) for item in (data.get("changed_warnings") or ())),
            changed_metrics=dict(data.get("changed_metrics") or {}),
            next_available_actions=tuple(
                str(item) for item in (data.get("next_available_actions") or ())
            ),
            schema_version=int(data.get("schema_version") or DECISION_RESULT_SCHEMA_VERSION),
            extra={key: value for key, value in data.items() if key not in _RESULT_FIELDS},
        )

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.extra)
        payload.update(
            {
                "schema_version": self.schema_version,
                "accepted": self.accepted,
                "action_id": self.action_id,
                "target": self.target,
                "result_ref": self.result_ref,
                "rationale": self.rationale,
                "rejection_reason": self.rejection_reason,
                "changed_violations": list(self.changed_violations),
                "changed_warnings": list(self.changed_warnings),
                "changed_metrics": dict(self.changed_metrics),
                "next_available_actions": list(self.next_available_actions),
            }
        )
        return payload


# ---------------------------------------------------------------------------
# Use case: validate + record a decision into the run manifest
# ---------------------------------------------------------------------------


def decide(
    context: DecisionContext,
    action: DecisionAction,
    *,
    result_ref: str | None = None,
    changed_violations: Sequence[str] = (),
    changed_warnings: Sequence[str] = (),
    changed_metrics: Mapping[str, Any] | None = None,
    next_available_actions: Sequence[str] = (),
) -> DecisionResult:
    """Validate *action* against *context* and build the resulting :class:`DecisionResult`.

    Never raises: an invalid action produces a rejected ``DecisionResult``
    (``accepted=False``, ``rejection_reason`` set to the validator's error
    code) rather than propagating an exception, so callers can always
    record and audit the outcome. Use :func:`validate_decision_action`
    directly if you want the exception instead.
    """
    try:
        validate_decision_action(context, action)
    except DecisionActionError as exc:
        return DecisionResult(
            accepted=False,
            action_id=action.action_id,
            target=action.target,
            rationale=action.rationale,
            rejection_reason=exc.code,
            next_available_actions=tuple(context.available_actions),
        )
    return DecisionResult(
        accepted=True,
        action_id=action.action_id,
        target=action.target,
        result_ref=result_ref,
        rationale=action.rationale,
        changed_violations=tuple(changed_violations),
        changed_warnings=tuple(changed_warnings),
        changed_metrics=dict(changed_metrics or {}),
        next_available_actions=tuple(next_available_actions),
    )


def record_llm_decision(
    work_dir: str,
    context: DecisionContext,
    action: DecisionAction,
    result: DecisionResult,
) -> DecisionResult:
    """Persist one LLM-directed decision into the run manifest's ``decision_log``.

    Wraps :meth:`RunManifest.record_decision` so transports never touch
    manifest internals directly, matching the ``operator_state`` use-case
    pattern.
    """
    RunManifest(work_dir).record_decision(
        context=context.to_dict(),
        action=action.to_dict(),
        result=result.to_dict(),
    )
    return result


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
