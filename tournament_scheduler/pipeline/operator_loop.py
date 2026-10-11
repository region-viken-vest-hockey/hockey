"""The observe-decide-act AI operator loop (issue #11).

Turns calendar-source recovery from something a human manually chooses
pipeline commands for into a bounded, deterministic loop:

    observe (source_health, issue #3)
        -> decide (a pure policy function, no LLM required)
            -> act (dispatch through the #10 action registry)
                -> evaluate (re-observe)
                    -> continue, retry, escalate, or finish

Every step is recorded in the run manifest's ``action_log`` (target,
action_id, arguments, rationale, policy_rule, result, transition) via
:meth:`RunManifest.record_action_transition`, so a run can resume safely
after interruption using recorded operator state.

The policy (:func:`decide_action_for_source`) is a plain function — pure,
synchronous, no network or LLM call — so it's fully unit-testable and never
requires an LLM provider to run. An LLM harness may *propose* an action
instead of using the default policy via the ``action_proposer`` parameter,
but a proposal that isn't a known registry action id is rejected and the
loop falls back to the deterministic policy — the harness can influence
*which* registered action runs, never invent a new one.

This loop does not replace or duplicate Stage 2 scraping itself: it only
repairs the unified cache (via the same actions available through
``rvv-miniputt``) and escalates what it can't fix, then defers to the
existing pipeline to re-run Stage 2 and pick up the repaired cache.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .capability_result import CapabilityResult
from .escalation import Question, infer_escalation_type, raise_question
from .operator_action import ActionRegistry, ApprovalRequiredError, DEFAULT_REGISTRY
from .run_manifest import RunManifest
from .source_health import compute_source_health

# ---------------------------------------------------------------------------
# Bounds — the "does not loop indefinitely" guarantee
# ---------------------------------------------------------------------------

DEFAULT_MAX_RETRIES_PER_SOURCE = 2
DEFAULT_MAX_ACTIONS = 10

ActionProposer = Callable[[CapabilityResult, int], Optional[str]]


def _source_name(health: CapabilityResult) -> str:
    capability = health.capability or ""
    return capability.split(":", 1)[-1] if ":" in capability else capability


# ---------------------------------------------------------------------------
# Policy: observe -> decide (pure, deterministic, no LLM required)
# ---------------------------------------------------------------------------


def decide_action_for_source(
    health: CapabilityResult, *, attempt: int, max_retries: int
) -> tuple[str | None, str]:
    """Return ``(action_id, policy_rule)`` for one source's health result.

    ``action_id`` is ``None`` when no automatic action applies — either the
    source is already healthy, or retries are exhausted and the caller
    should escalate instead. ``policy_rule`` is a stable, human-and-agent
    readable identifier for *why*, independent of prose.
    """
    if health.status == "ok":
        return None, "ok->noop"

    if health.status == "blocked":
        if infer_escalation_type(health) == "credentials":
            return "request_credentials", "blocked+credentials->request_credentials"
        if attempt >= max_retries:
            return None, "blocked+retries_exhausted->escalate"
        action_id = "retry_source" if attempt == 0 else "refresh_source"
        return action_id, f"blocked+attempt_{attempt}->{action_id}"

    if health.status == "warning":
        if attempt >= max_retries:
            return "use_trusted_cache", "warning+retries_exhausted->use_trusted_cache"
        if attempt == 0:
            return "refresh_source", "warning+attempt_0->refresh_source"
        return "use_trusted_cache", f"warning+attempt_{attempt}->use_trusted_cache"

    # "failed" or any future status: nothing automatic is safe to try.
    return None, f"{health.status}->escalate"


def _credential_env_vars(source_name: str) -> list[str]:
    """Best-effort lookup of the env vars a blocked source's strategy needs."""
    try:
        from ..clubs.registry import club_for_source_name
        from .scraper_strategies import get_strategy, requires_credentials
    except ImportError:
        return []
    strategy = get_strategy(club_for_source_name(source_name) or source_name)
    if strategy is not None and requires_credentials(strategy):
        return list(strategy.credential_env_vars)
    return []


def _escalate_unresolved_source(work_dir: str, health: CapabilityResult) -> None:
    """Raise a generic escalation question for a source the loop gave up on."""
    source_name = _source_name(health)
    question = Question(
        type=infer_escalation_type(health),
        capability="source_health",
        summary=health.summary or f"{source_name}: kan ikke gjenopprettes automatisk",
        context="; ".join(health.evidence) if health.evidence else health.summary,
        alternatives=list(health.suggested_actions),
        recommendation=health.suggested_actions[0] if health.suggested_actions else "",
        impact="; ".join(health.problems) if health.problems else "Kilden forblir utilgjengelig.",
    )
    raise_question(work_dir, question)


# ---------------------------------------------------------------------------
# The loop: decide -> act -> evaluate -> continue/retry/escalate/finish
# ---------------------------------------------------------------------------


def run_source_recovery_loop(
    work_dir: str,
    *,
    max_retries_per_source: int = DEFAULT_MAX_RETRIES_PER_SOURCE,
    max_actions: int = DEFAULT_MAX_ACTIONS,
    action_proposer: ActionProposer | None = None,
    registry: ActionRegistry = DEFAULT_REGISTRY,
) -> dict[str, Any]:
    """Run the bounded observe-decide-act loop for calendar source recovery.

    A no-op (returns immediately with ``actions_taken: 0``) when Stage 2
    hasn't produced a checkpoint yet, or every configured source is already
    healthy — this is meant to run unconditionally at the top of
    ``operator run`` without needing its own precondition check.

    Returns a summary dict with ``actions_taken``, ``sources_resolved``,
    ``sources_escalated``, and ``stopped_reason`` (one of ``"completed"``,
    the loop ran out of sources needing attention; or
    ``"max_actions_reached"``, the global bound tripped first).
    """
    manifest = RunManifest(work_dir)
    attempts: dict[str, int] = {}
    # The most recent action result per source. Preferred over a fresh
    # compute_source_health() observation once a source has been acted on
    # this run: an action's own result is immediately authoritative, while
    # compute_source_health() reads the Stage 2 checkpoint, which an action
    # like retry_source doesn't rewrite itself — only an actual Stage 2
    # rerun does.
    last_result: dict[str, CapabilityResult] = {}
    resolved: list[str] = []
    escalated: list[str] = []
    actions_taken = 0
    stopped_reason = "completed"

    while True:
        if actions_taken >= max_actions:
            stopped_reason = "max_actions_reached"
            break

        health_results = compute_source_health(work_dir)
        pending = [
            h
            for h in health_results
            if h.status != "ok"
            and _source_name(h) not in resolved
            and _source_name(h) not in escalated
        ]
        if not pending:
            break

        health = pending[0]
        source_name = _source_name(health)
        if source_name in last_result:
            health = last_result[source_name]
        attempt = attempts.get(source_name, 0)

        action_id, policy_rule = decide_action_for_source(
            health, attempt=attempt, max_retries=max_retries_per_source
        )

        if action_proposer is not None:
            proposed = action_proposer(health, attempt)
            if proposed is not None and proposed in registry.known_action_ids():
                action_id, policy_rule = proposed, f"proposed->{proposed}"

        if action_id is None:
            _escalate_unresolved_source(work_dir, health)
            escalated.append(source_name)
            manifest.record_action_transition(
                target=source_name,
                action_id="",
                arguments={},
                rationale=f"No safe automatic action for status={health.status}.",
                policy_rule=policy_rule,
                result=health,
                transition="escalate",
            )
            continue

        arguments: dict[str, Any] = {"work_dir": work_dir, "source_name": source_name}
        if action_id == "request_credentials":
            arguments["env_vars"] = _credential_env_vars(source_name)

        action = registry.build(action_id, **arguments)
        try:
            result = registry.execute(action)
        except ApprovalRequiredError:
            # None of this loop's policy-selected actions require approval —
            # a custom action_proposer could still name one. Treat that as
            # "can't act automatically" rather than crashing the loop.
            _escalate_unresolved_source(work_dir, health)
            escalated.append(source_name)
            manifest.record_action_transition(
                target=source_name,
                action_id=action_id,
                arguments=action.arguments,
                rationale="Proposed action requires human approval; not auto-executed.",
                policy_rule=policy_rule,
                result=health,
                transition="escalate",
            )
            continue

        actions_taken += 1
        attempts[source_name] = attempt + 1
        previous_result = last_result.get(source_name)

        if action_id == "request_credentials":
            # This action's entire purpose is to escalate — it always
            # returns "blocked" by design, so it must never be read as "try
            # again", or the loop would just re-raise the same (deduplicated,
            # so harmless but pointless) question every remaining attempt.
            transition = "escalate"
            escalated.append(source_name)
        elif result.status == "ok":
            transition = "resolved"
            resolved.append(source_name)
        elif previous_result is not None and (
            previous_result.status,
            previous_result.summary,
            tuple(previous_result.problems),
        ) == (result.status, result.summary, tuple(result.problems)):
            # No-progress detection: the action produced an identical result
            # to the previous attempt on this source — stop retrying and
            # escalate instead of burning through the remaining budget.
            _escalate_unresolved_source(work_dir, result)
            transition = "no_progress_stop"
            escalated.append(source_name)
        else:
            transition = "retry"

        last_result[source_name] = result
        manifest.record_action_transition(
            target=source_name,
            action_id=action_id,
            arguments=action.arguments,
            rationale=f"status={health.status} attempt={attempt}",
            policy_rule=policy_rule,
            result=result,
            transition=transition,
        )

    return {
        "actions_taken": actions_taken,
        "sources_resolved": resolved,
        "sources_escalated": escalated,
        "stopped_reason": stopped_reason,
    }
