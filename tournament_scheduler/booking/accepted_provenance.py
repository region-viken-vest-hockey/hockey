"""Provenance of a confirmed calendar booking, for the operator's audit trail (#847).

Pure and read-only: it reports the record that already holds the accepted
booking (who confirmed it, when, why, from which source revision, and the
interval that was accepted). It never rewrites that record, so a later
rebind or identity review cannot silently amend the accepted facts.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping


def accepted_association_provenance(
    records: Iterable[Mapping[str, Any]],
    tournament_id: str,
) -> dict[str, Any] | None:
    """Return how the active association for ``tournament_id`` was accepted, or None."""

    for record in records:
        if str(record.get("tournament_id") or "") != tournament_id:
            continue
        return {
            "association_id": str(record.get("id") or ""),
            "accepted_at": str(record.get("created_at") or ""),
            "accepted_by": str(record.get("created_by") or ""),
            "note": str(record.get("note") or ""),
            "source_revision": str(record.get("source_revision") or ""),
            "event_fingerprint": str(record.get("event_fingerprint") or ""),
            "accepted_interval": {
                "date": str(record.get("date") or ""),
                "start": str(record.get("start") or ""),
                "end": str(record.get("end") or ""),
            },
            "accepted_title": str(record.get("title") or ""),
        }
    return None
