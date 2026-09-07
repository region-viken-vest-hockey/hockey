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

from .application.decisions import DecisionContext

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
