"""Shared/joint-club hosting decision (issue #274).

A registration such as ``"Kongsberg/Tønsberg"`` has more than one legitimate
physical host for a given age group. Deciding *which* constituent should
carry a specific hosting obligation is a contextual fairness judgement --
the issue explicitly requires this to be an LLM/controller decision over
deterministic facts, never a fixed Python heuristic (e.g. "alphabetically
first with a free calendar slot").

This module builds the narrow :class:`~.application.decisions.DecisionContext`
for that single decision, following the same shape as
:mod:`tournament_scheduler.stage3_decision`. Python's job stops at:

- computing the facts (:func:`tournament_scheduler.hosting_coverage.shared_registration_facts`),
- constraining the choice to the registration's actual constituents (via the
  ``chosen_club`` argument's ``enum``, enforced deterministically by
  :func:`application.decisions.validate_decision_action` -- calendar
  unavailability never removes a constituent from that enum, so it can never
  bias the choice),
- persisting the resulting decision with provenance.

Nothing here hardcodes any specific pair of clubs.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from .application.decisions import DecisionAction, DecisionContext

SHARED_HOST_DECISION_ACTIONS: "tuple[str, ...]" = ("assign_shared_host", "request_operator")


def build_shared_host_decision_context(
    run_id: str,
    registration: str,
    age_group: str,
    facts: Mapping[str, Any],
) -> DecisionContext:
    """Build the :class:`DecisionContext` for one shared-registration obligation.

    *facts* is one entry from
    :func:`tournament_scheduler.hosting_coverage.shared_registration_facts`
    (``{"registration", "age_group", "constituents", "hosted_by_constituent",
    "hosted_by_constituent_total", "calendar_trust",
    "automatic_placement_possible"}``).
    """
    constituents = list(facts.get("constituents") or [])
    return DecisionContext(
        run_id=run_id,
        capability="shared_host_assignment",
        stage="stage3_planning",
        objective=(
            f"Decide which constituent club of the shared registration {registration!r} "
            f"({age_group}) should carry this hosting obligation. Calendar availability "
            "must not by itself decide the winner -- weigh current hosting burden/fairness "
            "first; a chosen constituent with no trustworthy calendar still keeps the "
            "obligation and becomes a manual placement instead of switching to the other "
            "constituent."
        ),
        facts=dict(facts),
        available_actions=SHARED_HOST_DECISION_ACTIONS,
        action_parameters={
            "assign_shared_host": {
                "chosen_club": {
                    "type": "string",
                    "enum": constituents,
                    "description": (
                        "The constituent club that will carry this hosting obligation. "
                        "Must be one of the registration's own constituents."
                    ),
                },
            },
        },
    )


def build_shared_host_decision_prompt(context: DecisionContext) -> str:
    """Build the judge prompt for one shared-host decision.

    Unlike the generic :func:`..llm_judge.prompts.build_action_decision_prompt`
    (which only ever asks for an action id on the first line), this
    decision's only useful action -- ``assign_shared_host`` -- is
    meaningless without its ``chosen_club`` argument, so the prompt asks for
    it explicitly on its own line and :func:`parse_shared_host_verdict`
    parses it back out.
    """
    constituents = list(context.facts.get("constituents") or [])
    lines = [f"OBJECTIVE: {context.objective}", ""]
    if context.facts:
        lines.append("Facts:")
        lines.extend(f"  - {key}: {value}" for key, value in context.facts.items())
        lines.append("")
    lines += [
        "Respond in exactly this format:",
        "Line 1: one action id, one of " + ", ".join(context.available_actions),
        "Line 2: only if line 1 is assign_shared_host, the chosen club exactly as "
        "written, one of: " + ", ".join(constituents),
        "Remaining lines: a brief rationale (never chain-of-thought).",
    ]
    return "\n".join(lines)


def parse_shared_host_verdict(context: DecisionContext, raw_verdict: str) -> DecisionAction:
    """Parse a judge reply for the shared-host decision into a :class:`DecisionAction`.

    Mirrors :func:`..llm_judge.prompts.parse_action_verdict`'s first-line
    action-id matching, but that generic parser never extracts an action's
    *arguments* -- it only ever returns the bare action id plus rationale.
    ``assign_shared_host`` is meaningless without ``chosen_club``, so this
    also extracts it: first from line 2 (the format the prompt asks for),
    falling back to a substring scan of the whole reply for a constituent
    club name. A verdict that names no constituent degrades to
    ``request_operator`` rather than guessing -- ``application.decisions``
    still rejects any non-constituent value deterministically either way.
    """
    lines = [ln.strip() for ln in raw_verdict.strip().splitlines() if ln.strip()]
    if not lines:
        return DecisionAction(
            action_id="request_operator",
            arguments={"question": "empty judge verdict"},
            rationale="empty judge verdict",
        )

    first = lines[0].lower().strip(" .:-")
    action_id = None
    for candidate in context.available_actions:
        if first == candidate.lower():
            action_id = candidate
            break
    if action_id is None:
        return DecisionAction(
            action_id="request_operator",
            arguments={"question": f"Could not parse judge verdict: {lines[0]!r}"},
            rationale=f"Unparsed judge verdict: {raw_verdict.strip()[:200]}",
        )

    if action_id != "assign_shared_host":
        return DecisionAction(action_id=action_id, rationale="\n".join(lines[1:]).strip())

    constituents = list(context.facts.get("constituents") or [])
    chosen = None
    for line in lines[1:]:
        stripped = line.strip(" .:-")
        for constituent in constituents:
            if stripped.lower() == constituent.lower():
                chosen = constituent
                break
        if chosen:
            break
    if chosen is None:
        lowered = raw_verdict.lower()
        for constituent in constituents:
            if constituent.lower() in lowered:
                chosen = constituent
                break

    rationale = "\n".join(lines[1:]).strip()
    if chosen is None:
        return DecisionAction(
            action_id="request_operator",
            arguments={"question": "assign_shared_host verdict did not name a constituent club"},
            rationale=rationale or raw_verdict.strip()[:200],
        )
    return DecisionAction(action_id="assign_shared_host", arguments={"chosen_club": chosen}, rationale=rationale)


def shared_host_decision_record(
    registration: str,
    age_group: str,
    chosen_club: str,
    rationale: str,
    *,
    decided_by: str = "llm",
    decided_at: str = "",
) -> Dict[str, str]:
    """Build the provenance dict persisted on ``SeasonPlan.shared_host_decisions``.

    Callers are expected to have already validated *chosen_club* via
    :func:`application.decisions.validate_decision_action` (the ``enum`` on
    ``assign_shared_host``'s ``chosen_club`` parameter already rejects any
    non-constituent choice deterministically) before recording this.
    """
    return {
        "registration": registration,
        "age_group": age_group,
        "chosen_club": chosen_club,
        "rationale": rationale,
        "decided_by": decided_by,
        "decided_at": decided_at,
    }
