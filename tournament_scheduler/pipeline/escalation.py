"""Human escalation and approval protocol.

Defines the escalation types an AI operator capability can raise, and the
durable question/answer record stored in the run manifest's
``pending_questions`` list (see ``docs/run-manifest-schema.md``).

A question is identified by a stable id derived from
``(type, capability, summary)`` and, for any scope narrower than
``workspace``, the concrete scope key too (issue #12) — see
:class:`DecisionScope` below. So the exact same question, in the exact same
scope context, is never asked twice in a workspace: recording an answer
makes it a durable, auditable decision, and a later run that would raise the
identical question in the identical context finds it already answered
instead of blocking again. This is deliberately scoped to *identical*
questions — a genuinely different problem (different summary), or the same
problem in a context the scope says no longer applies, always gets its own id and is escalated normally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from .fingerprints import stable_payload_sha256
from .run_manifest import RunManifest

if TYPE_CHECKING:
    from .capability_result import CapabilityResult


class EscalationType(str, Enum):
    """Reasons an operator capability may need a human decision."""

    CREDENTIALS = "credentials"
    INCOMPLETE_DATA = "incomplete_data"
    AMBIGUOUS_POLICY = "ambiguous_policy"
    DESTRUCTIVE_REPAIR = "destructive_repair"
    IMPOSSIBLE_CONSTRAINTS = "impossible_constraints"
    EXTERNAL_PUBLICATION = "external_publication"
    AUDIT_REVIEW = "audit_review"


_VALID_TYPES = {t.value for t in EscalationType}


class DecisionScope(str, Enum):
    """How durable a decision (an answered escalation question) is (issue #12).

    Ordered from narrowest to broadest — :func:`scope_order` reflects this
    ordering, used to validate that promotion only ever broadens a decision:

    ``run``
        Valid only for the run that raised it (``scope_key`` = ``run_id``).
        Example: "the plan needs one fewer weekend this run because a rink
        is closed for maintenance" — a fact about *this* run, not a policy.
    ``input_version``
        Valid only for the exact workbook that raised it (``scope_key`` =
        the input fingerprint's sha256). Example: "U12 has too few teams to
        fill its bracket" — true for this workbook, stale the moment the
        organizer uploads a corrected one.
    ``season``
        Valid for a whole season regardless of which workbook or run raised
        it (``scope_key`` = a caller-supplied season identifier — there is
        no single generic notion of "season" elsewhere in the pipeline, so
        callers must supply their own, e.g. a season/year label). Example:
        "skip the Christmas week for every tournament this season."
    ``workspace``
        Valid indefinitely, independent of run/input/season (``scope_key``
        is always empty — this is the whole point of the scope). This is
        the default and matches every question raised before issue #12
        existed. Example: "Kongsberg ishall always needs LLM-based scraping,
        not the standard parser" — a standing fact about that source.
    """

    RUN = "run"
    INPUT_VERSION = "input_version"
    SEASON = "season"
    WORKSPACE = "workspace"


_VALID_SCOPES = {s.value for s in DecisionScope}
_SCOPE_ORDER = {
    DecisionScope.RUN.value: 0,
    DecisionScope.INPUT_VERSION.value: 1,
    DecisionScope.SEASON.value: 2,
    DecisionScope.WORKSPACE.value: 3,
}


def scope_order(scope: str) -> int:
    """Return *scope*'s position from narrowest (0) to broadest (3)."""
    if scope not in _SCOPE_ORDER:
        raise ValueError(f"Invalid decision scope {scope!r}. Valid values: {', '.join(sorted(_VALID_SCOPES))}")
    return _SCOPE_ORDER[scope]


def scope_key_for(work_dir: str, scope: str) -> str:
    """Resolve the concrete scope key for *scope* from current workspace state.

    - ``workspace``: always ``""`` — deliberately independent of context.
    - ``run``: the active run's ``run_id`` from the run manifest.
    - ``input_version``: the active run's input fingerprint sha256.
    - ``season``: not derivable generically (no single "season identifier"
      exists elsewhere in the pipeline yet) — raises ``ValueError``; callers
      that want season scope must compute and pass their own key.
    """
    if scope == DecisionScope.WORKSPACE.value:
        return ""
    manifest = RunManifest(work_dir).read()
    if scope == DecisionScope.RUN.value:
        return str(manifest.get("run_id") or "")
    if scope == DecisionScope.INPUT_VERSION.value:
        return str((manifest.get("input_fingerprint") or {}).get("sha256") or "")
    if scope == DecisionScope.SEASON.value:
        raise ValueError(
            "season scope has no generic key — pass scope_key explicitly "
            "(e.g. a season/year label) when raising a season-scoped question"
        )
    raise ValueError(f"Invalid decision scope {scope!r}. Valid values: {', '.join(sorted(_VALID_SCOPES))}")


def _question_id(
    escalation_type: str, capability: str, summary: str, scope: str = "workspace", scope_key: str = ""
) -> str:
    payload: dict[str, Any] = {"type": escalation_type, "capability": capability, "summary": summary}
    # workspace is the pre-#12 default: leave its id computation untouched
    # (no scope/scope_key in the payload) so every question raised before
    # decision scoping existed keeps resolving to the same id.
    if scope != DecisionScope.WORKSPACE.value:
        payload["scope"] = scope
        payload["scope_key"] = scope_key
    return stable_payload_sha256(payload)[:16]


@dataclass
class Question:
    """A single escalation, with enough structure to answer it without
    re-deriving context: what happened, what could be done about it, what
    the operator recommends, and what's at stake either way.
    """

    type: str
    capability: str
    summary: str
    context: str = ""
    alternatives: list[str] = field(default_factory=list)
    recommendation: str = ""
    impact: str = ""
    scope: str = DecisionScope.WORKSPACE.value
    scope_key: str = ""

    def __post_init__(self) -> None:
        type_value = self.type.value if isinstance(self.type, EscalationType) else str(self.type)
        if type_value not in _VALID_TYPES:
            raise ValueError(
                f"Invalid escalation type {type_value!r}. Valid values: {', '.join(sorted(_VALID_TYPES))}"
            )
        self.type = type_value
        scope_value = self.scope.value if isinstance(self.scope, DecisionScope) else str(self.scope)
        if scope_value not in _VALID_SCOPES:
            raise ValueError(
                f"Invalid decision scope {scope_value!r}. Valid values: {', '.join(sorted(_VALID_SCOPES))}"
            )
        self.scope = scope_value

    @property
    def id(self) -> str:
        return _question_id(self.type, self.capability, self.summary, self.scope, self.scope_key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "capability": self.capability,
            "summary": self.summary,
            "context": self.context,
            "alternatives": list(self.alternatives),
            "recommendation": self.recommendation,
            "impact": self.impact,
            "scope": self.scope,
            "scope_key": self.scope_key,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "answered": False,
            "answer": None,
            "decided_by": None,
            "decided_at": None,
            "stale": False,
            "stale_reason": None,
            "promoted_from": None,
            "promoted_at": None,
        }


def raise_question(work_dir: str, question: Question) -> dict[str, Any]:
    """Record *question* in the run manifest, deduplicated by stable id.

    The id already bakes in ``(scope, scope_key)`` for anything narrower
    than ``workspace`` scope (see :func:`_question_id`), so a context change
    — a new run, a new workbook, a new season — naturally produces a new id
    rather than silently reusing an old answer. When that happens, any
    still-relevant older entry for the same ``(type, capability, summary,
    scope)`` is marked ``stale`` (see ``RunManifest.add_pending_question``)
    instead of being overwritten or deleted, preserving its audit history.

    Returns the stored entry — either the newly added question, or the
    existing entry (answered or not) when this exact question, in this
    exact scope context, was already raised in this workspace.
    """
    return RunManifest(work_dir).add_pending_question(question.to_dict())


def answer_question(
    work_dir: str, question_id: str, answer: str, *, decided_by: str | None = None
) -> dict[str, Any]:
    """Record a durable human answer to a previously-raised question."""
    return RunManifest(work_dir).answer_question(question_id, answer, decided_by=decided_by)


def unanswered_questions(work_dir: str) -> list[dict[str, Any]]:
    """Return every pending (not yet answered) question for this workspace."""
    return RunManifest(work_dir).unanswered_questions()


def all_questions(work_dir: str) -> list[dict[str, Any]]:
    """Return every question ever raised in this workspace, including
    answered and stale ones — the full audit trail (issue #12)."""
    return RunManifest(work_dir).all_questions()


def promote_question(
    work_dir: str,
    question_id: str,
    new_scope: str,
    *,
    new_scope_key: str = "",
    decided_by: str | None = None,
) -> dict[str, Any]:
    """Promote an existing decision to a broader scope (issue #12).

    Copies *question_id*'s content and answer into a new entry under
    *new_scope* (which must be strictly broader than the source entry's
    current scope — see :func:`scope_order`), so it becomes reusable in
    contexts the original scope wouldn't have covered. The source entry is
    left untouched (still auditable at its original scope) and gains a
    ``promoted_to`` pointer to the new entry.

    Promoting to ``workspace`` needs no *new_scope_key* (workspace's key is
    always empty). Promoting to ``season`` requires one, since there is no
    generic season identifier to derive it from automatically.
    """
    return RunManifest(work_dir).promote_question(
        question_id, new_scope, new_scope_key=new_scope_key, decided_by=decided_by
    )


_CREDENTIAL_MARKERS = ("legitimasjon", "credential", "miljøvariable")
_IMPOSSIBLE_MARKERS = ("kollisjon", "ingen gyldig plan", "0 turneringer", "klarte ikke")
_POLICY_MARKERS = ("godkjenning", "approval")


def infer_escalation_type(result: "CapabilityResult") -> str:
    """Best-effort classification of *why* a blocked capability needs a human.

    A coarse keyword heuristic over the capability's own problems/suggested
    actions, not a hard rule — capabilities that know their own escalation
    reason should pass an explicit :class:`EscalationType` to
    :func:`from_capability_result` instead of relying on this.
    """
    text = " ".join(result.problems + result.suggested_actions).lower()
    if any(marker in text for marker in _CREDENTIAL_MARKERS):
        return EscalationType.CREDENTIALS.value
    if any(marker in text for marker in _IMPOSSIBLE_MARKERS):
        return EscalationType.IMPOSSIBLE_CONSTRAINTS.value
    if any(marker in text for marker in _POLICY_MARKERS):
        return EscalationType.AMBIGUOUS_POLICY.value
    return EscalationType.INCOMPLETE_DATA.value


def from_capability_result(
    result: "CapabilityResult",
    *,
    escalation_type: str | None = None,
    impact: str = "",
    scope: str = DecisionScope.WORKSPACE.value,
    scope_key: str = "",
) -> Question:
    """Build a :class:`Question` from a blocked :class:`CapabilityResult`.

    Reuses fields the capability already populated: ``evidence`` becomes
    context, ``suggested_actions`` become alternatives (the first one doubles
    as the recommendation), and ``problems`` become the impact when *impact*
    is not given explicitly. This lets most blocked capabilities raise a
    well-formed question without writing bespoke escalation code — only
    capabilities with a genuinely ambiguous cause need to pass an explicit
    *escalation_type*.

    *scope* defaults to ``workspace`` — the pre-#12 behavior of a durable,
    context-independent decision — so existing call sites are unaffected
    unless they opt into a narrower scope explicitly.
    """
    resolved_type = escalation_type or infer_escalation_type(result)
    alternatives = list(result.suggested_actions)
    return Question(
        type=resolved_type,
        capability=result.capability or "unknown",
        summary=result.summary,
        context="; ".join(result.evidence) if result.evidence else result.summary,
        alternatives=alternatives,
        recommendation=alternatives[0] if alternatives else "",
        impact=impact or "; ".join(result.problems),
        scope=scope,
        scope_key=scope_key,
    )
