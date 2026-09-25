"""Canonical eligibility/withdrawal reconciliation for promoted seasons.

A mid-season team withdrawal is a durable fact about the *eligible pool* for an
age group, not only a per-tournament roster edit. ``effective_tournament_shape``
derives the legal no-bye shape from the registered pool, so removing a
participant without recording the pool change looks like an avoidable
underfill and is correctly refused. This module owns the explicit,
revision-bound reconciliation record that lets the verifier and game generator
see the correct active pool at the relevant scope.

The registered ``Lag`` roster (and the historical participation of an already
completed tournament) is never rewritten: withdrawal records are additive
canonical decisions that scope the reduced eligible pool to the exact
tournaments a genuine withdrawal applies to. A one-tournament participant
absence without such a record keeps the full registered pool and therefore
fails closed if the smaller shape is not independently legal.

Identity mirrors canonical team identity ``(club, label, age_group)``. The
verifier consumes the projection through :func:`project_into_problem`; no other
module re-derives an equivalent scope.

Supersession is explicit *and* projection-safe. A record is released (status
``released``) by the canonical release operation (CLI
``season release-withdrawal``) when a participant is restored or the
registration is reconciled, which keeps the record's provenance while removing
its eligibility effect. Independently, the projection only honours a record
while its team is genuinely absent from the scoped tournament and still present
in the registered pool, so an un-released stale record can never keep masking an
underfilled shape.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from tournament_scheduler.canonical_state import PARTICIPATION_WITHDRAWALS_KEY
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

ACTIVE = "active"
RELEASED = "released"

#: Problem field the verifier reads. Kept as a list of
#: ``{"tournament_id", "club", "label", "age_group"}`` entries so a single
#: problem stays a plain, serializable structure.
WITHDRAWN_TEAMS_FIELD = "withdrawn_tournament_teams"


def team_identity(team: Mapping[str, Any], fallback_age_group: str = "") -> tuple[str, str, str]:
    return (
        str(team.get("club") or ""),
        str(team.get("label") or ""),
        str(team.get("age_group") or fallback_age_group),
    )


def registered_team_identities(
    problem: Mapping[str, Any] | None,
) -> tuple[set[tuple[str, str, str]], set[tuple[str, str]]]:
    """Return the registered eligible pool as identity sets.

    The first set is the full canonical ``(club, label, age_group)`` identity.
    The second contains ``(club, label)`` pairs for legacy teams whose age group
    is missing, so a withdrawal target can still be recognised without
    fabricating an age group.
    """

    if not isinstance(problem, Mapping):
        return set(), set()
    full: set[tuple[str, str, str]] = set()
    ageless: set[tuple[str, str]] = set()
    for team in problem.get("teams", []) or []:
        if not isinstance(team, Mapping):
            continue
        club = str(team.get("club") or "")
        label = str(team.get("label") or "")
        if not club or not label:
            continue
        age_group = str(team.get("age_group") or "")
        if age_group:
            full.add((club, label, age_group))
        else:
            ageless.add((club, label))
    return full, ageless


def is_registered_participant(
    problem: Mapping[str, Any] | None,
    identity: tuple[str, str, str],
) -> bool:
    """Return whether *identity* exists in the authoritative registered pool.

    The full ``(club, label, age_group)`` identity must match a registered team;
    a legacy team without a recorded age group also matches on ``(club, label)``.
    """

    club, label, age_group = (str(part) for part in identity)
    if not club or not label:
        return False
    full, ageless = registered_team_identities(problem)
    if (club, label, age_group) in full:
        return True
    return (club, label) in ageless


def _record_id(
    *,
    team: Mapping[str, Any],
    tournament_id: str,
    request_id: str,
    source_revision: str,
) -> str:
    digest = stable_payload_sha256(
        {
            "kind": "participation_withdrawal",
            "team": {
                "club": str(team.get("club") or ""),
                "label": str(team.get("label") or ""),
                "age_group": str(team.get("age_group") or ""),
            },
            "tournament_id": tournament_id,
            "request_id": request_id,
            "source_revision": source_revision,
        }
    )
    return f"withdrawal:{digest[:16]}"


def build_withdrawal_records(
    *,
    team: Mapping[str, Any],
    tournament_ids: Iterable[str],
    request_id: str,
    actor: str,
    note: str,
    created_at: str,
    source_revision: str,
) -> list[dict[str, Any]]:
    """Build one revision-bound withdrawal record per affected tournament."""

    resolved_team = {
        "club": str(team.get("club") or ""),
        "label": str(team.get("label") or ""),
        "age_group": str(team.get("age_group") or ""),
    }
    records: list[dict[str, Any]] = []
    for tournament_id in sorted({str(item) for item in tournament_ids if str(item)}):
        record = {
            "kind": "participation_withdrawal",
            "status": ACTIVE,
            "team": dict(resolved_team),
            "tournament_id": tournament_id,
            "request_id": str(request_id or ""),
            "created_at": created_at,
            "created_by": actor,
            "note": note or "",
            "source_event": "participant_removal",
            "source_revision": source_revision,
        }
        record["id"] = _record_id(
            team=resolved_team,
            tournament_id=tournament_id,
            request_id=str(request_id or ""),
            source_revision=source_revision,
        )
        records.append(record)
    return records


def active_withdrawals(decisions: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(decisions, Mapping):
        return []
    return [
        dict(record)
        for record in decisions.get(PARTICIPATION_WITHDRAWALS_KEY, []) or []
        if isinstance(record, Mapping) and str(record.get("status") or ACTIVE) == ACTIVE
    ]


def _record_entries(records: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for record in records:
        if not isinstance(record, Mapping):
            continue
        team = record.get("team")
        if not isinstance(team, Mapping):
            continue
        tournament_id = str(record.get("tournament_id") or "")
        if not tournament_id:
            continue
        identity = (
            tournament_id,
            str(team.get("club") or ""),
            str(team.get("label") or ""),
            str(team.get("age_group") or ""),
        )
        if identity in seen:
            continue
        seen.add(identity)
        entries.append(
            {
                "tournament_id": identity[0],
                "club": identity[1],
                "label": identity[2],
                "age_group": identity[3],
            }
        )
    return entries


def withdrawn_entries(problem: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """Return the withdrawal projection already carried by a problem."""

    if not isinstance(problem, Mapping):
        return []
    raw = problem.get(WITHDRAWN_TEAMS_FIELD)
    if not isinstance(raw, list):
        return []
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        resolved = {
            "tournament_id": str(entry.get("tournament_id") or ""),
            "club": str(entry.get("club") or ""),
            "label": str(entry.get("label") or ""),
            "age_group": str(entry.get("age_group") or ""),
        }
        key = (
            resolved["tournament_id"],
            resolved["club"],
            resolved["label"],
            resolved["age_group"],
        )
        if not key[0] or key in seen:
            continue
        seen.add(key)
        entries.append(resolved)
    return entries


def _plan_roster_identities(
    plan: Mapping[str, Any] | None,
) -> set[tuple[str, str, str, str]]:
    """Return ``(tournament_id, club, label, age_group)`` for current participants."""

    present: set[tuple[str, str, str, str]] = set()
    if not isinstance(plan, Mapping):
        return present
    for tournament in plan.get("tournaments", []) or []:
        if not isinstance(tournament, Mapping):
            continue
        tournament_id = str(tournament.get("id") or "")
        if not tournament_id:
            continue
        age_group = str(tournament.get("age_group") or "")
        for team in tournament.get("teams", []) or []:
            if not isinstance(team, Mapping):
                continue
            present.add(
                (
                    tournament_id,
                    str(team.get("club") or ""),
                    str(team.get("label") or ""),
                    str(team.get("age_group") or age_group),
                )
            )
    return present


def _filter_scoped_entries(
    entries: list[dict[str, str]],
    *,
    plan: Mapping[str, Any] | None,
    problem: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    """Drop withdrawal entries that no longer legitimately reduce eligibility.

    A withdrawal record is only meaningful while the named team is genuinely
    absent from the tournament it scopes *and* still belongs to the registered
    eligible pool. If the team is restored to the tournament, or the registered
    pool is reconciled so the team is no longer eligible at all, the record must
    stop reducing the eligible shape count even though its provenance is kept.
    This is what makes a stale record harmless without erasing history.
    """

    if plan is None:
        return entries
    present = _plan_roster_identities(plan)
    registered_full, registered_ageless = registered_team_identities(problem)
    has_registered_pool = bool(registered_full or registered_ageless)
    filtered: list[dict[str, str]] = []
    for entry in entries:
        key = (entry["tournament_id"], entry["club"], entry["label"], entry["age_group"])
        if key in present:
            continue
        if has_registered_pool and not is_registered_participant(
            problem, (entry["club"], entry["label"], entry["age_group"])
        ):
            continue
        filtered.append(entry)
    return filtered


def project_into_problem(
    problem: Mapping[str, Any] | None,
    *,
    decisions: Mapping[str, Any] | None = None,
    records: Iterable[Mapping[str, Any]] | None = None,
    plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a copy of *problem* carrying the active withdrawal scopes.

    Withdrawals already present in *decisions* are merged with the explicit
    *records* supplied by a not-yet-committed operation. The result is a plain
    problem payload with only the additive :data:`WITHDRAWN_TEAMS_FIELD`.

    When *plan* is supplied the merged scopes are reconciled against it: an
    entry stops reducing eligibility once its team is restored to the scoped
    tournament or is no longer part of the registered pool. Callers that pass
    explicit *records* for a candidate under construction (the plan already
    reflects the removal) deliberately omit *plan* so the new scopes apply.
    """

    resolved: dict[str, Any] = dict(problem) if isinstance(problem, Mapping) else {}
    if not resolved:
        return resolved
    merged = list(withdrawn_entries(resolved))
    combined = list(active_withdrawals(decisions))
    if records:
        combined.extend(dict(record) for record in records if isinstance(record, Mapping))
    merged.extend(_record_entries(combined))
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for entry in merged:
        key = (entry["tournament_id"], entry["club"], entry["label"], entry["age_group"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    if plan is not None:
        deduped = _filter_scoped_entries(deduped, plan=plan, problem=resolved)
    resolved[WITHDRAWN_TEAMS_FIELD] = deduped
    return resolved


def withdrawn_team_count_for_tournament(
    problem: Mapping[str, Any] | None,
    tournament_id: str,
    age_group: str,
) -> int:
    """Return how many distinct withdrawn teams the problem scopes to one tournament."""

    resolved_id = str(tournament_id or "")
    if not resolved_id:
        return 0
    identities = {
        (entry["club"], entry["label"], entry["age_group"])
        for entry in withdrawn_entries(problem)
        if entry["tournament_id"] == resolved_id and entry["age_group"] == age_group
    }
    return len(identities)


def append_withdrawal_records(
    decisions: dict[str, Any],
    records: list[dict[str, Any]],
) -> None:
    existing = decisions.setdefault(PARTICIPATION_WITHDRAWALS_KEY, [])
    existing_ids = {str(record.get("id") or "") for record in existing if isinstance(record, Mapping)}
    for record in records:
        record_id = str(record.get("id") or "")
        if record_id and record_id not in existing_ids:
            existing.append(dict(record))
            existing_ids.add(record_id)


__all__ = [
    "ACTIVE",
    "PARTICIPATION_WITHDRAWALS_KEY",
    "RELEASED",
    "WITHDRAWN_TEAMS_FIELD",
    "active_withdrawals",
    "append_withdrawal_records",
    "build_withdrawal_records",
    "is_registered_participant",
    "project_into_problem",
    "registered_team_identities",
    "team_identity",
    "withdrawn_entries",
    "withdrawn_team_count_for_tournament",
]
