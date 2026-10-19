"""Booked-reality fairness for the teams a batch touches.

Each impacted team is reported before and after the candidate, using the same
booked-only basis as ``participation_fairness_report``. Booking evidence is the
current booking-status projection, applied to the candidate's tournaments. Teams
with unconfirmed participation stay marked provisional rather than counted as
booked.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from tournament_scheduler.participation.club_age_group_fairness import (
    build_club_age_group_participation_report,
)


def impacted_booked_fairness(
    service,
    *,
    season: str,
    before_plan: Mapping[str, Any],
    candidate_plan: Mapping[str, Any],
    problem: Mapping[str, Any] | None,
    changed_ids: Sequence[str],
) -> dict[str, Any]:
    resolved = dict(problem or {})
    status = service.booking_status_report(season=season, problem=problem)
    booking_states = {
        str(row.get("tournament_id") or ""): str(row.get("operational_state") or "")
        for row in status.get("tournaments") or []
    }
    shared = {
        "teams": resolved.get("teams") or [],
        "booking_states": booking_states,
        "targets_by_age_group": resolved.get("participation_targets_by_age_group") or {},
        "split_date": resolved.get("christmas_split_date"),
    }
    before = build_club_age_group_participation_report(
        tournaments=before_plan.get("tournaments") or [], **shared
    )
    after = build_club_age_group_participation_report(
        tournaments=candidate_plan.get("tournaments") or [], **shared
    )

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
    before_rows = {(row["club"], row["team"], row["age_group"]): row for row in before["team_rows"]}
    after_rows = {(row["club"], row["team"], row["age_group"]): row for row in after["team_rows"]}

    impacted = []
    for key in impacted_keys:
        before_row = before_rows.get(key) or {}
        after_row = after_rows.get(key) or {}
        before_booked = int((before_row.get("booked_participations") or {}).get("total", 0))
        after_booked = int((after_row.get("booked_participations") or {}).get("total", 0))
        impacted.append(
            {
                "club": key[0],
                "team": key[1],
                "age_group": key[2],
                "before_booked": before_booked,
                "after_booked": after_booked,
                "delta": after_booked - before_booked,
                "after_unconfirmed": int((after_row.get("unconfirmed_participations") or {}).get("total", 0)),
                "before_provisional": bool(before_row.get("provisional", False)),
                "after_provisional": bool(after_row.get("provisional", False)),
            }
        )

    return {
        "basis": "booked_reality",
        "booking_evidence": "current_booking_status",
        "provisional": bool(after["provisional"]),
        "impacted_teams": impacted,
        "lowest_booked_per_team_by_age_group": after["lowest_booked_per_team_by_age_group"],
    }
