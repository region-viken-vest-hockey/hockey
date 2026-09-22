"""Durable intent guards for accepted promoted-season changes.

A hard-valid later repair can still accidentally undo an earlier club request.
Change protections preserve the exact participant-placement intent of accepted
manual maintenance until an operator explicitly releases/supersedes it.
"""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.canonical_state import CHANGE_PROTECTIONS_KEY
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

ACTIVE = "active"
RELEASED = "released"
MUST_PARTICIPATE = "must_participate"
MUST_NOT_PARTICIPATE = "must_not_participate"
PLACEMENT_FIELD = "placement_field"


def team_identity(team: Mapping[str, Any], fallback_age_group: str = "") -> tuple[str, str, str]:
    return (
        str(team.get("club") or ""),
        str(team.get("label") or ""),
        str(team.get("age_group") or fallback_age_group),
    )


def _protection_id(
    *,
    kind: str,
    team: Mapping[str, Any],
    tournament_id: str,
    request_id: str,
    source_revision: str,
) -> str:
    digest = stable_payload_sha256(
        {
            "kind": kind,
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
    return f"change:{digest[:16]}"


def build_swap_protections(
    *,
    team_a: Mapping[str, Any],
    tournament_a_id: str,
    team_b: Mapping[str, Any],
    tournament_b_id: str,
    request_id: str,
    actor: str,
    note: str,
    created_at: str,
    source_revision: str,
) -> list[dict[str, Any]]:
    """Protect both sides of an accepted A<->B roster swap."""

    records: list[dict[str, Any]] = []
    for team, old_tournament, new_tournament in (
        (team_a, tournament_a_id, tournament_b_id),
        (team_b, tournament_b_id, tournament_a_id),
    ):
        for kind, tournament_id in (
            (MUST_PARTICIPATE, new_tournament),
            (MUST_NOT_PARTICIPATE, old_tournament),
        ):
            record = {
                "kind": kind,
                "status": ACTIVE,
                "team": {
                    "club": str(team.get("club") or ""),
                    "label": str(team.get("label") or ""),
                    "age_group": str(team.get("age_group") or ""),
                },
                "tournament_id": tournament_id,
                "request_id": request_id,
                "created_at": created_at,
                "created_by": actor,
                "note": note or "",
                "source_event": "participant_swap",
            }
            record["id"] = _protection_id(
                kind=kind,
                team=record["team"],
                tournament_id=tournament_id,
                request_id=request_id,
                source_revision=source_revision,
            )
            records.append(record)
    return records


def build_participant_replacement_protections(
    *,
    removed_team: Mapping[str, Any],
    added_team: Mapping[str, Any],
    tournament_id: str,
    request_id: str,
    actor: str,
    note: str,
    created_at: str,
    source_revision: str,
) -> list[dict[str, Any]]:
    """Protect an accepted one-tournament participant substitution."""

    records: list[dict[str, Any]] = []
    for team, kind in (
        (added_team, MUST_PARTICIPATE),
        (removed_team, MUST_NOT_PARTICIPATE),
    ):
        record = {
            "kind": kind,
            "status": ACTIVE,
            "team": {
                "club": str(team.get("club") or ""),
                "label": str(team.get("label") or ""),
                "age_group": str(team.get("age_group") or ""),
            },
            "tournament_id": tournament_id,
            "request_id": request_id,
            "created_at": created_at,
            "created_by": actor,
            "note": note or "",
            "source_event": "participant_replacement",
        }
        record["id"] = _protection_id(
            kind=kind,
            team=record["team"],
            tournament_id=tournament_id,
            request_id=request_id,
            source_revision=source_revision,
        )
        records.append(record)
    return records


def build_move_protections(
    *,
    tournament_id: str,
    changed_fields: Mapping[str, Any],
    request_id: str,
    actor: str,
    note: str,
    created_at: str,
    source_revision: str,
) -> list[dict[str, Any]]:
    """Protect only the placement fields an accepted move intentionally changed."""

    records: list[dict[str, Any]] = []
    for field in ("date", "arena", "host_club", "start_time"):
        if field not in changed_fields:
            continue
        value = changed_fields[field]
        synthetic_team = {
            "club": "",
            "label": f"{tournament_id}:{field}",
            "age_group": "",
        }
        record = {
            "kind": PLACEMENT_FIELD,
            "status": ACTIVE,
            "team": synthetic_team,
            "tournament_id": tournament_id,
            "field": field,
            "value": value,
            "request_id": request_id,
            "created_at": created_at,
            "created_by": actor,
            "note": note or "",
            "source_event": "move",
        }
        record["id"] = _protection_id(
            kind=f"{PLACEMENT_FIELD}:{field}:{value}",
            team=synthetic_team,
            tournament_id=tournament_id,
            request_id=request_id,
            source_revision=source_revision,
        )
        records.append(record)
    return records


def active_change_protections(decisions: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(record)
        for record in decisions.get(CHANGE_PROTECTIONS_KEY, []) or []
        if isinstance(record, Mapping) and str(record.get("status") or ACTIVE) == ACTIVE
    ]


def protection_violations(
    plan: Mapping[str, Any],
    decisions: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return violations of active team-specific change protections."""

    tournaments = {
        str(tournament.get("id") or ""): tournament
        for tournament in plan.get("tournaments", []) or []
        if tournament.get("id") and not tournament.get("cancelled")
    }
    violations: list[dict[str, Any]] = []
    for protection in active_change_protections(decisions):
        tournament_id = str(protection.get("tournament_id") or "")
        team = protection.get("team") or {}
        identity = team_identity(team)
        tournament = tournaments.get(tournament_id)
        present = bool(
            tournament
            and any(
                team_identity(candidate, str(tournament.get("age_group") or "")) == identity
                for candidate in tournament.get("teams", []) or []
                if not bool(candidate.get("guest", False))
            )
        )
        kind = str(protection.get("kind") or "")
        if kind == PLACEMENT_FIELD:
            field = str(protection.get("field") or "")
            expected = protection.get("value")
            actual = tournament.get(field) if tournament is not None else None
            violated = tournament is None or actual != expected
        else:
            field = ""
            expected = None
            actual = None
            violated = (
                kind == MUST_PARTICIPATE and not present
            ) or (
                kind == MUST_NOT_PARTICIPATE and present
            )
        if violated:
            violations.append(
                {
                    "code": "change_protection_violation",
                    "protection_id": protection.get("id"),
                    "request_id": protection.get("request_id") or "",
                    "kind": kind,
                    "team": dict(team),
                    "tournament_id": tournament_id,
                    "field": field or None,
                    "expected": expected,
                    "actual": actual,
                    "message": (
                        f"Accepted change protection {protection.get('id')} "
                        f"({kind}) would be undone in {tournament_id}"
                    ),
                }
            )
    return violations


def append_change_protections(
    decisions: dict[str, Any],
    protections: list[dict[str, Any]],
) -> None:
    records = decisions.setdefault(CHANGE_PROTECTIONS_KEY, [])
    existing_ids = {
        str(record.get("id") or "")
        for record in records
        if isinstance(record, Mapping)
    }
    for protection in protections:
        protection_id = str(protection.get("id") or "")
        if protection_id and protection_id not in existing_ids:
            records.append(dict(protection))
            existing_ids.add(protection_id)


__all__ = [
    "ACTIVE",
    "MUST_NOT_PARTICIPATE",
    "MUST_PARTICIPATE",
    "PLACEMENT_FIELD",
    "RELEASED",
    "active_change_protections",
    "append_change_protections",
    "build_move_protections",
    "build_participant_replacement_protections",
    "build_swap_protections",
    "protection_violations",
    "team_identity",
]
