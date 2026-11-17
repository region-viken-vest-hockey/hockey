"""Canonical tournament identity facts used by booking evidence."""

from __future__ import annotations

from typing import Any, Mapping


def _tournaments_by_id(plan: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    return {
        str(t.get("id") or ""): t
        for t in ((plan or {}).get("tournaments") or [])
        if isinstance(t, Mapping) and str(t.get("id") or "")
    }


def tournament_booking_facts(tournament: Mapping[str, Any]) -> dict[str, str]:
    return {
        "host_club": str(tournament.get("host_club") or ""),
        "arena": str(tournament.get("arena") or ""),
        "date": str(tournament.get("date") or ""),
        "start_time": str(tournament.get("start_time") or ""),
        "age_group": str(tournament.get("age_group") or ""),
    }
