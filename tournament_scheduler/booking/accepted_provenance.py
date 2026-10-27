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


# Keys are the decision-store keys owned by calendar_bookings.py. They are repeated
# here because that module imports this one.
_HISTORY_KEYS = (("calendar_booking_associations", "calendar"), ("manual_booking_assertions", "manual"))
# Each recognised end state, with the record fields that say when, by what and why it ended.
_ENDING_FIELDS = {
    "superseded": (("superseded_at",), ("superseded_by", "superseded_by_operation"), ("supersede_reason",)),
    "released": (("released_at",), ("released_by",), ("release_note",)),
    "revoked": (("revoked_at",), ("revoked_by",), ("revoke_reason",)),
}
_HISTORY_STATUSES = frozenset(_ENDING_FIELDS)


def acceptance_history(decisions: Mapping[str, Any] | None, tournament_id: str) -> list[dict[str, Any]]:
    """Return the acceptances that were superseded or released for one tournament (#847).

    Each entry keeps who accepted it, when, why, and what replaced it, so an
    operator can see the earlier acceptance next to the current one. Records are
    read only.
    """

    entries: list[dict[str, Any]] = []
    for key, kind in _HISTORY_KEYS:
        for record in (decisions or {}).get(key) or []:
            if not isinstance(record, Mapping) or str(record.get("tournament_id") or "") != tournament_id:
                continue
            status = str(record.get("status") or "")
            if status not in _HISTORY_STATUSES:
                # Only the decision store's recognised end states are history. An
                # unknown or missing status is not evidence that an acceptance ended.
                continue
            ended_at, ended_by, reason = (_first(record, keys) for keys in _ENDING_FIELDS[status])
            entries.append(
                {
                    "kind": kind,
                    "record_id": str(record.get("id") or ""),
                    "status": status,
                    "accepted_at": str(record.get("asserted_at") or record.get("created_at") or ""),
                    "accepted_by": str(record.get("asserted_by") or record.get("created_by") or ""),
                    "reference": str(record.get("reference") or ""),
                    "note": str(record.get("note") or ""),
                    "ended_at": ended_at,
                    "ended_by": ended_by,
                    "ended_reason": reason,
                }
            )
    return sorted(entries, key=lambda entry: entry["accepted_at"])


def _first(record: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    return next((str(record[key]) for key in keys if record.get(key)), "")
