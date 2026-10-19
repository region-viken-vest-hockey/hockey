"""Booked-reality fairness for the teams a batch touches.

Booking state is a function of the placement it describes. A manual assertion
records a specific slot, and calendar association evidence is checked against
the tournament it was linked to. So the before and after projections each run
the booking projection against their own plan and decisions. A tournament that
moves away from its confirmed slot therefore stops counting as booked, instead
of carrying its old evidence into the candidate.

Teams are reported with booked totals and per-half counts before and after, so
half-season deprivation stays visible next to the total.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from tournament_scheduler.booking.calendar_bookings import (
    booking_status_report as _booking_status_report,
)
from tournament_scheduler.participation.club_age_group_fairness import (
    HALVES,
    build_club_age_group_participation_report,
)


def impacted_booked_fairness(
    *,
    before_plan: Mapping[str, Any],
    candidate_plan: Mapping[str, Any],
    before_decisions: Mapping[str, Any] | None,
    after_decisions: Mapping[str, Any] | None,
    problem: Mapping[str, Any] | None,
    changed_ids: Sequence[str],
) -> dict[str, Any]:
    resolved = dict(problem or {})
    before = _fairness(before_plan, before_decisions, resolved)
    after = _fairness(candidate_plan, after_decisions, resolved)

    changed = set(changed_ids)
    impacted_keys = sorted(
        {
            (str(team.get("club") or ""), str(team.get("label") or ""), str(team.get("age_group") or ""))
            for plan in (before_plan, candidate_plan)
            for tournament in plan.get("tournaments") or []
            if str(tournament.get("id") or "") in changed
            for team in tournament.get("teams") or []
        }
    )

    impacted = []
    for key in impacted_keys:
        before_row = before["rows"].get(key, {})
        after_row = after["rows"].get(key, {})
        before_halves = _halves(before_row)
        after_halves = _halves(after_row)
        before_total = int((before_row.get("booked_participations") or {}).get("total", 0))
        after_total = int((after_row.get("booked_participations") or {}).get("total", 0))
        impacted.append(
            {
                "club": key[0],
                "team": key[1],
                "age_group": key[2],
                "before_booked": before_total,
                "after_booked": after_total,
                "delta": after_total - before_total,
                "before_booked_halves": before_halves,
                "after_booked_halves": after_halves,
                "half_delta": {half: after_halves[half] - before_halves[half] for half in HALVES},
                "after_unconfirmed": int((after_row.get("unconfirmed_participations") or {}).get("total", 0)),
                "before_provisional": bool(before_row.get("provisional", False)),
                "after_provisional": bool(after_row.get("provisional", False)),
            }
        )

    return {
        "basis": "booked_reality",
        "booking_evidence": "projected_against_each_plan",
        "provisional": bool(after["provisional"]),
        "impacted_teams": impacted,
        "lowest_booked_per_team_by_age_group": after["lowest_booked_per_team_by_age_group"],
    }


def _fairness(plan: Mapping[str, Any], decisions: Mapping[str, Any] | None, problem: Mapping[str, Any]) -> dict[str, Any]:
    status = _booking_status_report(problem=problem, plan=plan, decisions=decisions)
    booking_states = {
        str(row.get("tournament_id") or ""): str(row.get("operational_state") or "")
        for row in status.get("tournaments") or []
    }
    report = build_club_age_group_participation_report(
        teams=problem.get("teams") or [],
        tournaments=plan.get("tournaments") or [],
        booking_states=booking_states,
        targets_by_age_group=problem.get("participation_targets_by_age_group") or {},
        split_date=problem.get("christmas_split_date"),
    )
    rows = {(row["club"], row["team"], row["age_group"]): row for row in report["team_rows"]}
    return {
        "rows": rows,
        "provisional": report["provisional"],
        "lowest_booked_per_team_by_age_group": report["lowest_booked_per_team_by_age_group"],
    }


def _halves(row: Mapping[str, Any]) -> dict[str, int]:
    booked = row.get("booked_participations") or {}
    return {half: int(booked.get(half, 0)) for half in HALVES}
