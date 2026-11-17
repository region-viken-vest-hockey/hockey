"""Club-wide source document identity, provenance and version lookup."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.booking.booking_vocabulary import (
    BOOKING_AUTHORITY_MANUAL_INTERPRETATION,
    CLUB_BOOKING_SOURCE_ASSERTIONS_KEY,
    CLUB_BOOKING_SOURCE_SCOPE,
    MANUAL_ASSERTION_ACTIVE,
)
from tournament_scheduler.pipeline.fingerprints import stable_payload_sha256


def club_booking_source_id(club: str, source_fingerprint: str) -> str:
    """Stable identity for one club-wide booking source document."""

    return f"club_booking_source:{club}:{source_fingerprint}"


def _club_booking_source_fingerprint(
    *,
    club: str,
    source_document: str,
    source_version: str,
) -> str:
    """Deterministic identity of a club booking source when none is supplied.

    The fingerprint names the source identity, not its volatile file bytes, so a
    re-import of the same document/version is idempotent while a new version or
    a different document is a distinct, superseding assertion.
    """

    return stable_payload_sha256(
        {"club": club, "source_document": source_document, "source_version": source_version}
    )


def new_club_booking_source_record(
    *,
    club: str,
    source_document: str,
    source_version: str,
    actor: str,
    asserted_at: str,
    source_revision: str,
    reference: str = "",
    note: str = "",
    source_fingerprint: str | None = None,
    supersedes: str | None = None,
) -> dict[str, Any]:
    """Build one durable, revision-bound club-wide booking source assertion.

    The record carries the original source identity/version plus its optional
    explicit fingerprint, so a later operator can tell which club-provided
    booking list a per-tournament interpretation was derived from.
    """

    fingerprint = str(source_fingerprint or "").strip() or _club_booking_source_fingerprint(
        club=club,
        source_document=source_document,
        source_version=source_version,
    )
    return {
        "id": club_booking_source_id(club, fingerprint),
        "schema_version": 1,
        "status": MANUAL_ASSERTION_ACTIVE,
        "authority": BOOKING_AUTHORITY_MANUAL_INTERPRETATION,
        "source_scope": CLUB_BOOKING_SOURCE_SCOPE,
        "host_club": club,
        "source_document": source_document,
        "source_version": source_version,
        "source_fingerprint": fingerprint,
        "reference": str(reference or ""),
        "note": note or "",
        "asserted_at": asserted_at,
        "asserted_by": actor,
        "source_revision": source_revision,
        "supersedes": supersedes,
    }


def club_booking_source_records(decisions: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Return every persisted club-wide booking source assertion."""

    return [
        dict(record)
        for record in ((decisions or {}).get(CLUB_BOOKING_SOURCE_ASSERTIONS_KEY) or [])
        if isinstance(record, Mapping)
    ]


def active_club_booking_source_assertions(decisions: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    return [
        record
        for record in club_booking_source_records(decisions)
        if str(record.get("status") or "") == MANUAL_ASSERTION_ACTIVE
    ]


def club_booking_source_by_id(
    decisions: Mapping[str, Any] | None,
    source_id: str,
) -> dict[str, Any] | None:
    """Return one active club-wide source assertion by id, if present.

    Used for link validation: a new per-tournament interpretation may only be
    linked to a currently active source version.
    """

    if not source_id:
        return None
    for record in active_club_booking_source_assertions(decisions):
        if str(record.get("id") or "") == source_id:
            return record
    return None


def club_booking_source_record_by_id(
    decisions: Mapping[str, Any] | None,
    source_id: str,
) -> dict[str, Any] | None:
    """Return one club-wide source assertion by id regardless of status.

    Historical provenance lookup: after a source is superseded, existing
    per-tournament interpretations still reference the old id and must keep
    resolving to the document/version they were derived from.
    """

    if not source_id:
        return None
    for record in club_booking_source_records(decisions):
        if str(record.get("id") or "") == source_id:
            return record
    return None


def active_club_booking_source_for_club(
    decisions: Mapping[str, Any] | None,
    club: str,
) -> dict[str, Any] | None:
    """Return the newest active club-wide source assertion for one club."""

    matches = [
        record
        for record in active_club_booking_source_assertions(decisions)
        if str(record.get("host_club") or "") == club
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda record: str(record.get("asserted_at") or ""))[-1]
