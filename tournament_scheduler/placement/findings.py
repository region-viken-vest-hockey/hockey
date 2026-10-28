"""Canonical identity for unresolved tournament-placement planning findings.

A tournament the deterministic slot search could not place is planning work,
not schedule state: it has no verified/provisional concrete placement, so it
must never be materialized as a normal ``Tournament`` and must never be
counted as hosting, participation or played games. The planner records one
structured finding per failed placement in
``SeasonPlan.unresolved_tournament_placements``.

This module owns the stable finding identity every consumer (the manual-work
projection, promoted-season findings, audit evidence, a future materializing
repair) refers to, so no layer has to invent a tournament id for an obligation
that has no tournament.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

# Category shared by the planner finding and the manual-schedule projection.
UNPLACED_PLACEMENT_CATEGORY = "manual_tournament_placement"
UNPLACED_PLACEMENT_PREFIX = "unplaced_placement"


def unplaced_placement_finding_id(age_group: str, date_iso: str, sequence: int) -> str:
    """Stable finding identity independent of any tournament id.

    ``sequence`` disambiguates parallel same-age-group/same-date obligations
    the date skeleton requested independently; it is assigned in the
    planner's deterministic schedule order, so the same planning request
    yields the same id across runs.
    """
    return f"{UNPLACED_PLACEMENT_PREFIX}:{age_group}:{date_iso}:{int(sequence)}"


def _clean_teams(tournament: Mapping[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "club": str(team.get("club") or ""),
            "label": str(team.get("label") or ""),
            "age_group": str(team.get("age_group") or ""),
        }
        for team in (tournament.get("teams") or [])
        if isinstance(team, Mapping)
    ]


def _period_for(tournament: Mapping[str, Any], problem: Optional[Mapping[str, Any]]) -> str:
    existing = tournament.get("period") or tournament.get("half")
    if existing:
        return str(existing)
    period = (problem or {}).get("period")
    if isinstance(period, Mapping):
        value = period.get(str(tournament.get("age_group") or ""))
        if value:
            return str(value)
    return ""


def _obligation_from_tournament(
    tournament: Mapping[str, Any],
    verdict: Mapping[str, Any],
    problem: Optional[Mapping[str, Any]],
    ice_time_for_age_group: Mapping[str, int],
) -> dict[str, Any]:
    """Build a durable unplaced-placement obligation from a tournament."""
    from tournament_scheduler.shape.occupancy import occupancy_components, round_count_for_games
    from tournament_scheduler.serialization.season_plan import tournament_from_dict

    teams = _clean_teams(tournament)
    participant_clubs = sorted({team["club"] for team in teams if team["club"]})
    age_group = str(tournament.get("age_group") or "")
    try:
        round_count = round_count_for_games(tournament_from_dict(tournament).games)
    except (KeyError, ValueError, TypeError):
        round_count = 0
    components = occupancy_components(age_group, ice_time_for_age_group, round_count)
    candidate_hosts = sorted(
        set(participant_clubs) | ({str(tournament.get("host_club"))} if tournament.get("host_club") else set())
    )
    obligation: dict[str, Any] = {
        "age_group": age_group,
        "date": str(tournament.get("date") or ""),
        "period": _period_for(tournament, problem),
        "responsible_host": str(tournament.get("host_club") or ""),
        "candidate_hosts": candidate_hosts,
        "search_hosts_tried": [],
        "participant_teams": teams,
        "participant_clubs": participant_clubs,
        "participant_team_count": len(teams),
        "configured_ice_time_minutes": components.configured_ice_time_minutes,
        "round_count": components.round_count,
        "round_buffer_minutes": components.round_buffer_minutes,
        "required_duration_minutes": components.required_duration_minutes,
        "category": UNPLACED_PLACEMENT_CATEGORY,
        "search_attempted": True,
        "bounded_repair_exhausted": True,
        "reason": verdict.get("reason"),
        "source_tournament_id": verdict.get("tournament_id"),
        "normalized_from": "placement_normalization",
    }
    from tournament_scheduler.repair.unplaced_placement import unplaced_placement_search_capability

    obligation["search_capability"] = unplaced_placement_search_capability().to_dict()
    evidence = verdict.get("evidence")
    if isinstance(evidence, Mapping):
        obligation["placement_evidence"] = dict(evidence)
    return obligation


def _reuse_or_create_obligation(
    existing: list[dict[str, Any]],
    obligation: dict[str, Any],
    used: "set[int]",
) -> dict[str, Any]:
    """Merge tournament-derived facts into a matching existing obligation.

    The pre-fix planner recorded an obligation *and* still materialized a
    placeholder tournament for the same slot, so a normalized plan usually
    already holds the search evidence (candidate hosts, same-host dates,
    alternate-roster attempts). Reuse it instead of replacing richer evidence
    with the reduced set a placeholder can reconstruct. An obligation already
    consumed for one removed tournament is never reused for a parallel
    same-age-group/same-date slot -- those are distinct planning obligations
    and must keep distinct stable finding ids.
    """
    key = (obligation.get("age_group"), obligation.get("date"))
    for index, candidate in enumerate(existing):
        if index in used:
            continue
        if (candidate.get("age_group"), candidate.get("date")) != key:
            continue
        merged = dict(candidate)
        for field, value in obligation.items():
            if merged.get(field) in (None, "", [], {}) and value not in (None, "", [], {}):
                merged[field] = value
        # The concrete tournament is authoritative for the roster/host it was
        # materialized from, even if the obligation predates it.
        for field in ("participant_teams", "participant_clubs", "participant_team_count", "responsible_host"):
            if obligation.get(field) not in (None, "", [], {}):
                merged[field] = obligation[field]
        existing[index] = merged
        used.add(index)
        return merged
    existing.append(obligation)
    used.add(len(existing) - 1)
    return obligation


def _assign_finding_ids(obligations: list[dict[str, Any]]) -> None:
    counters: dict[tuple[str, str], int] = {}
    for entry in obligations:
        age_group = str(entry.get("age_group") or "?")
        date_iso = str(entry.get("date") or "?")
        key = (age_group, date_iso)
        counters[key] = counters.get(key, 0) + 1
        entry["id"] = unplaced_placement_finding_id(age_group, date_iso, counters[key])
