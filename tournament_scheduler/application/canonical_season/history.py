"""Canonical decision-history retention: compaction and archival.

The historical writer embedded a full whole-season ``verify_candidate`` result
inside every ``move`` history entry, which duplicates the same payload per move
and dominates ``decisions.json``. The current writer persists a bounded
:func:`~tournament_scheduler.canonical_history_summary.verification_summary`
instead. This module owns the deliberate migration of the already-oversized
history: it preserves every replay-critical history event in order, moves the
exact old evidence into the durable content-addressed archive, replaces the
inline payload with the bounded summary plus an evidence reference, and verifies
that the canonical-state revision, schedule fingerprint and event replay are all
unchanged before committing atomically.

Before any mutation the migration also preserves the *complete original*
``decisions.json`` byte-for-byte into a durable, content-addressed backup with a
migration manifest (see
:mod:`tournament_scheduler.infrastructure.canonical_compaction_backup`), so the
transformation is reversible and auditable even though the compacted history is
smaller.

Compaction is opt-in, reviewable and idempotent. It never rewrites the published
schedule and never generates a planning candidate.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Mapping

from tournament_scheduler.canonical_history_summary import verification_summary
from tournament_scheduler.canonical_state import (
    CANONICAL_STATE_REVISION_KEY,
    compute_canonical_state_revision,
    schedule_fingerprint,
)
from tournament_scheduler.infrastructure.canonical_compaction_backup import (
    compaction_backup_id,
    create_compaction_backup,
    load_compaction_backup,
)
from tournament_scheduler.infrastructure.canonical_evidence_archive import (
    archive_move_evidence,
    evidence_ref,
    load_move_evidence,
)
from tournament_scheduler.infrastructure.canonical_season_store import (
    SeasonStateError,
)
from tournament_scheduler.published_baseline import (
    active_baseline,
    is_published_sealed,
    projection_from_canonical_schedule,
)
from tournament_scheduler.published_mutation_history import reconcile_published_baseline

from .shared import _now_iso, _operator_identity, published_baseline_reconciliation

VERIFICATION_RESULT_KEY = "verification_result"
VERIFICATION_SUMMARY_KEY = "verification_summary"
EVIDENCE_REF_KEY = "evidence_ref"


def _json_size(payload: Any) -> int:
    return len(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _compact_event_details(
    service,
    *,
    season: str,
    entry: Mapping[str, Any],
    archive: bool,
    root: str,
    dry_run: bool = False,
) -> tuple[dict[str, Any], str | None]:
    """Return (new_details, archived_evidence_hash) for one history entry.

    The archived hash is ``None`` when the entry was already bounded or when
    ``archive`` is disabled and the full evidence is dropped. In dry-run mode
    the reference is computed but the archive file is not written. The narrow
    transform touches *only* the oversized ``verification_result``; every other
    detail field (including the operational-acceptability verdict with its full
    profiles) is preserved verbatim. An unknown or ambiguous evidence shape is
    refused rather than silently stripped.
    """

    details = deepcopy(dict(entry.get("details") or {}))
    canonical_revision = str(
        details.get("before_canonical_revision") or entry.get("schedule_fingerprint") or ""
    )
    event_at = str(entry.get("at") or "")
    tournament_id = str(entry.get("tournament_id") or "")

    archived_hash: str | None = None
    if VERIFICATION_RESULT_KEY in details:
        verification_result = details.get(VERIFICATION_RESULT_KEY)
        if not isinstance(verification_result, Mapping):
            raise SeasonStateError(
                "Refusing compaction: a history event carries a non-object "
                "verification_result; unknown evidence shapes are never silently stripped"
            )
        if archive:
            ref = evidence_ref(verification_result)
            archived_hash = str(ref.get("sha256") or "")
            if not dry_run:
                archive_move_evidence(
                    season,
                    tournament_id=tournament_id,
                    canonical_revision=canonical_revision,
                    event_at=event_at,
                    verification_result=verification_result,
                    root=root,
                )
        details.pop(VERIFICATION_RESULT_KEY, None)
        details[VERIFICATION_SUMMARY_KEY] = verification_summary(
            verification_result, tournament_id=tournament_id or None
        )
        if archive:
            details[EVIDENCE_REF_KEY] = ref
    elif VERIFICATION_SUMMARY_KEY in details:
        # Already compacted. A retained evidence reference must resolve and
        # match, otherwise the required archive is missing and compaction fails
        # closed rather than silently treating the summary as verified.
        ref = details.get(EVIDENCE_REF_KEY)
        if isinstance(ref, Mapping):
            load_move_evidence(season, ref, root=root)

    return details, archived_hash


def _semantic_projection(decisions: Mapping[str, Any]) -> dict[str, Any]:
    """Return every top-level decisions field except the replay-only ``history``."""

    return {key: value for key, value in decisions.items() if key != "history"}


def _published_replay_parity(
    service,
    schedule: Mapping[str, Any],
    before_decisions: Mapping[str, Any],
    after_decisions: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Assert published-baseline replay/reconciliation is unchanged.

    Only meaningful for a published-sealed season with an active baseline; for
    un-sealed seasons the reconciliation invariant does not apply and ``None``
    is returned. Any replay/reconciliation change raises rather than committing.
    """

    if not is_published_sealed(before_decisions):
        return None
    baseline = active_baseline(before_decisions)
    if baseline is None:
        return None
    current_projection = projection_from_canonical_schedule(schedule)
    published_projection, attested_additions = published_baseline_reconciliation(
        service,
        baseline,
        current_projection=current_projection,
    )

    def reconcile(decisions: Mapping[str, Any]) -> dict[str, Any]:
        return reconcile_published_baseline(
            published_projection=published_projection,
            current_projection=current_projection,
            history=decisions.get("history") or [],
            attested_additions=attested_additions,
        )

    before_report = reconcile(before_decisions)
    after_report = reconcile(after_decisions)
    if before_report.get("ok") != after_report.get("ok") or before_report.get(
        "unexplained_delta"
    ) != after_report.get("unexplained_delta"):
        raise SeasonStateError(
            "Compaction changed published-baseline replay/reconciliation; refusing to write"
        )
    return {
        "ok": bool(before_report.get("ok")),
        "published_tournament_count": before_report.get("published_tournament_count"),
        "applied_mutation_count": before_report.get("applied_mutation_count"),
    }


def compact_history(
    service,
    *,
    season: str,
    actor: str | None = None,
    note: str = "",
    dry_run: bool = False,
    archive: bool = True,
) -> dict[str, Any]:
    """Migrate oversized inline move evidence into the durable archive.

    Returns a bounded report. With ``dry_run`` nothing is written; otherwise the
    complete original ``decisions.json`` is backed up byte-for-byte, the
    compacted decisions are installed through the store's atomic swap, the
    canonical-state revision and schedule fingerprint are unchanged by
    construction and asserted explicitly, the full canonical semantic projection
    is asserted structurally equal, and published-baseline replay/reconciliation
    is asserted unchanged for sealed seasons.
    """

    snapshot = service.load(season)
    schedule = snapshot.schedule
    decisions = deepcopy(snapshot.decisions)

    before_revision = compute_canonical_state_revision(schedule, decisions)
    before_fingerprint = schedule_fingerprint(schedule.get("plan") or {})
    stored_revision = str(decisions.get(CANONICAL_STATE_REVISION_KEY) or "")

    before_bytes = _json_size(decisions)
    history = decisions.get("history")
    if not isinstance(history, list):
        raise SeasonStateError("Canonical decisions history is not a list; refusing compaction")

    compacted: list[dict[str, Any]] = []
    archived_hashes: list[str] = []
    archived_count = 0
    dropped_count = 0
    already_compacted = 0
    compacted_indices: list[int] = []

    root = str(getattr(service.store, "root", "season"))
    for index, entry in enumerate(history):
        if not isinstance(entry, Mapping):
            compacted.append(entry)
            continue
        original_details = entry.get("details") if isinstance(entry.get("details"), Mapping) else {}
        has_result = VERIFICATION_RESULT_KEY in original_details
        has_summary = VERIFICATION_SUMMARY_KEY in original_details
        if not (has_result or has_summary):
            compacted.append(entry)
            continue
        details, archived_hash = _compact_event_details(
            service,
            season=season,
            entry=entry,
            archive=archive,
            root=root,
            dry_run=dry_run,
        )
        if archived_hash is not None:
            archived_hashes.append(archived_hash)
            archived_count += 1
            compacted_indices.append(index)
        elif has_result:
            # ``archive=False`` dropped the full result without retaining it.
            dropped_count += 1
            compacted_indices.append(index)
        else:
            # Already carried a bounded summary rather than a full result.
            already_compacted += 1
        new_entry = dict(entry)
        new_entry["details"] = details
        compacted.append(new_entry)

    decisions["history"] = compacted
    after_bytes = _json_size(decisions)

    after_revision = compute_canonical_state_revision(schedule, decisions)
    after_fingerprint = schedule_fingerprint(schedule.get("plan") or {})
    if after_revision != before_revision:
        raise SeasonStateError(
            "Compaction changed the canonical-state revision "
            f"({before_revision} -> {after_revision}); refusing to write"
        )
    if after_fingerprint != before_fingerprint:
        raise SeasonStateError(
            "Compaction changed the schedule fingerprint; refusing to write"
        )
    if str(decisions.get(CANONICAL_STATE_REVISION_KEY) or "") != stored_revision:
        raise SeasonStateError("Compaction changed the stored canonical-state revision; refusing to write")
    if _semantic_projection(snapshot.decisions) != _semantic_projection(decisions):
        raise SeasonStateError(
            "Compaction changed a top-level decisions field; refusing to write"
        )

    replay_parity = _published_replay_parity(service, schedule, snapshot.decisions, decisions)

    report = {
        "season": season,
        "dry_run": dry_run,
        "archive": archive,
        "history_events": len(history),
        "compacted_moves": archived_count,
        "dropped_moves": dropped_count,
        "already_compacted": already_compacted,
        "archived_evidence_hashes": archived_hashes,
        "before_revision": before_revision,
        "after_revision": after_revision,
        "before_fingerprint": before_fingerprint,
        "after_fingerprint": after_fingerprint,
        "before_decisions_chars": before_bytes,
        "after_decisions_chars": after_bytes,
        "chars_saved": before_bytes - after_bytes,
        "replay_parity": replay_parity,
    }

    nothing_to_compact = archived_count == 0 and dropped_count == 0

    if dry_run:
        if nothing_to_compact:
            report["backup"] = None
        else:
            original_bytes = _read_original_decisions_bytes(service, season)
            report["backup"] = {
                "dry_run": True,
                "backup_id": compaction_backup_id(original_bytes),
                "decisions_bytes": len(original_bytes),
                "compacted_event_indices": compacted_indices,
            }
        return report

    if nothing_to_compact:
        # Idempotent no-op: nothing oversized remained, so no backup is created
        # and the canonical state is left byte-for-byte untouched.
        report["committed"] = False
        report["backup"] = None
        return report

    # Durable byte-for-byte backup of the complete original, created and
    # verified *before* the compacted state is installed, so a failed swap
    # always leaves the original active state plus a safe immutable backup.
    original_bytes = _read_original_decisions_bytes(service, season)
    backup_manifest = create_compaction_backup(
        season=season,
        decisions_bytes=original_bytes,
        source_canonical_revision=stored_revision,
        schedule_fingerprint=str(schedule.get("fingerprint") or before_fingerprint),
        original_path=str(service.store.decisions_path(season)),
        actor=_operator_identity(actor),
        note=note or "",
        created_at=_now_iso(),
        history_event_count=len(history),
        compacted_event_indices=compacted_indices,
        archived_evidence_hashes=archived_hashes,
        root=root,
    )
    # Verify the backup round-trips before relying on it.
    load_compaction_backup(season, str(backup_manifest["backup_id"]), root=root)
    report["backup"] = backup_manifest

    committed = snapshot.with_decisions(decisions)
    # Commit through the single shared lifecycle boundary so the write stays
    # owned by the persistence lifecycle (and the revision recompute is a no-op
    # by construction: history is not part of the semantic revision hash).
    service._commit(committed)
    report["committed"] = True
    return report


def _read_original_decisions_bytes(service, season: str) -> bytes:
    """Return the exact on-disk bytes of ``decisions.json`` before compaction."""

    path = service.store.decisions_path(season)
    try:
        return path.read_bytes()
    except OSError as exc:
        raise SeasonStateError(f"Cannot read canonical decisions for backup: {exc}") from exc


def history_inventory(
    service,
    *,
    season: str,
    export_root: str = "export",
    pipeline_root: str = ".pipeline",
) -> dict[str, Any]:
    """Return a read-only size/shape inventory for one season and its artifacts."""

    from tournament_scheduler.canonical_inventory import history_inventory as _inventory

    return _inventory(
        season,
        root=str(getattr(service.store, "root", "season")),
        export_root=export_root,
        pipeline_root=pipeline_root,
    )


__all__ = [
    "EVIDENCE_REF_KEY",
    "VERIFICATION_RESULT_KEY",
    "VERIFICATION_SUMMARY_KEY",
    "compact_history",
    "history_inventory",
]
