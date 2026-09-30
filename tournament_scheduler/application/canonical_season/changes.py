"""Read-only canonical season change-request ledger projection."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from tournament_scheduler.canonical_state import canonical_state_revision
from tournament_scheduler.request_constraints import ACTIVE as REQUEST_CONSTRAINT_ACTIVE


SCHEMA_VERSION = 1
UNKNOWN_REQUEST_ID = "unknown"


def _text(value: Any) -> str:
    return str(value or "")


def _team_key(team: Mapping[str, Any]) -> str:
    club = _text(team.get("club"))
    label = _text(team.get("label"))
    age_group = _text(team.get("age_group"))
    return ":".join(part for part in (club, label, age_group) if part)


def _placement(tournament: Mapping[str, Any] | None) -> dict[str, Any]:
    tournament = tournament or {}
    return {
        "date": tournament.get("date"),
        "arena": tournament.get("arena"),
        "host_club": tournament.get("host_club"),
        "start_time": tournament.get("start_time"),
    }


def _roster(tournament: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    tournament = tournament or {}
    return [
        {
            "club": team.get("club"),
            "label": team.get("label"),
            "age_group": team.get("age_group") or tournament.get("age_group"),
        }
        for team in tournament.get("teams", []) or []
        if isinstance(team, Mapping)
    ]


def _status_rank(status: str) -> int:
    order = {
        "needs_action": 0,
        "partially_resolved": 1,
        "active": 2,
        "resolved": 3,
        "superseded": 4,
        "released": 5,
        "unknown": 6,
    }
    return order.get(status, 99)


def _combine_status(statuses: list[str]) -> str:
    values = {status for status in statuses if status}
    if not values:
        return "unknown"
    if "needs_action" in values and (values - {"needs_action"}):
        return "partially_resolved"
    if "needs_action" in values:
        return "needs_action"
    if "active" in values and "resolved" in values:
        return "partially_resolved"
    if "active" in values:
        return "active"
    if "resolved" in values:
        return "resolved"
    if values == {"released"}:
        return "released"
    if values == {"superseded"}:
        return "superseded"
    if "released" in values and "superseded" in values:
        return "released"
    return sorted(values, key=_status_rank)[0]


def _request_bucket(requests: dict[str, dict[str, Any]], request_id: str) -> dict[str, Any]:
    key = request_id.strip() or UNKNOWN_REQUEST_ID
    if key not in requests:
        requests[key] = {
            "request_id": key,
            "created_at": None,
            "sources": [],
            "actors": [],
            "notes": [],
            "types": [],
            "affected_tournaments": [],
            "affected_teams": [],
            "constraints": [],
            "mutations": [],
            "protections": [],
            "withdrawals": [],
            "statuses": [],
            "status": "unknown",
            "result_summary": "",
        }
    return requests[key]


def _add_unique(bucket: dict[str, Any], field: str, value: Any) -> None:
    text = _text(value)
    if text and text not in bucket[field]:
        bucket[field].append(text)


def _touch_created(bucket: dict[str, Any], timestamp: Any) -> None:
    text = _text(timestamp)
    if text and (bucket.get("created_at") is None or text < str(bucket.get("created_at"))):
        bucket["created_at"] = text


def _index_protections(decisions: Mapping[str, Any]) -> dict[tuple[str, str, str], set[str]]:
    index: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for protection in decisions.get("change_protections", []) or []:
        if not isinstance(protection, Mapping):
            continue
        request_id = _text(protection.get("request_id"))
        if not request_id:
            continue
        key = (
            _text(protection.get("source_event")),
            _text(protection.get("tournament_id")),
            _text(protection.get("source_revision")),
        )
        index[key].add(request_id)
    return index


def _history_request_id(
    event: Mapping[str, Any],
    protection_index: Mapping[tuple[str, str, str], set[str]],
) -> tuple[str, bool]:
    details = event.get("details") if isinstance(event.get("details"), Mapping) else {}
    direct = _text(event.get("request_id") or details.get("request_id"))
    if direct:
        return direct, False
    # Older move history did not persist request_id directly. Correlate only
    # through exactly one matching protection record for the same source event,
    # tournament and pre-mutation canonical revision.
    key = (
        _text(event.get("event")),
        _text(event.get("tournament_id")),
        _text(details.get("before_canonical_revision")),
    )
    matches = sorted(protection_index.get(key, set()))
    if len(matches) == 1:
        return matches[0], True
    return UNKNOWN_REQUEST_ID, False


def _mutation_from_history(
    event: Mapping[str, Any],
    *,
    request_id: str,
    inferred_request_id: bool,
) -> dict[str, Any]:
    details = event.get("details") if isinstance(event.get("details"), Mapping) else {}
    mutation = {
        "event": _text(event.get("event")),
        "timestamp": event.get("timestamp") or event.get("created_at"),
        "actor": event.get("actor"),
        "note": event.get("note") or details.get("note"),
        "tournament_id": event.get("tournament_id") or None,
        "request_id": None if request_id == UNKNOWN_REQUEST_ID else request_id,
        "request_id_inferred": bool(inferred_request_id),
        "details": {},
    }
    name = mutation["event"]
    if name == "move":
        mutation["details"] = {
            "before": details.get("old_placement"),
            "after": details.get("new_placement"),
        }
    elif name == "participant_replacement":
        mutation["details"] = {
            "removed_team": details.get("removed_team"),
            "added_team": details.get("added_team"),
            "participation_counts": details.get("participation_counts"),
        }
    elif name in {"participant_removal", "participant_restoration"}:
        mutation["details"] = {
            key: details.get(key)
            for key in ("removed_team", "restored_team", "tournament_ids", "withdrawal_ids")
            if key in details
        }
    elif name == "batch_maintenance":
        mutation["details"] = {
            key: details.get(key)
            for key in ("operations", "scope", "cancelled_tournaments", "request_id")
            if key in details
        }
    else:
        mutation["details"] = {
            key: value
            for key, value in details.items()
            if key in {"request_id", "constraint_id", "released_constraint_ids", "type"}
        }
    return mutation


def change_request_ledger(service, season: str) -> dict[str, Any]:
    """Return the read-only request-grouped change ledger for one season."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule.get("plan") or {}
    tournaments = {
        _text(tournament.get("id")): tournament
        for tournament in plan.get("tournaments", []) or []
        if isinstance(tournament, Mapping)
    }
    requests: dict[str, dict[str, Any]] = {}

    # Request constraints with derived satisfaction/lifecycle semantics.
    constraints = service.request_constraint_report(season, include_released=True).get("constraints", [])
    for constraint in constraints:
        if not isinstance(constraint, Mapping):
            continue
        request_id = _text(constraint.get("request_id")) or UNKNOWN_REQUEST_ID
        bucket = _request_bucket(requests, request_id)
        _touch_created(bucket, constraint.get("created_at"))
        _add_unique(bucket, "sources", constraint.get("created_by"))
        _add_unique(bucket, "actors", constraint.get("created_by"))
        _add_unique(bucket, "notes", constraint.get("note"))
        _add_unique(bucket, "types", constraint.get("type"))
        for team in constraint.get("teams", []) or []:
            if isinstance(team, Mapping):
                _add_unique(bucket, "affected_teams", _team_key(team))
        violations = constraint.get("violations", []) or []
        for violation in violations:
            if isinstance(violation, Mapping):
                _add_unique(bucket, "affected_tournaments", violation.get("tournament_id"))
        status = _text(constraint.get("status"))
        if status == REQUEST_CONSTRAINT_ACTIVE:
            derived_status = "resolved" if constraint.get("satisfied") else "needs_action"
        else:
            derived_status = "released"
        bucket["statuses"].append(derived_status)
        bucket["constraints"].append(
            {
                "id": constraint.get("id"),
                "type": constraint.get("type"),
                "status": status,
                "satisfied": constraint.get("satisfied"),
                "violations": violations,
                "date_from": constraint.get("date_from"),
                "date_to": constraint.get("date_to"),
                "min_days": constraint.get("min_days"),
                "teams": constraint.get("teams") or [],
                "note": constraint.get("note") or "",
            }
        )

    # Participation withdrawals have their own domain lifecycle report.
    for withdrawal in service.withdrawal_report(season, include_released=True).get("withdrawals", []) or []:
        if not isinstance(withdrawal, Mapping):
            continue
        request_id = _text(withdrawal.get("request_id")) or UNKNOWN_REQUEST_ID
        bucket = _request_bucket(requests, request_id)
        _touch_created(bucket, withdrawal.get("created_at"))
        _add_unique(bucket, "sources", withdrawal.get("created_by"))
        _add_unique(bucket, "actors", withdrawal.get("created_by"))
        _add_unique(bucket, "notes", withdrawal.get("note"))
        _add_unique(bucket, "types", "participation_withdrawal")
        team = withdrawal.get("team") if isinstance(withdrawal.get("team"), Mapping) else {}
        _add_unique(bucket, "affected_teams", _team_key(team))
        for tournament_id in withdrawal.get("tournament_ids", []) or []:
            _add_unique(bucket, "affected_tournaments", tournament_id)
        if _text(withdrawal.get("status")) != "active":
            derived_status = "released"
        elif withdrawal.get("superseded"):
            derived_status = "superseded"
        elif withdrawal.get("restored"):
            derived_status = "needs_action"
        else:
            derived_status = "active"
        bucket["statuses"].append(derived_status)
        bucket["withdrawals"].append(dict(withdrawal))

    # Protections retain request provenance for accepted placement/roster changes.
    for protection in decisions.get("change_protections", []) or []:
        if not isinstance(protection, Mapping):
            continue
        request_id = _text(protection.get("request_id")) or UNKNOWN_REQUEST_ID
        bucket = _request_bucket(requests, request_id)
        _touch_created(bucket, protection.get("created_at"))
        _add_unique(bucket, "sources", protection.get("created_by"))
        _add_unique(bucket, "actors", protection.get("created_by"))
        _add_unique(bucket, "notes", protection.get("note"))
        _add_unique(bucket, "types", protection.get("source_event") or protection.get("kind"))
        _add_unique(bucket, "affected_tournaments", protection.get("tournament_id"))
        team = protection.get("team") if isinstance(protection.get("team"), Mapping) else {}
        _add_unique(bucket, "affected_teams", _team_key(team))
        bucket["protections"].append(dict(protection))
        bucket["statuses"].append("released" if _text(protection.get("status")) == "released" else "resolved")

    protection_index = _index_protections(decisions)
    tracked_events = {
        "move",
        "participant_swap",
        "participant_replacement",
        "participant_removal",
        "participant_restoration",
        "batch_maintenance",
        "add_request_constraint",
        "release_request_constraint",
        "ban_date",
        "unban_date",
    }
    for event in decisions.get("history", []) or []:
        if not isinstance(event, Mapping):
            continue
        event_name = _text(event.get("event"))
        if event_name not in tracked_events:
            continue
        request_id, inferred = _history_request_id(event, protection_index)
        bucket = _request_bucket(requests, request_id)
        _touch_created(bucket, event.get("timestamp") or event.get("created_at"))
        _add_unique(bucket, "actors", event.get("actor"))
        _add_unique(bucket, "notes", event.get("note"))
        _add_unique(bucket, "types", event_name)
        _add_unique(bucket, "affected_tournaments", event.get("tournament_id"))
        details = event.get("details") if isinstance(event.get("details"), Mapping) else {}
        for tournament_id in details.get("tournament_ids", []) or details.get("scope", []) or []:
            _add_unique(bucket, "affected_tournaments", tournament_id)
        for operation in details.get("operations", []) or []:
            if isinstance(operation, Mapping):
                _add_unique(bucket, "affected_tournaments", operation.get("tournament_id"))
        mutation = _mutation_from_history(event, request_id=request_id, inferred_request_id=inferred)
        bucket["mutations"].append(mutation)

    for bucket in requests.values():
        for tournament_id in list(bucket["affected_tournaments"]):
            tournament = tournaments.get(tournament_id)
            if tournament:
                for team in _roster(tournament):
                    _add_unique(bucket, "affected_teams", _team_key(team))
        bucket["affected_tournaments"].sort()
        bucket["affected_teams"].sort()
        bucket["sources"].sort()
        bucket["actors"].sort()
        bucket["types"].sort()
        bucket["status"] = _combine_status(bucket.pop("statuses", []))
        if bucket["status"] == "unknown" and bucket["mutations"]:
            bucket["status"] = "resolved"
        if bucket["mutations"]:
            changed = [m.get("event") for m in bucket["mutations"] if m.get("event")]
            bucket["result_summary"] = ", ".join(dict.fromkeys(changed))
        elif bucket["constraints"]:
            bucket["result_summary"] = "constraint recorded"
        elif bucket["withdrawals"]:
            bucket["result_summary"] = "withdrawal recorded"
        bucket["current_state"] = {
            tournament_id: {
                "placement": _placement(tournaments.get(tournament_id)),
                "roster": _roster(tournaments.get(tournament_id)),
            }
            for tournament_id in bucket["affected_tournaments"]
            if tournament_id in tournaments
        }

    ordered = sorted(
        requests.values(),
        key=lambda item: (item.get("created_at") or "", item.get("request_id") or ""),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "season": season,
        "revision": schedule.get("revision"),
        "canonical_state_revision": canonical_state_revision(schedule, decisions),
        "request_count": len(ordered),
        "unresolved_count": sum(1 for item in ordered if item.get("status") in {"needs_action", "partially_resolved"}),
        "requests": ordered,
    }


def _md_escape(value: Any) -> str:
    return _text(value).replace("|", "\\|").replace("\n", " ")


def _summarize_request(item: Mapping[str, Any]) -> str:
    notes = item.get("notes") or []
    if notes:
        return _text(notes[0])
    types = item.get("types") or []
    return ", ".join(_text(value) for value in types) or "—"


def render_change_log_markdown(ledger: Mapping[str, Any]) -> str:
    """Render a deterministic human-readable Markdown change ledger."""

    lines = [
        f"# Change-request ledger — {ledger.get('season')}",
        "",
        "Generated from canonical `schedule.json` and `decisions.json`; do not edit this file manually.",
        "",
        f"Canonical state revision: `{ledger.get('canonical_state_revision') or ''}`",
        "",
        "## Overview",
        "",
        "| Request | Source | Request | Affected | Result | Status |",
        "|---|---|---|---|---|---|",
    ]
    requests = ledger.get("requests") or []
    if not requests:
        lines.append("| — | — | No recorded change requests | — | — | — |")
    for item in requests:
        affected = ", ".join(item.get("affected_tournaments") or item.get("affected_teams") or []) or "—"
        source = ", ".join(item.get("sources") or item.get("actors") or []) or "—"
        lines.append(
            "| "
            + " | ".join(
                _md_escape(value)
                for value in (
                    f"`{item.get('request_id')}`",
                    source,
                    _summarize_request(item),
                    affected,
                    item.get("result_summary") or "—",
                    item.get("status") or "unknown",
                )
            )
            + " |"
        )
    lines.extend(["", "## Details", ""])
    for item in requests:
        lines.extend(
            [
                f"### `{item.get('request_id')}`",
                "",
                f"- Status: **{item.get('status') or 'unknown'}**",
                f"- Created/received: {item.get('created_at') or 'unknown'}",
                f"- Source/actor: {', '.join(item.get('sources') or item.get('actors') or []) or 'unknown'}",
                f"- Type(s): {', '.join(item.get('types') or []) or 'unknown'}",
                f"- Affected tournaments: {', '.join(f'`{tid}`' for tid in item.get('affected_tournaments') or []) or '—'}",
                f"- Affected teams: {', '.join(item.get('affected_teams') or []) or '—'}",
            ]
        )
        if item.get("notes"):
            lines.append("- Notes:")
            for note in item.get("notes") or []:
                lines.append(f"  - {_text(note)}")
        if item.get("constraints"):
            lines.append("- Constraints:")
            for constraint in item.get("constraints") or []:
                state = "satisfied" if constraint.get("satisfied") else "unsatisfied"
                if constraint.get("status") != REQUEST_CONSTRAINT_ACTIVE:
                    state = _text(constraint.get("status"))
                lines.append(
                    f"  - `{constraint.get('id')}` {constraint.get('type')} ({state})"
                )
        if item.get("mutations"):
            lines.append("- Mutations:")
            for mutation in item.get("mutations") or []:
                tournament = f" `{mutation.get('tournament_id')}`" if mutation.get("tournament_id") else ""
                inferred = " (request inferred from protections)" if mutation.get("request_id_inferred") else ""
                lines.append(
                    f"  - {mutation.get('timestamp') or 'unknown'}: {mutation.get('event')}{tournament}{inferred}"
                )
                details = mutation.get("details") if isinstance(mutation.get("details"), Mapping) else {}
                before = details.get("before")
                after = details.get("after")
                if before or after:
                    lines.append(f"    - Before → after: `{before}` → `{after}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_change_log_markdown(ledger: Mapping[str, Any], *, root: str | Path = "season") -> Path:
    """Write the deterministic generated Markdown ledger under season/<season>."""

    season = _text(ledger.get("season"))
    target = Path(root) / season / "change-log.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_change_log_markdown(ledger), encoding="utf-8")
    return target
