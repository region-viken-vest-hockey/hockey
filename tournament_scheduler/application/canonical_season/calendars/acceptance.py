"""Review and accept an existing club-wide booking interpretation set."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from tournament_scheduler.booking.calendar_bookings import (
    active_manual_assertions,
    club_booking_source_by_id,
    manual_assertion_stale_reasons,
)
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.season.state import canonical_state_revision

from ..shared import _append_decision_history, _now_iso, _operator_identity, _resolve_plan_problem

_ACCEPTANCE_EVENT = "club_booking_acceptance"


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _content_digest(preview: Mapping[str, Any]) -> str:
    """Digest of what was reviewed, independent of the canonical revision.

    Every canonical write advances the revision (including the acceptance audit
    entry itself), so recognising an already-accepted set needs a revision-free
    identity of the reviewed content.
    """

    return _digest(
        {
            "season": preview["season"],
            "source_id": preview["source_id"],
            "source_fingerprint": preview["source_fingerprint"],
            "interpretations": preview["interpretations"],
            "conflicts": preview["conflicts"],
        }
    )


def _reviewed_token(preview: Mapping[str, Any]) -> str:
    return _digest(
        {"content": _content_digest(preview), "canonical_state_revision": preview["canonical_state_revision"]}
    )


def _preview_from_snapshot(
    snapshot,
    *,
    season: str,
    source_id: str,
    expected_revision: str | None,
) -> dict[str, Any]:
    schedule, decisions = snapshot.schedule, snapshot.decisions
    revision = canonical_state_revision(schedule, decisions)
    if expected_revision and expected_revision != revision:
        raise SeasonStateError(f"Stale canonical revision: expected {expected_revision}, current is {revision}")

    source = club_booking_source_by_id(decisions, source_id)
    if source is None:
        raise SeasonStateError(
            f"Unknown active club booking source assertion: {source_id}. Superseded sources cannot be accepted."
        )

    plan = schedule.get("plan") or {}
    tournaments = {
        str(tournament.get("id") or ""): tournament
        for tournament in plan.get("tournaments") or []
        if isinstance(tournament, Mapping)
    }
    problem = _resolve_plan_problem(schedule, None, decisions)
    club = str(source.get("host_club") or "")
    interpretations: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    linked = sorted(
        (
            assertion
            for assertion in active_manual_assertions(decisions)
            if str(assertion.get("source_assertion_id") or "") == source_id
            and str(assertion.get("source_scope") or "") == "club_wide_interpretation"
        ),
        key=lambda assertion: str(assertion.get("tournament_id") or ""),
    )
    if not linked:
        conflicts.append(
            {
                "kind": "no_linked_interpretations",
                "message": "The source has no active per-tournament interpretations to accept.",
            }
        )

    for assertion in linked:
        tournament_id = str(assertion.get("tournament_id") or "")
        tournament = tournaments.get(tournament_id)
        stale_reasons = manual_assertion_stale_reasons(
            assertion,
            problem=problem,
            tournament=tournament,
        )
        item_conflicts = list(stale_reasons)
        if str(assertion.get("host_club") or "") != club:
            item_conflicts.append("source_host_club_mismatch")
        if tournament is not None and tournament.get("cancelled"):
            item_conflicts.append("tournament_cancelled")
        item_conflicts = sorted(set(item_conflicts))
        if item_conflicts:
            conflicts.append(
                {
                    "kind": "interpretation_not_current",
                    "tournament_id": tournament_id,
                    "reasons": item_conflicts,
                    "message": f"Tournament {tournament_id} must be reviewed before source acceptance.",
                }
            )

        interpretations.append(
            {
                "tournament_id": tournament_id,
                "assertion_id": str(assertion.get("id") or ""),
                "booking_status": str(assertion.get("booking_status") or ""),
                "before_interval": assertion.get("calendar_baseline"),
                "accepted_interval": assertion.get("asserted_interval"),
                "stated_interval": assertion.get("stated_interval"),
                "source_revision": str(assertion.get("source_revision") or ""),
                "conflicts": item_conflicts,
            }
        )

    preview: dict[str, Any] = {
        "season": season,
        "source_id": source_id,
        "club": club,
        "source_document": str(source.get("source_document") or ""),
        "source_version": str(source.get("source_version") or ""),
        "source_fingerprint": str(source.get("source_fingerprint") or ""),
        "canonical_state_revision": revision,
        "interpretations": interpretations,
        # Compatibility for callers introduced by the original PR.
        "proposed_assertions": interpretations,
        "count": len(interpretations),
        "conflicts": conflicts,
    }
    preview["content_digest"] = _content_digest(preview)
    preview["reviewed_token"] = _reviewed_token(preview)
    return preview


def club_booking_acceptance_preview(
    service,
    *,
    season: str,
    source_id: str,
    actor: str | None = None,
    note: str = "",
    reference: str = "",
    expected_revision: str | None = None,
) -> dict[str, Any]:
    """Preview the exact active interpretations already linked to one source."""

    del actor, note, reference
    preview = _preview_from_snapshot(
        service.load(season),
        season=season,
        source_id=source_id,
        expected_revision=expected_revision,
    )
    return {**preview, "dry_run": True}


def club_booking_acceptance_apply(
    service,
    *,
    season: str,
    source_id: str,
    actor: str | None = None,
    note: str = "",
    reference: str = "",
    expected_revision: str | None = None,
    reviewed_token: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Atomically record review of an existing, conflict-free interpretation set.

    Per-tournament source facts and interval corrections remain owned by
    ``set_manual_booking_assertion``. This operation never manufactures or
    rewrites assertions or booking evidence; it only persists the reviewed
    source/revision-bound acceptance artifact.
    """

    snapshot = service.load(season)
    preview = _preview_from_snapshot(
        snapshot,
        season=season,
        source_id=source_id,
        expected_revision=expected_revision,
    )
    if not reviewed_token:
        raise SeasonStateError("A reviewed token from club-booking-acceptance-preview is required")

    # A genuine repeat: this exact token was already accepted AND the reviewed
    # content is still what was accepted (later unrelated writes only advance the
    # revision). A token for content that has since changed is never honoured.
    history = snapshot.decisions.get("history") or []
    already_accepted = next(
        (
            entry
            for entry in reversed(history)
            if isinstance(entry, Mapping)
            and entry.get("event") == _ACCEPTANCE_EVENT
            and (entry.get("details") or {}).get("source_assertion_id") == source_id
            and (entry.get("details") or {}).get("reviewed_token") == reviewed_token
            and (entry.get("details") or {}).get("content_digest") == preview["content_digest"]
        ),
        None,
    )
    if already_accepted is not None and not preview["conflicts"]:
        return {
            **preview,
            "dry_run": bool(dry_run),
            "would_apply": preview["count"],
            "changed": False,
            "idempotent": True,
        }
    if reviewed_token != preview["reviewed_token"]:
        raise SeasonStateError(
            "Reviewed token does not match the active source, canonical revision, and interpretations; "
            "run the preview again"
        )
    if preview["conflicts"]:
        raise SeasonStateError(
            "Club booking acceptance has unresolved conflicts: "
            + ", ".join(str(conflict["kind"]) for conflict in preview["conflicts"])
        )

    result = {
        **preview,
        "dry_run": bool(dry_run),
        "would_apply": preview["count"],
        "changed": True,
        "idempotent": False,
    }
    if dry_run:
        return result

    updated = dict(snapshot.decisions)
    now = _now_iso()
    _append_decision_history(
        updated,
        event=_ACCEPTANCE_EVENT,
        tournament_id="",
        actor=_operator_identity(actor),
        now=now,
        note=note,
        details={
            "source_assertion_id": source_id,
            "source_fingerprint": preview["source_fingerprint"],
            "source_document": preview["source_document"],
            "source_version": preview["source_version"],
            "canonical_state_revision": preview["canonical_state_revision"],
            "reviewed_token": reviewed_token,
            "content_digest": preview["content_digest"],
            "reference": reference,
            "tournament_ids": [item["tournament_id"] for item in preview["interpretations"]],
        },
    )
    updated["updated_at"] = now
    committed = service._commit(
        snapshot.with_decisions(updated),
        expected_revision=preview["canonical_state_revision"],
    )
    return {
        **result,
        "dry_run": False,
        "changed": True,
        "idempotent": False,
        "canonical_state_revision": canonical_state_revision(
            committed.schedule,
            committed.decisions,
        ),
        "previous_revision": preview["canonical_state_revision"],
    }
