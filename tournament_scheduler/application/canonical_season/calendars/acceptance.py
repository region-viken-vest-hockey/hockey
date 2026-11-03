"""Review and accept an existing club-wide booking interpretation set."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.season.state import canonical_state_revision

from ..shared import _append_decision_history, _now_iso, _operator_identity
from .acceptance_preview import _preview_from_snapshot

_ACCEPTANCE_EVENT = "club_booking_acceptance"


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
