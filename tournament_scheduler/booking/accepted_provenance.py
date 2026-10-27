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


def accepted_manual_provenance(manual: Mapping[str, Any]) -> dict[str, Any]:
    """Return how a manual assertion accepted the booking (#847).

    Manual confirmations have the same audit need as calendar associations. The
    record is read, never rewritten, and its stated interval is reported as
    accepted.
    """

    stated = manual.get("stated_interval") if isinstance(manual.get("stated_interval"), Mapping) else None
    asserted = manual.get("asserted_interval") if isinstance(manual.get("asserted_interval"), Mapping) else {}
    if stated:
        interval = {
            "date": str(stated.get("date") or ""),
            "start": str(stated.get("start") or stated.get("start_time") or ""),
            "end": str(stated.get("end") or stated.get("end_time") or ""),
        }
    else:
        interval = {
            "date": str(manual.get("date") or ""),
            "start": str(manual.get("start_time") or ""),
            "end": str(asserted.get("end_time") or ""),
        }
    return {
        "assertion_id": str(manual.get("id") or ""),
        "accepted_at": str(manual.get("asserted_at") or ""),
        "accepted_by": str(manual.get("asserted_by") or ""),
        "note": str(manual.get("note") or ""),
        "reference": str(manual.get("reference") or ""),
        "source_revision": str(manual.get("source_revision") or ""),
        "accepted_interval": interval,
    }
