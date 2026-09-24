"""Service-owned authorization for narrow mutations on a sealed season.

A published, sealed season may not authorize schedule changes from a caller
controlled string such as ``operation="targeted_mutation"`` or from a
caller-built "scope" list.  A wide whole-season candidate can trivially be
packaged as a contract that declares every tournament, so a mode string or a
syntactically valid contract must never itself grant permission.

Instead this module owns the *typed operations* the application layer allows on
a sealed season.  Each ``authorize_*`` function:

* derives the affected tournament ids from the exact operation the candidate
  performs (never from a caller-supplied id list);
* compares the complete before/after operational projection owned by
  :mod:`tournament_scheduler.pipeline.export_projection_guard` -- stable id, age
  group, placement, participants, canonical occupancy duration/end, cancellation
  state and guest-reservation facts -- so no published timetable or booking fact
  can change unnoticed;
* reproduces the exact bounded repair for ``bounded_repair`` and derives the
  allowed ids from the reproduced candidate rather than trusting the caller; and
* mints a capability that the canonical apply boundary re-validates before it
  writes.  The capability is only produced here, so a caller cannot construct
  one and use it to bypass the sealed-season global-regeneration refusal.

For legitimate participant swaps and replacements the operation evidence also
pins that *only* participants changed: placement, host, age group, occupancy,
cancellation state and guest reservations are part of the projection and are
rejected if they drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from tournament_scheduler.canonical_state import canonical_state_revision
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.pipeline.export_projection_guard import (
    diff_tournament_projection,
    tournament_projection,
)
from tournament_scheduler.published_baseline import (
    participant_key,
    projection_problem_from_schedule,
)


# A module-private token.  The commit boundary checks identity, so a caller that
# only has the public API cannot mint an authorization.
_SCOPED_MUTATION_CAPABILITY = object()

# Operational facts a roster-only operation (swap/replacement) must not alter.
_ROSTER_ONLY_FIELDS = (
    "date",
    "start_time",
    "arena",
    "host_club",
    "age_group",
    "duration_minutes",
    "end_time",
    "cancelled",
    "cancellation_reason",
    "guest_slots",
)


@dataclass(frozen=True)
class ScopedMutationAuthorization:
    """Service-minted evidence that one narrow mutation is allowed."""

    operation: str
    expected_canonical_revision: str
    affected_tournament_ids: tuple[str, ...]
    before_records: dict[str, dict[str, Any] | None]
    after_records: dict[str, dict[str, Any] | None]
    _capability: object = field(default=None, repr=False, compare=False)


def is_scoped_mutation_authorization(value: Any) -> bool:
    """Return whether ``value`` is a capability this module minted."""

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


def _validated_scope(
    *,
    schedule: Mapping[str, Any],
    candidate: Mapping[str, Any],
    operation: str,
    expected_affected: Iterable[str],
    problem: Mapping[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], tuple[str, ...]]:
    """Derive the changed ids and require them to equal the operation's scope."""

    resolved_problem = problem if problem is not None else projection_problem_from_schedule(schedule)
    before = _projection(schedule.get("plan") or {}, resolved_problem)
    after = _projection(candidate, resolved_problem)
    derived = changed_tournament_ids(before, after)
    expected = tuple(sorted({str(item) for item in expected_affected if str(item)}))
    if derived != expected:
        raise SeasonStateError(
            f"Refusing {operation}: candidate changes tournaments {list(derived)} "
            f"but the operation allows only {list(expected)}"
        )
    if not derived:
        raise SeasonStateError(f"Refusing {operation}: candidate does not change the schedule")
    return before, after, derived


def _mint(
    *,
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
    operation: str,
    affected: tuple[str, ...],
) -> ScopedMutationAuthorization:
    return ScopedMutationAuthorization(
        operation=operation,
        expected_canonical_revision=canonical_state_revision(schedule, decisions),
        affected_tournament_ids=affected,
        before_records={tournament_id: before.get(tournament_id) for tournament_id in affected},
        after_records={tournament_id: after.get(tournament_id) for tournament_id in affected},
        _capability=_SCOPED_MUTATION_CAPABILITY,
    )


def _assert_roster_only(
    authorization: ScopedMutationAuthorization,
    *,
    operation: str,
) -> None:
    for tournament_id in authorization.affected_tournament_ids:
        before = authorization.before_records.get(tournament_id) or {}
        after = authorization.after_records.get(tournament_id) or {}
        for field_name in _ROSTER_ONLY_FIELDS:
            if before.get(field_name) != after.get(field_name):
                raise SeasonStateError(
                    f"Refusing {operation}: tournament {tournament_id} changed "
                    f"{field_name}, which the operation does not allow"
                )


def _participants(record: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(record, Mapping):
        return []
    return [str(key) for key in record.get("participants") or []]


def _replaced_once(participants: list[str], removed: str, added: str) -> list[str]:
    result = [key for key in participants if key != removed]
    if added and added not in result:
        result.append(added)
    return sorted(result)


def _team_key(team: Mapping[str, Any] | None, fallback_age_group: str) -> str:
    team = team or {}
    age_group = str(team.get("age_group") or fallback_age_group)
    return participant_key({**dict(team), "age_group": age_group})


def authorize_participant_swap(
    *,
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
    candidate: Mapping[str, Any],
    tournament_a_id: str,
    tournament_b_id: str,
    age_group: str,
    team_a: Mapping[str, Any],
    team_b: Mapping[str, Any],
) -> ScopedMutationAuthorization:
    """Authorize a two-tournament same-age participant swap."""

    tournament_a_id = str(tournament_a_id)
    tournament_b_id = str(tournament_b_id)
    before, after, affected = _validated_scope(
        schedule=schedule,
        candidate=candidate,
        operation="participant swap",
        expected_affected=(tournament_a_id, tournament_b_id),
    )
    authorization = _mint(
        schedule=schedule,
        decisions=decisions,
        before=before,
        after=after,
        operation="participant_swap",
        affected=affected,
    )
    _assert_roster_only(authorization, operation="participant swap")

    key_a = _team_key(team_a, age_group)
    key_b = _team_key(team_b, age_group)
    if key_a == key_b:
        raise SeasonStateError("Refusing participant swap: the two team identities are identical")
    expected_a = _replaced_once(_participants(before.get(tournament_a_id)), key_a, key_b)
    expected_b = _replaced_once(_participants(before.get(tournament_b_id)), key_b, key_a)
    if _participants(after.get(tournament_a_id)) != expected_a or _participants(
        after.get(tournament_b_id)
    ) != expected_b:
        raise SeasonStateError(
            "Refusing participant swap: candidate participants are not the declared same-age swap"
        )
    return authorization


def authorize_participant_replacement(
    *,
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
    candidate: Mapping[str, Any],
    tournament_id: str,
    age_group: str,
    removed_team: Mapping[str, Any],
    added_team: Mapping[str, Any],
) -> ScopedMutationAuthorization:
    """Authorize a one-tournament participant replacement."""

    tournament_id = str(tournament_id)
    before, after, affected = _validated_scope(
        schedule=schedule,
        candidate=candidate,
        operation="participant replacement",
        expected_affected=(tournament_id,),
    )
    authorization = _mint(
        schedule=schedule,
        decisions=decisions,
        before=before,
        after=after,
        operation="participant_replacement",
        affected=affected,
    )
    _assert_roster_only(authorization, operation="participant replacement")

    removed = _team_key(removed_team, age_group)
    added = _team_key(added_team, age_group)
    expected = _replaced_once(_participants(before.get(tournament_id)), removed, added)
    if _participants(after.get(tournament_id)) != expected:
        raise SeasonStateError(
            "Refusing participant replacement: candidate participants are not the declared replacement"
        )
    return authorization


def authorize_bounded_repair(
    *,
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
    candidate: Mapping[str, Any],
    option_id: str,
    finding_id: str | None = None,
    dimensions: Iterable[str] | None = None,
    problem: Mapping[str, Any] | None = None,
    allow_manual_placement: bool = False,
    allow_host_confirmation: bool = False,
) -> ScopedMutationAuthorization:
    """Authorize the exact, reproduced result of one bounded repair option.

    ``option_id``/``finding_id`` and the current revision are verified by
    reproducing the bounded repair from the current canonical plan.  The allowed
    affected ids are derived from that reproduction, never from the caller.
    """

    # Imported lazily: ``season_maintenance`` imports this module for its own
    # apply path, so a module-level import would be circular.
    from tournament_scheduler.season_maintenance import apply_repair_to_plan

    plan = schedule.get("plan") or {}
    resolved_problem = problem if problem is not None else projection_problem_from_schedule(schedule)
    reproduction = apply_repair_to_plan(
        plan,
        resolved_problem or {},
        str(option_id),
        finding_id=finding_id,
        dimensions=dimensions if dimensions is not None else (),
        allow_manual_placement=allow_manual_placement,
        allow_host_confirmation=allow_host_confirmation,
    )
    if not reproduction.get("ok"):
        raise SeasonStateError(
            "Refusing bounded repair: option "
            f"{option_id!r} could not be reproduced from the current canonical plan "
            f"({reproduction.get('reason') or 'rejected'})"
        )
    reproduced_candidate = reproduction.get("candidate")
    if not isinstance(reproduced_candidate, Mapping):
        raise SeasonStateError("Refusing bounded repair: reproduction returned no candidate")

    before = _projection(plan, resolved_problem)
    after_reproduced = _projection(reproduced_candidate, resolved_problem)
    affected = changed_tournament_ids(before, after_reproduced)
    if not affected:
        raise SeasonStateError(f"Refusing bounded repair: option {option_id!r} does not change the schedule")

    after_candidate = _projection(candidate, resolved_problem)
    if changed_tournament_ids(before, after_candidate) != affected:
        raise SeasonStateError(
            "Refusing bounded repair: candidate does not match the reproduced bounded repair"
        )
    authorization = _mint(
        schedule=schedule,
        decisions=decisions,
        before=before,
        after=after_candidate,
        operation="bounded_repair",
        affected=affected,
    )
    for tournament_id in affected:
        if authorization.after_records.get(tournament_id) != after_reproduced.get(tournament_id):
            raise SeasonStateError(
                "Refusing bounded repair: candidate does not match the reproduced bounded repair "
                f"for tournament {tournament_id}"
            )
    return authorization


def validate_scoped_mutation_authorization(
    *,
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
    candidate: Mapping[str, Any],
    authorization: Any,
    operation: str,
) -> None:
    """Re-validate a minted authorization at the canonical apply boundary."""

    if not is_scoped_mutation_authorization(authorization):
        raise SeasonStateError(
            f"Refusing {operation}: sealed-season mutation requires a service-issued "
            "scoped authorization, not a caller-constructed scope"
        )
    current_revision = canonical_state_revision(schedule, decisions)
    if authorization.expected_canonical_revision != current_revision:
        raise SeasonStateError(
            f"Refusing {operation}: scoped mutation expected canonical revision "
            f"{authorization.expected_canonical_revision}, found {current_revision}"
        )
    problem = projection_problem_from_schedule(schedule)
    before = _projection(schedule.get("plan") or {}, problem)
    after = _projection(candidate, problem)
    derived = changed_tournament_ids(before, after)
    affected = tuple(sorted({str(item) for item in authorization.affected_tournament_ids if str(item)}))
    if not affected:
        raise SeasonStateError(f"Refusing {operation}: scoped mutation authorizes no tournaments")
    if set(derived) != set(affected):
        raise SeasonStateError(
            f"Refusing {operation}: candidate changed tournaments {list(derived)} outside its "
            f"authorized scope {list(affected)}"
        )
    expected_before = {tournament_id: before.get(tournament_id) for tournament_id in affected}
    expected_after = {tournament_id: after.get(tournament_id) for tournament_id in affected}
    if authorization.before_records != expected_before:
        raise SeasonStateError(
            f"Refusing {operation}: scoped mutation before-records do not match current canonical state"
        )
    if authorization.after_records != expected_after:
        raise SeasonStateError(
            f"Refusing {operation}: scoped mutation after-records do not match candidate state"
        )


def validate_scoped_mutation_completion(
    *,
    schedule: Mapping[str, Any],
    plan: Mapping[str, Any],
    authorization: Any,
    operation: str,
) -> None:
    """Re-check the reconciled candidate against the authorized scope.

    Derived-state reconciliation runs after the first scope check.  It must not
    move, demote, recancel or otherwise change any tournament outside the
    authorized scope, and the final after-state must still be exactly the state
    the authorization recorded (otherwise durable history could not replay).
    """

    if not is_scoped_mutation_authorization(authorization):
        raise SeasonStateError(
            f"Refusing {operation}: sealed-season mutation lost its scoped authorization"
        )
    problem = projection_problem_from_schedule(schedule)
    before = _projection(schedule.get("plan") or {}, problem)
    after = _projection(plan, problem)
    derived = changed_tournament_ids(before, after)
    affected = tuple(sorted({str(item) for item in authorization.affected_tournament_ids if str(item)}))
    if set(derived) != set(affected):
        raise SeasonStateError(
            f"Refusing {operation}: reconciled candidate changed tournaments {list(derived)} "
            f"outside its authorized scope {list(affected)}"
        )
    expected_after = {tournament_id: after.get(tournament_id) for tournament_id in affected}
    if authorization.after_records != expected_after:
        raise SeasonStateError(
            f"Refusing {operation}: reconciled candidate no longer matches the authorized after-state"
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
    "ScopedMutationAuthorization",
    "authorization_history_details",
    "authorize_bounded_repair",
    "authorize_participant_replacement",
    "authorize_participant_swap",
    "changed_tournament_ids",
    "is_scoped_mutation_authorization",
    "validate_scoped_mutation_authorization",
    "validate_scoped_mutation_completion",
]
