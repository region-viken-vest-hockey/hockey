"""Shared eligibility and consequence boundary for participant removal.

The single ``season remove-participant`` operation and the atomic ``batch``
``remove_participant`` operator are two transports for the same canonical
outcome. They must apply the *same* domain policy:

* a withdrawal reconciliation may only name a team that is genuinely part of
  the authoritative registered eligible pool;
* a one-tournament absence without a withdrawal record must fail closed when
  the reduced shape is avoidably underfilled;
* the withdrawn team's own shortfall is reported separately and never blocks
  the operation, while every remaining affected team is measured for a
  material schedule regression.

Keeping that boundary here means the two paths cannot drift into different
eligibility rules.
"""

from __future__ import annotations

import copy
from typing import Any, Iterable, Mapping

from tournament_scheduler.infrastructure.canonical_season_store import (
    SeasonStateError,
)
from tournament_scheduler.participation_withdrawals import (
    is_registered_participant,
    team_identity,
)
from tournament_scheduler.planning_contract import verify_candidate


def remaining_team_identities(
    plan: Mapping[str, Any],
    tournament_ids: Iterable[str],
) -> tuple[tuple[str, str, str], ...]:
    """Return the distinct non-guest participants left in the affected scope."""

    wanted = {str(item) for item in tournament_ids}
    identities: set[tuple[str, str, str]] = set()
    for tournament in plan.get("tournaments", []) or []:
        if str(tournament.get("id") or "") not in wanted:
            continue
        age_group = str(tournament.get("age_group") or "")
        for team in tournament.get("teams", []) or []:
            if bool(team.get("guest", False)):
                continue
            identities.add(team_identity(team, age_group))
    return tuple(sorted(identities))


def require_registered_removal_target(
    problem: Mapping[str, Any] | None,
    *,
    removed_identity: tuple[str, str, str],
    remove_team_label: str,
) -> None:
    """Refuse a withdrawal for a team outside the authoritative registered pool."""

    if is_registered_participant(problem, removed_identity):
        return
    age_group = removed_identity[2] if len(removed_identity) > 2 else ""
    raise SeasonStateError(
        f"Refusing withdrawal reconciliation: {remove_team_label!r} is not a registered "
        f"{age_group or '<unknown>'} participant in the canonical problem"
    )


def avoidable_underfill_violations(
    candidate_plan: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
    tournament_ids: Iterable[str],
) -> list[dict[str, Any]]:
    """Return the scoped ``bye_team_not_allowed`` violations of a candidate."""

    wanted = {str(item) for item in tournament_ids}
    preview = verify_candidate(copy.deepcopy(dict(candidate_plan)), problem)
    return [
        violation
        for violation in preview.get("violations", [])
        if violation.get("code") == "bye_team_not_allowed"
        and str(violation.get("tournament_id") or "") in wanted
    ]


def require_no_avoidable_underfill(
    candidate_plan: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
    tournament_ids: Iterable[str],
) -> None:
    """Fail closed when a removal without reconciliation leaves an avoidable bye."""

    violations = avoidable_underfill_violations(candidate_plan, problem, tournament_ids)
    if not violations:
        return
    messages = "; ".join(str(item.get("message")) for item in violations)
    raise SeasonStateError(
        "Refusing canonical participant removal: removing the participant would leave an "
        "avoidably underfilled shape and no withdrawal reconciliation was requested. "
        "Re-run with --reconcile-withdrawal only for a genuine season/age-group "
        "withdrawal. " + messages
    )


def evaluate_removal_consequences(
    before_plan: Mapping[str, Any],
    after_plan: Mapping[str, Any],
    *,
    problem: Mapping[str, Any] | None,
    removed_identities: Iterable[tuple[str, str, str]],
    tournament_ids: Iterable[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(removed_consequences, retained_consequences)`` for a removal.

    Both maps are keyed by the full ``club|label|age_group`` identity. The
    withdrawn team's membership change is reported separately so its own
    deliberate participation shortfall never blocks the operation; only the
    remaining affected teams can do that.
    """

    from tournament_scheduler.team_schedule_quality import (
        compare_changed_team_schedule_consequence,
    )

    removed_set = {tuple(str(part) for part in identity) for identity in removed_identities}
    removed_consequences: dict[str, Any] = {}
    for identity in sorted(removed_set):
        key = "|".join(identity)
        removed_consequences[key] = compare_changed_team_schedule_consequence(
            before_plan,
            after_plan,
            identity,
            problem=problem,
            membership_role="removed",
        )
    retained_consequences: dict[str, Any] = {}
    for identity in remaining_team_identities(after_plan, list(tournament_ids)):
        if identity in removed_set:
            continue
        key = "|".join(identity)
        if key in retained_consequences:
            continue
        retained_consequences[key] = compare_changed_team_schedule_consequence(
            before_plan,
            after_plan,
            identity,
            problem=problem,
            membership_role="retained",
        )
    return removed_consequences, retained_consequences


__all__ = [
    "avoidable_underfill_violations",
    "evaluate_removal_consequences",
    "remaining_team_identities",
    "require_no_avoidable_underfill",
    "require_registered_removal_target",
]
