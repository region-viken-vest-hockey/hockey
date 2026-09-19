"""Canonical-season state identity.

Two distinct identities exist for a promoted season and must never be
conflated:

``schedule_fingerprint``
    The tournament-content identity. Changing a tournament's content changes
    it; changing a decision/lock/acceptance does not. It answers "is this the
    same schedule?" for export, diff and audit.

``canonical_state_revision``
    The effective canonical-planning-state identity. It combines the schedule
    fingerprint with the durable decisions, participation acceptances and the
    promoted verification/problem context, so an approval, lock, acceptance or
    revoke produces a new revision even though the tournament content is
    unchanged. Findings/options/actions bind to this revision so a sequential
    operator decision invalidates previously generated repair options.

Participation acceptance identity is also owned here. Canonical team identity is
``(club, label, age_group)``, so an acceptance is keyed by all three plus its
scope; legacy records that omitted ``age_group`` are migrated explicitly rather
than silently colliding.
"""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256

CANONICAL_STATE_REVISION_KEY = "canonical_state_revision"

CHANGE_PROTECTIONS_KEY = "change_protections"
PARTICIPATION_ACCEPTANCES_KEY = "participation_acceptances"
PARTICIPATION_ACCEPTANCE_PREFIX = "participation_acceptance"
REQUEST_CONSTRAINTS_KEY = "request_constraints"


def schedule_fingerprint(plan_dict: Mapping[str, Any]) -> str:
    """Return the tournament-content fingerprint of a plan payload."""

    return stable_payload_sha256(plan_dict.get("tournaments", []))


def compute_canonical_state_revision(
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
) -> str:
    """Return the effective canonical-state revision for a snapshot.

    The projection deliberately names only semantically relevant fields, so the
    result is a pure function of durable state (never of the previously stored
    revision or of volatile write timestamps).
    """

    payload = {
        "schedule_fingerprint": schedule_fingerprint(schedule.get("plan") or {}),
        "decisions": decisions.get("decisions") or {},
        "participation_acceptances": decisions.get(PARTICIPATION_ACCEPTANCES_KEY) or [],
        "change_protections": decisions.get(CHANGE_PROTECTIONS_KEY) or [],
        "request_constraints": decisions.get(REQUEST_CONSTRAINTS_KEY) or [],
        "verification_context": schedule.get("verification_context"),
    }
    return stable_payload_sha256(payload)


def canonical_state_revision(
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any] | None = None,
) -> str:
    """Return the effective revision readers should bind to.

    Seasons written by the canonical mutation service carry an explicit
    ``canonical_state_revision``. Older promoted files do not, so they fall back
    to the schedule content revision; the value converges on the first write
    through :class:`~tournament_scheduler.application.canonical_season_service.CanonicalSeasonService`.
    """

    resolved_decisions = decisions if isinstance(decisions, Mapping) else {}
    stored = resolved_decisions.get(CANONICAL_STATE_REVISION_KEY) or schedule.get(
        CANONICAL_STATE_REVISION_KEY
    )
    if stored:
        return str(stored)
    return str(schedule.get("revision") or schedule.get("fingerprint") or "")


def participation_acceptance_id(club: str, label: str, age_group: str, scope: str) -> str:
    """Stable identity for one accepted team/scope participation deviation."""

    return f"{PARTICIPATION_ACCEPTANCE_PREFIX}:{club}:{label}:{age_group}:{scope}"


def migrate_participation_acceptance_ids(decisions: dict[str, Any]) -> list[str]:
    """Rewrite legacy acceptance ids to include ``age_group``.

    Legacy ids omitted the age group, so the same club/label/scope in two age
    groups could collide. The record already stores ``age_group``; the migration
    recomputes the id from it and keeps the old value for audit. Returns the ids
    that were migrated.
    """

    records = decisions.get(PARTICIPATION_ACCEPTANCES_KEY)
    if not isinstance(records, list):
        return []
    migrated: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        club = str(record.get("club") or "")
        label = str(record.get("label") or "")
        age_group = str(record.get("age_group") or "")
        scope = str(record.get("scope") or "")
        if not (club and label and age_group and scope):
            continue
        expected = participation_acceptance_id(club, label, age_group, scope)
        current = str(record.get("id") or "")
        if current == expected:
            continue
        record["id"] = expected
        if current:
            record.setdefault("migrated_from", current)
        migrated.append(expected)
    return migrated


__all__ = [
    "CANONICAL_STATE_REVISION_KEY",
    "CHANGE_PROTECTIONS_KEY",
    "PARTICIPATION_ACCEPTANCES_KEY",
    "PARTICIPATION_ACCEPTANCE_PREFIX",
    "REQUEST_CONSTRAINTS_KEY",
    "canonical_state_revision",
    "compute_canonical_state_revision",
    "migrate_participation_acceptance_ids",
    "participation_acceptance_id",
    "schedule_fingerprint",
]
