"""Validated scoped schedule-mutation contracts.

A published, sealed season may not authorize schedule changes from a caller
controlled string such as ``operation=\"targeted_mutation\"``.  Narrow
maintenance paths instead construct this application-owned contract from the
current canonical snapshot and the exact candidate they intend to commit.  The
canonical apply boundary then re-validates the expected revision, declared scope
and before/after records before persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from tournament_scheduler.canonical_state import canonical_state_revision
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.published_baseline import participant_key


_OPERATIONAL_EXTRA_FIELDS = (
    "ice_time_minutes",
    "duration_minutes",
    "end_time",
    "cancelled",
    "requires_host_confirmation",
    "host_confirmation_reason",
    "placement_status",
)


@dataclass(frozen=True)
class ScopedMutationContract:
    """Expected current revision, scope and stable before/after records."""

    expected_canonical_revision: str
    affected_tournament_ids: tuple[str, ...]
    before_records: dict[str, dict[str, Any] | None]
    after_records: dict[str, dict[str, Any] | None]


def _tournament_by_id(plan: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(tournament.get("id") or ""): tournament
        for tournament in plan.get("tournaments", []) or []
        if isinstance(tournament, Mapping) and tournament.get("id")
    }


def stable_tournament_record(tournament: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return the publishable operational facts used to scope a mutation."""

    if tournament is None:
        return None
    tournament_id = str(tournament.get("id") or "")
    record: dict[str, Any] = {
        "id": tournament_id,
        "date": str(tournament.get("date") or ""),
        "start_time": str(tournament.get("start_time") or ""),
        "arena": str(tournament.get("arena") or ""),
        "host_club": str(tournament.get("host_club") or ""),
        "age_group": str(tournament.get("age_group") or ""),
        "participants": sorted(
            participant_key(team)
            for team in tournament.get("teams", []) or []
            if isinstance(team, Mapping)
        ),
    }
    for field in _OPERATIONAL_EXTRA_FIELDS:
        if field in tournament:
            record[field] = tournament.get(field)
    return record


def make_scoped_mutation_contract(
    *,
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
    candidate: Mapping[str, Any],
    affected_tournament_ids: list[str] | tuple[str, ...] | set[str],
) -> ScopedMutationContract:
    """Build a revision-bound contract for a narrow candidate mutation."""

    affected = tuple(sorted({str(item) for item in affected_tournament_ids if str(item)}))
    before_by_id = _tournament_by_id(schedule.get("plan") or {})
    after_by_id = _tournament_by_id(candidate)
    return ScopedMutationContract(
        expected_canonical_revision=canonical_state_revision(schedule, decisions),
        affected_tournament_ids=affected,
        before_records={
            tournament_id: stable_tournament_record(before_by_id.get(tournament_id))
            for tournament_id in affected
        },
        after_records={
            tournament_id: stable_tournament_record(after_by_id.get(tournament_id))
            for tournament_id in affected
        },
    )


def validate_scoped_mutation_contract(
    *,
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
    candidate: Mapping[str, Any],
    contract: ScopedMutationContract,
    operation: str,
) -> None:
    """Validate that a candidate changes only the declared scoped records."""

    if not isinstance(contract, ScopedMutationContract):
        raise SeasonStateError(
            f"Refusing {operation}: sealed-season mutation requires a validated scoped contract"
        )
    current_revision = canonical_state_revision(schedule, decisions)
    if contract.expected_canonical_revision != current_revision:
        raise SeasonStateError(
            f"Refusing {operation}: scoped mutation expected canonical revision "
            f"{contract.expected_canonical_revision}, found {current_revision}"
        )
    affected = tuple(sorted({str(item) for item in contract.affected_tournament_ids if str(item)}))
    if not affected:
        raise SeasonStateError(f"Refusing {operation}: scoped mutation declares no affected tournaments")

    before_by_id = _tournament_by_id(schedule.get("plan") or {})
    after_by_id = _tournament_by_id(candidate)
    all_ids = set(before_by_id) | set(after_by_id)
    changed_ids = sorted(
        tournament_id
        for tournament_id in all_ids
        if before_by_id.get(tournament_id) != after_by_id.get(tournament_id)
    )
    changed_outside_scope = [tournament_id for tournament_id in changed_ids if tournament_id not in affected]
    if changed_outside_scope:
        raise SeasonStateError(
            f"Refusing {operation}: scoped mutation changed tournaments outside its declared scope: "
            + ", ".join(changed_outside_scope)
        )
    if not changed_ids:
        raise SeasonStateError(f"Refusing {operation}: scoped mutation does not change the schedule")

    expected_before = {
        tournament_id: stable_tournament_record(before_by_id.get(tournament_id))
        for tournament_id in affected
    }
    expected_after = {
        tournament_id: stable_tournament_record(after_by_id.get(tournament_id))
        for tournament_id in affected
    }
    if contract.before_records != expected_before:
        raise SeasonStateError(f"Refusing {operation}: scoped mutation before-records do not match current canonical state")
    if contract.after_records != expected_after:
        raise SeasonStateError(f"Refusing {operation}: scoped mutation after-records do not match candidate state")


def contract_history_details(contract: ScopedMutationContract) -> dict[str, Any]:
    """Serialize contract evidence for durable mutation history."""

    return {
        "expected_canonical_revision": contract.expected_canonical_revision,
        "affected_tournament_ids": list(contract.affected_tournament_ids),
        "before_records": contract.before_records,
        "after_records": contract.after_records,
    }


__all__ = [
    "ScopedMutationContract",
    "contract_history_details",
    "make_scoped_mutation_contract",
    "stable_tournament_record",
    "validate_scoped_mutation_contract",
]
