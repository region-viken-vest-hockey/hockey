"""Club x age-group participation fairness report (booked reality).

Aggregates tournament participations per club and age group, normalized by the
club's registered team count, so multi-team clubs are compared at club level
rather than by exact sibling label. Only tournaments the booking projection
classifies as ``booked`` count as opportunity; planned, proposed, ambiguous,
stale or action-required participations are reported separately as
unconfirmed coverage, and cancelled tournaments are excluded entirely.

This module is a pure read-only projection over the canonical roster, plan and
booking state. It never persists or changes a planning, booking or approval
decision.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Any, Iterable, Mapping, Optional

from tournament_scheduler.planning.half import tournament_half

BOOKED_OPERATIONAL_STATE = "booked"
HALVES = ("before_christmas", "after_christmas", "unsplit")


def _split_date(value: Any) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _empty_halves() -> dict[str, int]:
    return {half: 0 for half in HALVES}


def build_club_age_group_participation_report(
    *,
    teams: Iterable[Mapping[str, Any]],
    tournaments: Iterable[Mapping[str, Any]],
    booking_states: Mapping[str, str],
    targets_by_age_group: Optional[Mapping[str, Mapping[str, Any]]] = None,
    split_date: Any = None,
    age_group: Optional[str] = None,
) -> dict[str, Any]:
    """Return per club x age-group booked and unconfirmed participation.

    ``teams`` is the registered roster (``club``/``label``/``age_group``).
    ``tournaments`` are plan tournaments (``id``/``date``/``teams``/``cancelled``).
    ``booking_states`` maps tournament id to its booking ``operational_state``.
    ``targets_by_age_group`` holds per-team targets per half. Each row's
    ``provisional`` flag is set when unconfirmed participation remains, so the
    fairness conclusion is explicitly marked as not yet final.
    """

    split = _split_date(split_date)
    roster = [
        (str(team.get("club") or ""), str(team.get("label") or ""), str(team.get("age_group") or ""))
        for team in teams
    ]
    registered: Counter[tuple[str, str]] = Counter()
    for club, _label, group in roster:
        registered[(club, group)] += 1

    booked: dict[tuple[str, str], dict[str, int]] = defaultdict(_empty_halves)
    unconfirmed: dict[tuple[str, str], dict[str, int]] = defaultdict(_empty_halves)
    cancelled: Counter[tuple[str, str]] = Counter()
    team_booked: dict[tuple[str, str, str], dict[str, int]] = defaultdict(_empty_halves)
    team_unconfirmed: dict[tuple[str, str, str], dict[str, int]] = defaultdict(_empty_halves)
    team_cancelled: Counter[tuple[str, str, str]] = Counter()
    tournament_states: Counter[str] = Counter()

    for tournament in tournaments:
        tournament_id = str(tournament.get("id") or "")
        tournament_age_group = str(tournament.get("age_group") or "")
        if age_group and tournament_age_group != age_group:
            continue
        team_identities = [
            (
                str(team.get("club") or ""),
                str(team.get("label") or ""),
                str(team.get("age_group") or tournament_age_group),
            )
            for team in tournament.get("teams") or []
        ]
        if tournament.get("cancelled"):
            for club, label, group in team_identities:
                cancelled[(club, group)] += 1
                team_cancelled[(club, label, group)] += 1
            continue
        state = str(booking_states.get(tournament_id) or "unknown")
        tournament_states[state] += 1
        half = tournament_half(date.fromisoformat(str(tournament["date"])), split)
        is_booked = state == BOOKED_OPERATIONAL_STATE
        club_bucket = booked if is_booked else unconfirmed
        team_bucket = team_booked if is_booked else team_unconfirmed
        for club, label, group in team_identities:
            club_bucket[(club, group)][half] += 1
            team_bucket[(club, label, group)][half] += 1

    keys = set(registered) | set(booked) | set(unconfirmed) | set(cancelled)
    rows: list[dict[str, Any]] = []
    for club, group in sorted(keys):
        if age_group and group != age_group:
            continue
        team_count = registered.get((club, group), 0)
        booked_halves = booked.get((club, group), _empty_halves())
        unconfirmed_halves = unconfirmed.get((club, group), _empty_halves())
        booked_total = sum(booked_halves.values())
        unconfirmed_total = sum(unconfirmed_halves.values())
        targets = (targets_by_age_group or {}).get(group) or {}
        reasons: list[str] = []
        if unconfirmed_total:
            reasons.append("unconfirmed_participations")
        if team_count == 0:
            reasons.append("participation_without_registered_team")
        rows.append(
            {
                "club": club,
                "age_group": group,
                "registered_teams": team_count,
                "booked_participations": {"total": booked_total, **booked_halves},
                "booked_per_team": round(booked_total / team_count, 4) if team_count else None,
                "target_per_team": {
                    half: targets.get(half) for half in ("before_christmas", "after_christmas") if half in targets
                },
                "unconfirmed_participations": {"total": unconfirmed_total, **unconfirmed_halves},
                "cancelled_excluded": cancelled.get((club, group), 0),
                "provisional": bool(reasons),
                "provisional_reasons": reasons,
            }
        )

    rows.sort(key=lambda row: (row["age_group"], row["booked_per_team"] is None, row["booked_per_team"] or 0, row["club"]))

    registered_teams = set(roster)
    team_rows: list[dict[str, Any]] = []
    for club, label, group in sorted(registered_teams | set(team_booked) | set(team_unconfirmed) | set(team_cancelled)):
        if age_group and group != age_group:
            continue
        team_key = (club, label, group)
        booked_halves = team_booked.get(team_key, _empty_halves())
        unconfirmed_halves = team_unconfirmed.get(team_key, _empty_halves())
        unconfirmed_total = sum(unconfirmed_halves.values())
        reasons: list[str] = []
        if unconfirmed_total:
            reasons.append("unconfirmed_participations")
        if team_key not in registered_teams:
            reasons.append("participation_without_registered_team")
        team_rows.append(
            {
                "club": club,
                "team": label,
                "age_group": group,
                "booked_participations": {"total": sum(booked_halves.values()), **booked_halves},
                "unconfirmed_participations": {"total": unconfirmed_total, **unconfirmed_halves},
                "cancelled_excluded": team_cancelled.get(team_key, 0),
                "provisional": bool(reasons),
                "provisional_reasons": reasons,
            }
        )
    team_rows.sort(key=lambda row: (row["age_group"], row["booked_participations"]["total"], row["club"], row["team"]))
    lowest_by_age_group: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["booked_per_team"] is None:
            continue
        current = lowest_by_age_group.get(row["age_group"])
        if current is None or row["booked_per_team"] < current["booked_per_team"]:
            lowest_by_age_group[row["age_group"]] = {
                "club": row["club"],
                "booked_per_team": row["booked_per_team"],
                "provisional": row["provisional"],
            }

    return {
        "basis": "booked_reality",
        "counted_operational_states": [BOOKED_OPERATIONAL_STATE],
        "age_group": age_group,
        "tournament_booking_states": dict(sorted(tournament_states.items())),
        "provisional": any(row["provisional"] for row in rows + team_rows),
        "lowest_booked_per_team_by_age_group": dict(sorted(lowest_by_age_group.items())),
        "rows": rows,
        "team_rows": team_rows,
    }
