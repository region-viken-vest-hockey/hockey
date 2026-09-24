"""Service-owned authorization for narrow mutations on a sealed season.

A published, sealed season may not authorize schedule changes from a caller
controlled string such as ``operation="targeted_mutation"`` or from a
caller-built "scope" list.  A wide whole-season candidate can trivially be
packaged as a contract that declares every tournament, so a mode string or a
syntactically valid contract must never itself grant permission.

This module owns the *typed operations* the application layer allows on a
sealed season.  An authorization records which typed operation is requested and
the exact operation parameters.  It is **not** proof by itself: at the canonical
apply boundary the typed operation is re-run from the current canonical state
and the submitted candidate must equal the reproduced result as a *whole plan*,
excluding only explicitly identified derived/reporting fields.  A forged object,
a stolen capability token or a widened candidate therefore cannot authorize
anything the repository-owned operation would not itself produce.

The comparison is deliberately broader than the published operational
projection: plan-owned facts the projection omits -- ``unresolved_tournament_placements``,
tournament ``games`` and per-tournament placement/host-confirmation metadata --
are preserved too.  A bounded repair may not silently delete an unrelated
obligation or rewrite games, and a roster swap/replacement may not touch an
unrelated tournament or its host-confirmation/placement metadata.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from tournament_scheduler.canonical_state import canonical_state_revision
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.pipeline.export_projection_guard import (
    diff_tournament_projection,
    tournament_projection,
)
from tournament_scheduler.published_baseline import projection_problem_from_schedule


OPERATION_PARTICIPANT_SWAP = "participant_swap"
OPERATION_PARTICIPANT_REPLACEMENT = "participant_replacement"
OPERATION_BOUNDED_REPAIR = "bounded_repair"

_PERMITTED_HISTORY_EVENT = {
    OPERATION_PARTICIPANT_SWAP: "participant_swap",
    OPERATION_PARTICIPANT_REPLACEMENT: "participant_replacement",
    OPERATION_BOUNDED_REPAIR: "repair_option_applied",
}

# Exactly the plan fields excluded from the whole-plan comparison: the
# reconciliation-owned derived/reporting projections (which legitimately differ
# between an un-reconciled reproduction and a reconciled candidate), the
# dispatcher's ``source`` metadata and the schema annotation. Everything else,
# including ``unresolved_tournament_placements``, tournament ``games`` and
# placement/host-confirmation metadata, is compared.
_IGNORED_PLAN_FIELDS = (
    "source",
    "schema_version",
    "unresolved_hosting_obligations",
    "same_age_hosting_repairs",
    "cross_age_hosting_repairs",
    "hosting_balance",
    "hosting_balance_imbalances",
    "unresolved_external_conflicts",
    "unresolved_participation_shortfalls",
    "participation_club_pools",
    "operator_waived_violations",
    "operator_waivers",
    "publication_readiness",
)

# A module-private token. Kept only as defence in depth; the apply boundary does
# NOT treat it as proof -- it re-runs the typed operation instead.
_SCOPED_MUTATION_CAPABILITY = object()


@dataclass(frozen=True)
class ScopedMutationAuthorization:
    """Typed evidence that one narrow operation is requested.

    ``parameters`` identify the exact repository-owned operation to re-run;
    ``affected_tournament_ids`` and the projection records are descriptive
    evidence used for the durable history, not authorization.
    """

    operation: str
    expected_canonical_revision: str
    parameters: dict[str, Any]
    affected_tournament_ids: tuple[str, ...]
    before_records: dict[str, dict[str, Any] | None]
    after_records: dict[str, dict[str, Any] | None]
    permitted_history_event: str
    _capability: object = field(default=None, repr=False, compare=False)


def is_scoped_mutation_authorization(value: Any) -> bool:
    """Return whether ``value`` carries this module's capability token.

    Retained for diagnostics and defence in depth. The sealed apply boundary
    accepts any :class:`ScopedMutationAuthorization` instance and proves it by
    reproducing the typed operation, so the token is never the proof.
    """

    return (
        isinstance(value, ScopedMutationAuthorization)
        and value._capability is _SCOPED_MUTATION_CAPABILITY
    )


def _projection(plan: Mapping[str, Any] | None, problem: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    return tournament_projection(plan or {}, problem)


def changed_tournament_ids(
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
) -> tuple[str, ...]:
    """Return the stable ids whose complete operational projection changed."""

    delta = diff_tournament_projection(before, after)
    changed = set(delta.get("removed_tournament_ids") or [])
    changed.update(delta.get("added_tournament_ids") or [])
    changed.update(str(entry.get("tournament_id")) for entry in delta.get("field_changes") or [])
    return tuple(sorted(changed))


def normalized_plan_content(plan: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the semantically meaningful plan content for a whole-plan compare.

    Derived/reporting projections and the schema annotation are dropped; every
    other plan field (including ``unresolved_tournament_placements``, tournament
    ``games`` and host-confirmation/placement metadata) stays in the comparison.
    """

    normalized = copy.deepcopy(dict(plan or {}))
    for field_name in _IGNORED_PLAN_FIELDS:
        normalized.pop(field_name, None)
    return normalized


def _plan_content_differences(
    expected: Mapping[str, Any] | None,
    actual: Mapping[str, Any] | None,
) -> list[str]:
    expected_plan = normalized_plan_content(expected)
    actual_plan = normalized_plan_content(actual)
    differences: list[str] = []
    for key in sorted(set(expected_plan) | set(actual_plan)):
        if key == "tournaments":
            continue
        if expected_plan.get(key) != actual_plan.get(key):
            differences.append(str(key))
    expected_tournaments = {
        str(tournament.get("id") or ""): tournament
        for tournament in expected_plan.get("tournaments") or []
        if isinstance(tournament, Mapping)
    }
    actual_tournaments = {
        str(tournament.get("id") or ""): tournament
        for tournament in actual_plan.get("tournaments") or []
        if isinstance(tournament, Mapping)
    }
    for tournament_id in sorted(set(expected_tournaments) | set(actual_tournaments)):
        if expected_tournaments.get(tournament_id) != actual_tournaments.get(tournament_id):
            differences.append(f"tournament:{tournament_id}")
    return differences


def _require_same_operation_result(
    *,
    expected: Mapping[str, Any] | None,
    actual: Mapping[str, Any] | None,
    operation: str,
) -> None:
    differences = _plan_content_differences(expected, actual)
    if differences:
        raise SeasonStateError(
            f"Refusing {operation}: candidate does not match the reproduced operation. "
            "Unexpected differences in: " + ", ".join(differences)
        )


def _authoritative_problem(
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild the canonical maintenance problem from durable canonical state.

    Reproduction must not depend on a caller-supplied planning problem: a
    crafted ``parallel_games``/``rounds_per_tournament`` could otherwise change
    the games the operation regenerates for its own tournaments while leaving
    the rest of the plan untouched.
    """

    from .approvals import _acceptance_record_id
    from .shared import _resolve_plan_problem
    from tournament_scheduler.canonical_baseline import build_canonical_baseline
    from tournament_scheduler.canonical_state import PARTICIPATION_ACCEPTANCES_KEY
    from tournament_scheduler.participation_targets import search_evidence_from_acceptances

    problem = _resolve_plan_problem(schedule, None, decisions)
    if problem is None:
        # A legacy promoted season without a stored verification context can
        # still perform a placement-only swap; reproduction then matches the
        # operation's own default game parameters.
        problem = {}
    problem["canonical_baseline"] = build_canonical_baseline(schedule, decisions)
    records = decisions.get(PARTICIPATION_ACCEPTANCES_KEY) or []
    acceptances = [
        {**record, "id": _acceptance_record_id(record)}
        for record in records
        if isinstance(record, dict) and not record.get("revoked_at")
    ]
    problem["participation_search_evidence"] = search_evidence_from_acceptances(acceptances)
    return problem


def _reproduce_operation(
    *,
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
    authorization: ScopedMutationAuthorization,
) -> dict[str, Any]:
    """Re-run the typed operation from current canonical state."""

    plan = copy.deepcopy(schedule.get("plan") or {})
    parameters = dict(authorization.parameters or {})
    operation = authorization.operation
    problem = _authoritative_problem(schedule, decisions)

    if operation == OPERATION_PARTICIPANT_SWAP:
        from .roster import _apply_swap_to_plan

        _apply_swap_to_plan(
            plan,
            tournament_a_id=str(parameters.get("tournament_a_id") or ""),
            team_a_label=str(parameters.get("team_a_label") or ""),
            tournament_b_id=str(parameters.get("tournament_b_id") or ""),
            team_b_label=str(parameters.get("team_b_label") or ""),
            problem=problem,
        )
    elif operation == OPERATION_PARTICIPANT_REPLACEMENT:
        from .replacement import _apply_replace_participant_to_plan

        _apply_replace_participant_to_plan(
            plan,
            tournament_id=str(parameters.get("tournament_id") or ""),
            remove_team_label=str(parameters.get("remove_team_label") or ""),
            add_team_label=str(parameters.get("add_team_label") or ""),
            problem=problem,
        )
    elif operation == OPERATION_BOUNDED_REPAIR:
        # Imported lazily: ``season_maintenance`` imports this module for its own
        # apply path, so a module-level import would be circular.
        from tournament_scheduler.season_maintenance import apply_repair_to_plan

        reproduction = apply_repair_to_plan(
            plan,
            problem,
            str(parameters.get("option_id") or ""),
            finding_id=parameters.get("finding_id"),
            dimensions=parameters.get("dimensions") or (),
            allow_manual_placement=bool(parameters.get("allow_manual_placement", False)),
            allow_host_confirmation=bool(parameters.get("allow_host_confirmation", False)),
        )
        if not reproduction.get("ok"):
            raise SeasonStateError(
                "Refusing bounded repair: option "
                f"{parameters.get('option_id')!r} could not be reproduced from the current "
                f"canonical plan ({reproduction.get('reason') or 'rejected'})"
            )
        reproduced_candidate = reproduction.get("candidate")
        if not isinstance(reproduced_candidate, Mapping):
            raise SeasonStateError("Refusing bounded repair: reproduction returned no candidate")
        plan = copy.deepcopy(dict(reproduced_candidate))
    else:
        raise SeasonStateError(f"Refusing {operation}: unknown scoped mutation operation {operation!r}")

    return plan


def _mint(
    *,
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
    operation: str,
    parameters: Mapping[str, Any],
    affected: tuple[str, ...],
    before_records: dict[str, dict[str, Any] | None],
    after_records: dict[str, dict[str, Any] | None],
) -> ScopedMutationAuthorization:
    return ScopedMutationAuthorization(
        operation=operation,
        expected_canonical_revision=canonical_state_revision(schedule, decisions),
        parameters=dict(parameters),
        affected_tournament_ids=affected,
        before_records=before_records,
        after_records=after_records,
        permitted_history_event=_PERMITTED_HISTORY_EVENT[operation],
        _capability=_SCOPED_MUTATION_CAPABILITY,
    )


def _authorize_operation(
    *,
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
    candidate: Mapping[str, Any],
    operation: str,
    parameters: Mapping[str, Any],
    description: str,
) -> ScopedMutationAuthorization:
    """Reproduce one typed operation and require the candidate to match it."""

    authorization = _mint(
        schedule=schedule,
        decisions=decisions,
        operation=operation,
        parameters=parameters,
        affected=(),
        before_records={},
        after_records={},
    )
    reproduced = _reproduce_operation(
        schedule=schedule,
        decisions=decisions,
        authorization=authorization,
    )
    _require_same_operation_result(expected=reproduced, actual=candidate, operation=description)

    resolved_problem = _authoritative_problem(schedule, decisions)
    before = _projection(schedule.get("plan") or {}, resolved_problem)
    after = _projection(reproduced, resolved_problem)
    affected = changed_tournament_ids(before, after)
    if not affected:
        raise SeasonStateError(f"Refusing {description}: the operation does not change the schedule")
    return _mint(
        schedule=schedule,
        decisions=decisions,
        operation=operation,
        parameters=parameters,
        affected=affected,
        before_records={tournament_id: before.get(tournament_id) for tournament_id in affected},
        after_records={tournament_id: after.get(tournament_id) for tournament_id in affected},
    )


def authorize_participant_swap(
    *,
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
    candidate: Mapping[str, Any],
    tournament_a_id: str,
    team_a_label: str,
    tournament_b_id: str,
    team_b_label: str,
) -> ScopedMutationAuthorization:
    """Authorize a two-tournament same-age participant swap."""

    return _authorize_operation(
        schedule=schedule,
        decisions=decisions,
        candidate=candidate,
        operation=OPERATION_PARTICIPANT_SWAP,
        parameters={
            "tournament_a_id": str(tournament_a_id),
            "team_a_label": str(team_a_label),
            "tournament_b_id": str(tournament_b_id),
            "team_b_label": str(team_b_label),
        },
        description="participant swap",
    )


def authorize_participant_replacement(
    *,
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
    candidate: Mapping[str, Any],
    tournament_id: str,
    remove_team_label: str,
    add_team_label: str,
) -> ScopedMutationAuthorization:
    """Authorize a one-tournament participant replacement."""

    return _authorize_operation(
        schedule=schedule,
        decisions=decisions,
        candidate=candidate,
        operation=OPERATION_PARTICIPANT_REPLACEMENT,
        parameters={
            "tournament_id": str(tournament_id),
            "remove_team_label": str(remove_team_label),
            "add_team_label": str(add_team_label),
        },
        description="participant replacement",
    )


def authorize_bounded_repair(
    *,
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
    candidate: Mapping[str, Any],
    option_id: str,
    finding_id: str | None = None,
    dimensions: Iterable[str] | None = None,
    allow_manual_placement: bool = False,
    allow_host_confirmation: bool = False,
) -> ScopedMutationAuthorization:
    """Authorize the exact, reproduced result of one bounded repair option."""

    return _authorize_operation(
        schedule=schedule,
        decisions=decisions,
        candidate=candidate,
        operation=OPERATION_BOUNDED_REPAIR,
        parameters={
            "option_id": str(option_id),
            "finding_id": finding_id,
            "dimensions": sorted(str(item) for item in (dimensions or ())),
            "allow_manual_placement": bool(allow_manual_placement),
            "allow_host_confirmation": bool(allow_host_confirmation),
        },
        description="bounded repair",
    )


def validate_scoped_mutation_authorization(
    *,
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
    candidate: Mapping[str, Any],
    authorization: Any,
    operation: str,
) -> dict[str, Any]:
    """Revalidate a typed operation at the canonical apply boundary.

    The typed operation is re-run from the current canonical state and the
    submitted candidate must equal the reproduced result as a whole plan. The
    capability token is not consulted as proof. Returns the reproduced plan so
    the caller can reuse it for the post-reconciliation completion check.
    """

    if not isinstance(authorization, ScopedMutationAuthorization):
        raise SeasonStateError(
            f"Refusing {operation}: sealed-season mutation requires a repository-owned "
            "scoped operation authorization"
        )
    current_revision = canonical_state_revision(schedule, decisions)
    if authorization.expected_canonical_revision != current_revision:
        raise SeasonStateError(
            f"Refusing {operation}: scoped mutation expected canonical revision "
            f"{authorization.expected_canonical_revision}, found {current_revision}"
        )
    reproduced = _reproduce_operation(
        schedule=schedule,
        decisions=decisions,
        authorization=authorization,
    )
    _require_same_operation_result(expected=reproduced, actual=candidate, operation=operation)

    resolved_problem = _authoritative_problem(schedule, decisions)
    before = _projection(schedule.get("plan") or {}, resolved_problem)
    after = _projection(reproduced, resolved_problem)
    derived = changed_tournament_ids(before, after)
    affected = tuple(
        sorted({str(item) for item in authorization.affected_tournament_ids if str(item)})
    )
    if not affected:
        raise SeasonStateError(f"Refusing {operation}: scoped mutation authorizes no tournaments")
    if set(derived) != set(affected):
        raise SeasonStateError(
            f"Refusing {operation}: candidate changed tournaments {list(derived)} outside its "
            f"authorized scope {list(affected)}"
        )
    return reproduced


def validate_scoped_mutation_completion(
    *,
    schedule: Mapping[str, Any],
    plan: Mapping[str, Any],
    reproduced: Mapping[str, Any] | None,
    authorization: Any,
    operation: str,
) -> None:
    """Re-check the reconciled candidate against the reproduced operation.

    Derived-state reconciliation runs after the first check. It must not change
    the plan in any way the typed operation did not produce, and the final
    after-state must still match the durable history the authorization records.
    """

    if not isinstance(authorization, ScopedMutationAuthorization):
        raise SeasonStateError(
            f"Refusing {operation}: sealed-season mutation lost its scoped authorization"
        )
    if reproduced is not None:
        _require_same_operation_result(expected=reproduced, actual=plan, operation=operation)
    problem = projection_problem_from_schedule(schedule)
    before = _projection(schedule.get("plan") or {}, problem)
    after = _projection(plan, problem)
    derived = changed_tournament_ids(before, after)
    affected = tuple(
        sorted({str(item) for item in authorization.affected_tournament_ids if str(item)})
    )
    if set(derived) != set(affected):
        raise SeasonStateError(
            f"Refusing {operation}: reconciled candidate changed tournaments {list(derived)} "
            f"outside its authorized scope {list(affected)}"
        )
    if authorization.after_records:
        expected_after = {tournament_id: after.get(tournament_id) for tournament_id in affected}
        if authorization.after_records != expected_after:
            raise SeasonStateError(
                f"Refusing {operation}: reconciled candidate no longer matches the authorized "
                "after-state"
            )


def authorization_history_details(authorization: ScopedMutationAuthorization) -> dict[str, Any]:
    """Serialize authorization evidence for durable mutation history."""

    return {
        "expected_canonical_revision": authorization.expected_canonical_revision,
        "affected_tournament_ids": list(authorization.affected_tournament_ids),
        "before_records": authorization.before_records,
        "after_records": authorization.after_records,
    }


__all__ = [
    "OPERATION_BOUNDED_REPAIR",
    "OPERATION_PARTICIPANT_REPLACEMENT",
    "OPERATION_PARTICIPANT_SWAP",
    "ScopedMutationAuthorization",
    "authorization_history_details",
    "authorize_bounded_repair",
    "authorize_participant_replacement",
    "authorize_participant_swap",
    "changed_tournament_ids",
    "is_scoped_mutation_authorization",
    "normalized_plan_content",
    "validate_scoped_mutation_authorization",
    "validate_scoped_mutation_completion",
]
