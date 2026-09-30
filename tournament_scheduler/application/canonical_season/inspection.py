"""Read-only canonical-season inspection projections.

These projections answer routine operational investigation questions using
domain facts instead of exposing callers to the raw ``schedule.json`` /
``decisions.json`` schema:

- what is this tournament, who is in it, and what is its approval/booking state?
- which request constraints are relevant to a team, tournament or date?
- which registered same-age teams could replace a participant?

They never mutate canonical state. They deliberately return domain semantics
(approval resolution, derived constraint satisfaction, registered-team pool)
so a harness does not have to reimplement canonical readers or validation.
"""

from __future__ import annotations

from typing import Any, Mapping

from tournament_scheduler.canonical_baseline import resolve_approval
from tournament_scheduler.canonical_state import canonical_state_revision
from tournament_scheduler.infrastructure.canonical_season_store import SeasonStateError
from tournament_scheduler.request_constraints import (
    request_constraint_report as build_request_constraint_report,
    team_identity,
)

from .shared import _resolve_plan_problem

_HISTORY_LIMIT = 20


def _find_tournament(plan: Mapping[str, Any], tournament_id: str) -> dict[str, Any]:
    for tournament in plan.get("tournaments", []) or []:
        if str(tournament.get("id") or "") == tournament_id:
            return dict(tournament)
    raise SeasonStateError(f"Unknown tournament id in canonical schedule: {tournament_id}")


def _parse_window_date(value: Any) -> str:
    return str(value or "")


def _constraint_window_contains(constraint: Mapping[str, Any], date: str) -> bool:
    if not date:
        return False
    start = _parse_window_date(constraint.get("date_from"))
    end = _parse_window_date(constraint.get("date_to") or constraint.get("date_from"))
    if not start:
        return False
    return start <= date <= (end or start)


def _constraint_matches_team(constraint: Mapping[str, Any], identity: tuple[str, str, str]) -> bool:
    for team in constraint.get("teams") or []:
        if team_identity(team) == identity:
            return True
    return False


def _constraint_affects_tournament(
    constraint: Mapping[str, Any],
    tournament: Mapping[str, Any],
) -> bool:
    age_group = str(tournament.get("age_group") or "")
    participants = {
        team_identity(team, age_group)
        for team in tournament.get("teams", []) or []
        if not bool((team or {}).get("guest", False))
    }
    if any(_constraint_matches_team(constraint, identity) for identity in participants):
        return True
    return _constraint_window_contains(constraint, str(tournament.get("date") or ""))


def _filter_constraint_report(
    report: list[dict[str, Any]],
    *,
    team: str | None,
    tournament: Mapping[str, Any] | None,
    date: str | None,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for constraint in report:
        if team is not None:
            wanted = str(team)
            matches = any(
                str(item.get("label") or "") == wanted
                or str(item.get("club") or "") == wanted
                for item in constraint.get("teams") or []
            )
            if not matches:
                continue
        if tournament is not None and not _constraint_affects_tournament(constraint, tournament):
            continue
        if date is not None and not _constraint_window_contains(constraint, str(date)):
            continue
        filtered.append(constraint)
    return filtered


def _constraint_projection(report: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "constraints": report,
        "count": len(report),
        "unsatisfied_count": sum(
            1 for item in report if item.get("satisfied") is False
        ),
    }


def constraint_inspection(
    service,
    *,
    season: str,
    team: str | None = None,
    tournament_id: str | None = None,
    date: str | None = None,
    include_released: bool = False,
) -> dict[str, Any]:
    """Read-only request-constraint projection filtered by team/tournament/date."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule.get("plan") or {}
    tournament = _find_tournament(plan, tournament_id) if tournament_id else None
    report = build_request_constraint_report(
        plan,
        decisions,
        include_released=include_released,
    )
    filtered = _filter_constraint_report(
        report,
        team=team,
        tournament=tournament,
        date=date,
    )
    return {
        "season": season,
        "revision": schedule.get("revision"),
        "canonical_state_revision": canonical_state_revision(schedule, decisions),
        "filters": {
            "team": team,
            "tournament_id": tournament_id,
            "date": date,
            "include_released": include_released,
        },
        **_constraint_projection(filtered),
    }


def _booking_row(service, season: str, tournament_id: str) -> dict[str, Any] | None:
    from .calendars import booking_status_report

    report = booking_status_report(service, season=season)
    for row in report.get("tournaments") or []:
        if str(row.get("tournament_id") or "") == tournament_id:
            return dict(row)
    return None


def _tournament_history(decisions: Mapping[str, Any], tournament_id: str) -> list[dict[str, Any]]:
    events = [
        event
        for event in decisions.get("history") or []
        if str((event or {}).get("tournament_id") or "") == tournament_id
    ]
    projections = [
        {
            "event": event.get("event"),
            "actor": event.get("actor"),
            "at": event.get("at"),
            "note": event.get("note") or "",
            "tournament_fingerprint": event.get("tournament_fingerprint"),
            "previous_fingerprint": event.get("previous_fingerprint"),
            "schedule_fingerprint": event.get("schedule_fingerprint"),
        }
        for event in events
    ]
    # Most recent first; bulky verification ``details`` stay in the canonical
    # evidence and are not part of the routine inspection projection.
    return list(reversed(projections[-_HISTORY_LIMIT:]))


def tournament_inspection(
    service,
    *,
    season: str,
    tournament_id: str,
    include_released_constraints: bool = False,
) -> dict[str, Any]:
    """Read-only domain projection of one canonical tournament and its context."""

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule.get("plan") or {}
    tournament = _find_tournament(plan, tournament_id)
    age_group = str(tournament.get("age_group") or "")
    approval = resolve_approval(
        (decisions.get("decisions") or {}).get(tournament_id, {}),
        tournament,
    )
    roster = [
        {
            "club": team.get("club"),
            "label": team.get("label"),
            "age_group": team.get("age_group") or age_group,
            "guest": bool(team.get("guest", False)),
        }
        for team in tournament.get("teams", []) or []
    ]
    report = build_request_constraint_report(
        plan,
        decisions,
        include_released=include_released_constraints,
    )
    relevant = _filter_constraint_report(
        report,
        team=None,
        tournament=tournament,
        date=None,
    )
    return {
        "season": season,
        "revision": schedule.get("revision"),
        "canonical_state_revision": canonical_state_revision(schedule, decisions),
        "tournament": {
            "id": tournament.get("id"),
            "date": tournament.get("date"),
            "arena": tournament.get("arena"),
            "age_group": tournament.get("age_group"),
            "host_club": tournament.get("host_club"),
            "start_time": tournament.get("start_time"),
            "cancelled": bool(tournament.get("cancelled", False)),
            "cancellation_reason": tournament.get("cancellation_reason") or "",
            "placement_state": tournament.get("placement_state"),
            "team_count": len(roster),
            "game_count": len(tournament.get("games", []) or []),
            "teams": roster,
        },
        "approval": approval,
        "booking": _booking_row(service, season, tournament_id),
        "constraints": _constraint_projection(relevant),
        "history": _tournament_history(decisions, tournament_id),
    }


def _participation_index(
    plan: Mapping[str, Any],
    age_group: str,
) -> tuple[dict[tuple[str, str, str], int], dict[tuple[str, str, str], list[tuple[str, str]]]]:
    counts: dict[tuple[str, str, str], int] = {}
    dates: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    for tournament in plan.get("tournaments", []) or []:
        if tournament.get("cancelled"):
            continue
        if str(tournament.get("age_group") or "") != age_group:
            continue
        tournament_id = str(tournament.get("id") or "")
        tournament_date = str(tournament.get("date") or "")
        for team in tournament.get("teams", []) or []:
            if bool((team or {}).get("guest", False)):
                continue
            identity = team_identity(team, age_group)
            counts[identity] = counts.get(identity, 0) + 1
            dates.setdefault(identity, []).append((tournament_date, tournament_id))
    return counts, dates


def replacement_candidates(
    service,
    *,
    season: str,
    tournament_id: str,
    replace_team_label: str | None = None,
    legal_only: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Read-only registered same-age teams that could replace a participant.

    Candidates come from the promoted verification context's registered team
    pool, matching the validation used by ``season replace-participant``. Teams
    already participating in the tournament and guest participants are excluded;
    each candidate is annotated with its season participation count and whether
    it already plays on the tournament date.

    When ``replace_team_label`` is given the projection evaluates each candidate
    through the same read-only replacement gates used by the mutation dry-run
    and returns an explicit ``verdict`` (``safe_to_apply``/``blocked``). This is
    the supported replacement-candidate discovery path, so callers never need to
    probe the mutation command themselves.
    """

    snapshot = service.load(season)
    schedule, decisions = snapshot.schedule, snapshot.decisions
    plan = schedule.get("plan") or {}
    tournament = _find_tournament(plan, tournament_id)
    age_group = str(tournament.get("age_group") or "")
    problem = _resolve_plan_problem(schedule, None, decisions)
    registered = (problem or {}).get("teams") or []
    if not registered:
        raise SeasonStateError(
            "Replacement candidates require the promoted verification context's "
            f"registered team pool; none is available for season {season}"
        )
    existing = {
        team_identity(team, age_group)
        for team in tournament.get("teams", []) or []
    }
    counts, dates = _participation_index(plan, age_group)
    tournament_date = str(tournament.get("date") or "")
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for team in registered:
        if not isinstance(team, Mapping):
            continue
        if str(team.get("age_group") or "") != age_group:
            continue
        if bool(team.get("guest", False)):
            continue
        identity = team_identity(team, age_group)
        if identity in existing or identity in seen:
            continue
        seen.add(identity)
        same_date = sorted(
            {tid for date, tid in dates.get(identity, []) if date == tournament_date}
        )
        candidates.append(
            {
                "club": identity[0],
                "label": identity[1],
                "age_group": identity[2],
                "season_participations": counts.get(identity, 0),
                "plays_on_tournament_date": bool(same_date),
                "same_date_tournament_ids": same_date,
            }
        )
    candidates.sort(
        key=lambda item: (
            item["plays_on_tournament_date"],
            item["season_participations"],
            str(item["club"]),
            str(item["label"]),
        )
    )
    if replace_team_label is not None:
        participant_labels = {
            str(team.get("label") or "")
            for team in tournament.get("teams", []) or []
        }
        if str(replace_team_label) not in participant_labels:
            raise SeasonStateError(
                f"Team {replace_team_label!r} is not a participant in tournament {tournament_id}"
            )
        from .replacement import replace_participant

        for candidate in candidates:
            if candidate["plays_on_tournament_date"]:
                # A team cannot play two tournaments on the same date, so a
                # same-date participant is deterministically blocked without
                # running the full-season verification.
                candidate["verdict"] = {
                    "status": "blocked",
                    "applicable": False,
                    "checks": {},
                    "blockers": ["same_date_participation"],
                }
                continue
            try:
                preview = replace_participant(
                    service,
                    season=season,
                    tournament_id=tournament_id,
                    remove_team_label=str(replace_team_label),
                    add_team_label=str(candidate["label"]),
                    dry_run=True,
                )
                candidate["verdict"] = preview["verdict"]
            except SeasonStateError as exc:
                candidate["verdict"] = {
                    "status": "blocked",
                    "applicable": False,
                    "checks": {},
                    "blockers": [str(exc)],
                }
        candidates.sort(
            key=lambda item: (
                not bool((item.get("verdict") or {}).get("applicable")),
                item["season_participations"],
                str(item["club"]),
                str(item["label"]),
            )
        )
        if legal_only:
            candidates = [
                item
                for item in candidates
                if bool((item.get("verdict") or {}).get("applicable"))
            ]
    if limit is not None and limit >= 0:
        candidates = candidates[:limit]
    return {
        "season": season,
        "revision": schedule.get("revision"),
        "canonical_state_revision": canonical_state_revision(schedule, decisions),
        "tournament_id": tournament_id,
        "age_group": age_group,
        "date": tournament.get("date"),
        "replace_team": replace_team_label,
        "validated": replace_team_label is not None,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


__all__ = [
    "constraint_inspection",
    "replacement_candidates",
    "tournament_inspection",
]
