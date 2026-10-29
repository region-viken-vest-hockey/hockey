"""Public history of tournament changes between sealed-season publications (#869 item 3).

Each immutable publication record keeps the stable-id projection that was
published. Comparing consecutive publications shows which public tournament
facts changed, and which publication first carried each new value. The result
is derived from those records only. It is read-only, and it allows only public
tournament facts through, so internal notes, operators and source references
never reach the public page.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

# Public tournament facts only. Anything not listed here stays internal.
PUBLIC_CHANGE_FIELDS: tuple[str, ...] = (
    "date",
    "start_time",
    "end_time",
    "duration_minutes",
    "arena",
    "host_club",
    "cancelled",
    "cancellation_reason",
    "participants",
)


def _projection(record: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    projection: dict[str, dict[str, Any]] = {}
    for entry in record.get("tournaments") or []:
        if not isinstance(entry, Mapping):
            continue
        tournament_id = str(entry.get("tournament_id") or entry.get("id") or "")
        if tournament_id:
            projection[tournament_id] = dict(entry)
    return projection


def _comparable(field: str, value: Any) -> Any:
    if field == "participants":
        return sorted(str(item) for item in value or [])
    return value


def _changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for field in PUBLIC_CHANGE_FIELDS:
        old, new = before.get(field), after.get(field)
        if _comparable(field, old) != _comparable(field, new):
            changes.append({"field": field, "before": old, "after": new})
    return changes


def _label(entry: Mapping[str, Any]) -> str:
    parts = [str(entry.get("host_club") or ""), str(entry.get("date") or ""), str(entry.get("start_time") or "")]
    return " ".join(part for part in parts if part)


def public_change_history(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return public change entries per tournament, in publication order.

    ``records`` are the publication records in the order they were published.
    The first publication has nothing to compare against, so it adds no entries.
    ``labels`` gives each tournament a public header from its latest publication.
    """

    ordered = [record for record in records if isinstance(record, Mapping)]
    tournaments: dict[str, list[dict[str, Any]]] = {}
    for previous, current in zip(ordered, ordered[1:]):
        before, after = _projection(previous), _projection(current)
        for tournament_id in sorted(set(before) & set(after)):
            changes = _changes(before[tournament_id], after[tournament_id])
            if not changes:
                continue
            tournaments.setdefault(tournament_id, []).append(
                {
                    "publication_id": str(current.get("publication_id") or ""),
                    "published_at": str(current.get("published_at") or ""),
                    "changes": changes,
                }
            )
    labels = {tournament_id: _label(entry) for tournament_id, entry in _projection(ordered[-1]).items()} if ordered else {}
    return {"tournaments": tournaments, "labels": {tid: labels.get(tid, "") for tid in tournaments}}
