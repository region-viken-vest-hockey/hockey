"""Club-wide booking source management use cases."""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.calendar_bookings import (
    CLUB_BOOKING_SOURCE_ASSERTIONS_KEY,
    MANUAL_ASSERTION_SUPERSEDED,
    active_club_booking_source_for_club,
    booking_status_report as _booking_status_report,
    new_club_booking_source_record,
)
from tournament_scheduler.canonical_state import (
    canonical_state_revision,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    SeasonStateError,
)

from ..shared import (
    _operator_identity,
    _now_iso,
    _append_decision_history,
    _resolve_plan_problem,
)

def set_club_booking_source(
    service,
    *,
    season: str,
    club: str,
    source_document: str,
    source_version: str,
    actor: str | None = None,
    note: str = "",
    reference: str = "",
    source_fingerprint: str | None = None,
    expected_revision: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Record a durable club-wide booking source document as accepted authority.

    The source assertion is decision-only: it never moves, approves or cancels a
    tournament. It persists the original club-provided booking list (document,
    version and optional explicit fingerprint) so later per-tournament
    interpretations remain traceable to the club scope they came from, and a
    newer version supersedes the previous record while retaining it as audit
    history. Per-ID authority is still recorded separately with
    ``set_manual_booking_assertion --source-assertion-id``.
    """

    if not str(club).strip():
        raise SeasonStateError("A club booking source assertion requires a club")
    if not str(source_document).strip():
        raise SeasonStateError("A club booking source assertion requires --source-document")
    if not str(source_version).strip():
        raise SeasonStateError("A club booking source assertion requires --source-version")
    if not (str(reference).strip() or str(note).strip()):
        raise SeasonStateError(
            "A club booking source assertion requires a traceable source reference "
            "(--reference) or rationale (--note)"
        )
    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    current_revision = canonical_state_revision(schedule, decisions)
    if expected_revision and expected_revision != current_revision:
        raise SeasonStateError(
            f"Stale canonical revision: expected {expected_revision}, current is {current_revision}"
        )
    now = _now_iso()
    resolved_actor = _operator_identity(actor)
    existing = active_club_booking_source_for_club(decisions, club)
    candidate = new_club_booking_source_record(
        club=club,
        source_document=source_document,
        source_version=source_version,
        actor=resolved_actor,
        asserted_at=now,
        source_revision=current_revision,
        reference=reference,
        note=note,
        source_fingerprint=source_fingerprint,
    )
    if existing is not None and str(existing.get("id") or "") == candidate["id"]:
        # A stable id alone is not enough: an explicit fingerprint reused for a
        # different document/version must not silently return the old record.
        if (
            str(existing.get("source_document") or "")
            != str(candidate.get("source_document") or "")
            or str(existing.get("source_version") or "")
            != str(candidate.get("source_version") or "")
        ):
            raise SeasonStateError(
                "Source fingerprint already identifies a different document/version "
                f"({existing.get('source_document')} @ {existing.get('source_version')}); "
                "use a distinct fingerprint or record the new version under its own identity"
            )
        return {
            "season": season,
            "dry_run": bool(dry_run),
            "changed": False,
            "idempotent": True,
            "club": club,
            "source": existing,
            "canonical_state_revision": current_revision,
        }
    if existing is not None:
        candidate["supersedes"] = str(existing.get("id") or "")
    updated = dict(decisions)
    records = [
        dict(record)
        for record in updated.get(CLUB_BOOKING_SOURCE_ASSERTIONS_KEY) or []
        if isinstance(record, Mapping)
    ]
    if existing is not None:
        existing_id = str(existing.get("id") or "")
        for record in records:
            if str(record.get("id") or "") == existing_id:
                record["status"] = MANUAL_ASSERTION_SUPERSEDED
                record["superseded_at"] = now
                record["superseded_by"] = candidate["id"]
                record["supersede_reason"] = note or (
                    f"superseded by source version {source_version}"
                )
    records.append(candidate)
    updated[CLUB_BOOKING_SOURCE_ASSERTIONS_KEY] = records
    updated["updated_at"] = now
    _append_decision_history(
        updated,
        event="set_club_booking_source",
        tournament_id="",
        actor=resolved_actor,
        now=now,
        note=note,
        details={
            "club": club,
            "source_assertion_id": candidate["id"],
            "source_document": source_document,
            "source_version": source_version,
            "source_fingerprint": candidate["source_fingerprint"],
            "reference": reference or "",
            "supersedes": candidate.get("supersedes") or "",
        },
    )
    result: dict[str, Any] = {
        "season": season,
        "dry_run": bool(dry_run),
        "changed": True,
        "idempotent": False,
        "club": club,
        "source": candidate,
        "previous_source": existing,
        "canonical_state_revision": current_revision,
    }
    if dry_run:
        return result
    committed = service._commit(snapshot.with_decisions(updated))
    result["canonical_state_revision"] = canonical_state_revision(committed.schedule, committed.decisions)
    return result

def club_booking_sources(
    service,
    *,
    season: str,
    club: str | None = None,
    problem: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read-only per-ID disposition for recorded club-wide booking sources."""

    snapshot = service.load(season)
    resolved_problem = _resolve_plan_problem(snapshot.schedule, problem, snapshot.decisions)
    report = _booking_status_report(
        problem=resolved_problem,
        plan=snapshot.schedule.get("plan") or {},
        decisions=snapshot.decisions,
    )
    sources = list(report.get("club_booking_sources") or [])
    if club:
        sources = [
            source
            for source in sources
            if str(source.get("host_club") or "") == club
            or source.get("unprovenanced")
        ]
    return {
        "season": season,
        "canonical_state_revision": canonical_state_revision(
            snapshot.schedule, snapshot.decisions
        ),
        "count": len(sources),
        "sources": sources,
    }
