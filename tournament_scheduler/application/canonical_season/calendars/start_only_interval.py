"""Move a tournament's start for a source that states only a booking start (#869).

A club may state the start of a booking and leave the end unstated. The start
is then the source's explicit authority. The end and duration stay
proposal-derived, so this moves only the date and start. It records no ice-time
override and no accepted source interval, which would pin the derived duration
as source evidence and stop it following the proposal. The derived window is
returned separately, for feasibility classification only.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

from tournament_scheduler.booking.interval_facts import tournament_occupancy_interval_facts
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError

from .associations import _align_tournament_to_authoritative_interval


def start_only_alignment(
    schedule: Mapping[str, Any],
    *,
    tournament_id: str,
    interval_date: str,
    start: str,
    problem: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    """Return (schedule with date and start moved, moved tournament, derived window for classification)."""

    updated = copy.deepcopy(dict(schedule))
    tournaments = (updated.get("plan") or {}).get("tournaments") or []
    target = next((t for t in tournaments if str(t.get("id") or "") == tournament_id), None)
    if target is None:
        raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")
    target["date"] = interval_date
    target["start_time"] = start
    facts = tournament_occupancy_interval_facts(target, problem)
    derived = {"date": interval_date, "start": facts["start_time"], "end": facts["end_time"]}
    return updated, target, derived


def align_booked_interval(
    *,
    schedule: Mapping[str, Any],
    decisions: Mapping[str, Any],
    tournament_id: str,
    stated_interval: Mapping[str, Any],
    interval_date: str,
    actor: str,
    note: str,
    now: str,
    problem: Mapping[str, Any] | None,
    request_id: str,
    reference: str,
) -> tuple[Any, ...]:
    """Align a booked assertion: a full window is an accepted source interval, a start is not.

    Returns (schedule, decisions, tournament, source alignment, override, start-only classification).
    """

    if stated_interval.get("end"):
        return (
            *_align_tournament_to_authoritative_interval(
                schedule=schedule,
                decisions=decisions,
                tournament_id=tournament_id,
                interval={**dict(stated_interval), "date": interval_date},
                actor=actor,
                note=note,
                now=now,
                problem=problem,
                authority="manual_club_confirmation_interval",
                request_id=request_id,
                reference=reference,
                accepted_key="accepted_source_interval",
                freeze_default_equal_interval=True,
            ),
            None,
        )
    updated_schedule, aligned, classification = start_only_alignment(
        schedule, tournament_id=tournament_id, interval_date=interval_date,
        start=str(stated_interval.get("start") or ""), problem=problem,
    )
    return updated_schedule, dict(decisions), aligned, None, None, classification
