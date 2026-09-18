"""Placed / provisional / unplaced placement-state normalization.

``season_plan`` means *the actual schedule*. A tournament belongs in
``plan.tournaments`` only when it has a concrete placement that is either
verified against the planning contract (*placed*) or explicitly marked as a
concrete-but-confirmation-required candidate (*provisional*).

A tournament the deterministic search could not place -- or whose concrete
placement provably collides with a trusted/fixed external calendar booking
with no accepted alternative -- is *unplaced*: it must be removed from
``plan.tournaments`` and retained only as structured planning work in
``unresolved_tournament_placements`` with a stable finding identity
(:mod:`tournament_scheduler.placement_findings`). That keeps every normal
season-plan consumer (participation, hosting, opponent/game counts, CSV/XLSX/
Spond/ICS/club exports) from mistaking an unplaced obligation for scheduled
hockey, while the obligation stays visible in the manual/findings surfaces.

This module owns one classification (:func:`classify_tournament`) and one
normalizer (:func:`normalize_unplaced_placements`) shared by the arena-conflict
decision, the export chokepoint and the canonical-season normalization
command, so the state model cannot drift between callers.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .placement_findings import (
    UNPLACED_PLACEMENT_CATEGORY,
    unplaced_placement_finding_id,
)
from .planning_contract import (
    apply_calendar_interpretations,
    external_calendar_conflict,
)

PLACED = "placed"
PROVISIONAL = "provisional"
UNPLACED = "unplaced"

# Classification reasons (stable machine-readable codes, not operator prose).
REASON_NO_CONCRETE_SLOT = "no_concrete_slot"
REASON_EXHAUSTED_SEARCH = "exhausted_placement_search"
REASON_FIXED_EXTERNAL_CONFLICT = "fixed_external_calendar_conflict"
REASON_ARENA_CONFLICT = "arena_conflict_no_alternative"
REASON_CALENDAR_UNAVAILABLE = "calendar_unavailable"
REASON_HOST_CONFIRMATION_REQUIRED = "host_confirmation_required"
REASON_APPROVED = "operator_approved"


def _is_operator_confirmed(record: Optional[Mapping[str, Any]]) -> bool:
    """True when an operator has explicitly confirmed this placement.

    An approval (or a live placement lock) is explicit external confirmation:
    the placement is scheduled even if a scraped calendar disagrees, so it is
    *placed* rather than provisional/unplaced.
    """
    if not isinstance(record, Mapping):
        return False
    if record.get("placement_locked"):
        return True
    return str(record.get("status") or "") == "approved"


def _normalized_approvals(approvals: Any) -> dict[str, Mapping[str, Any]]:
    """Accept either a decisions mapping or an ``approval_report`` result."""
    if isinstance(approvals, Mapping) and isinstance(approvals.get("tournaments"), list):
        return {
            str(entry.get("tournament_id")): entry
            for entry in approvals["tournaments"]
            if isinstance(entry, Mapping)
        }
    if isinstance(approvals, Mapping):
        return {
            str(key): value
            for key, value in approvals.items()
            if isinstance(value, Mapping)
        }
    return {}


def _interval_for(tournament: Mapping[str, Any], ice_time_for_age_group: Mapping[str, int]):
    from .arena_conflicts import tournament_interval
    from .serialization.season_plan import tournament_from_dict

    if not tournament.get("start_time"):
        return None
    try:
        return tournament_interval(tournament_from_dict(tournament), ice_time_for_age_group)
    except (KeyError, ValueError, TypeError):
        return None


def _calendar_status(problem: Optional[Mapping[str, Any]]) -> Mapping[str, str]:
    return (problem or {}).get("club_calendar_status") or {}


def _busy_intervals(
    problem: Optional[Mapping[str, Any]], plan: Mapping[str, Any]
) -> Mapping[str, Any]:
    return apply_calendar_interpretations(
        (problem or {}).get("club_busy_intervals") or {},
        plan.get("calendar_interpretations"),
    )


def classify_tournament(
    tournament: Mapping[str, Any],
    problem: Optional[Mapping[str, Any]] = None,
    *,
    approvals: Any = None,
    busy_intervals: Optional[Mapping[str, Any]] = None,
    ice_time_for_age_group: Optional[Mapping[str, int]] = None,
) -> dict[str, Any]:
    """Classify one tournament dict as placed, provisional or unplaced.

    Returns ``{"state", "reason", "tournament_id", "age_group", "date"}``.
    """
    from .host_placement_repair import is_manual_slot_failure

    tournament_id = str(tournament.get("id") or "")
    base = {
        "tournament_id": tournament_id,
        "age_group": str(tournament.get("age_group") or ""),
        "date": str(tournament.get("date") or ""),
        "host_club": str(tournament.get("host_club") or ""),
    }
    if tournament.get("cancelled"):
        return {**base, "state": PLACED, "reason": "cancelled"}

    record = _normalized_approvals(approvals).get(tournament_id)
    if _is_operator_confirmed(record):
        return {**base, "state": PLACED, "reason": REASON_APPROVED}

    # No concrete slot at all (e.g. a legacy arena-conflict demotion that
    # cleared start_time): there is nothing to schedule or export. A bare
    # missing start_time without any manual/unplaced marker is not a
    # placement obligation -- it is a malformed scheduled tournament, so it
    # is left in place for the hard verifier to reject rather than being
    # silently demoted into work that hides the defect.
    if not tournament.get("start_time"):
        if not tournament.get("manual_booking_reason"):
            return {**base, "state": PLACED, "reason": "missing_start_time_unverified"}
        return {**base, "state": UNPLACED, "reason": REASON_NO_CONCRETE_SLOT}

    # The legacy/pre-fix marker for a genuinely exhausted slot search. The
    # fix no longer creates these, but an already-generated plan may carry
    # them; they must be normalized back into planning work.
    if is_manual_slot_failure(tournament):
        return {**base, "state": UNPLACED, "reason": REASON_EXHAUSTED_SEARCH}

    # A host-controlled movable interval is a concrete candidate that merely
    # needs host confirmation -- provisional, never unplaced.
    if tournament.get("requires_host_confirmation"):
        return {**base, "state": PROVISIONAL, "reason": REASON_HOST_CONFIRMATION_REQUIRED}

    # Calendar unavailable/untrusted with an explicit provisional workflow:
    # the placement is real enough to retain but must stay visibly marked.
    if tournament.get("manual_booking_reason"):
        return {**base, "state": PROVISIONAL, "reason": REASON_CALENDAR_UNAVAILABLE}

    ice_time = ice_time_for_age_group
    if ice_time is None:
        ice_time = (problem or {}).get("ice_time_minutes") or (problem or {}).get("round_length_minutes") or {}
    busy = busy_intervals
    if busy is None:
        busy = _busy_intervals(problem, {})

    interval = _interval_for(tournament, ice_time or {})
    if interval is not None and interval.host_club:
        status = _calendar_status(problem)
        # A known calendar whose concrete interval overlaps a trusted/fixed
        # external booking cannot be scheduled as-is.
        if not status or status.get(interval.host_club, "unknown") == "known":
            duration = int((interval.end - interval.start).total_seconds() // 60)
            if external_calendar_conflict(
                busy,
                interval.host_club,
                interval.start.date(),
                interval.start.strftime("%H:%M"),
                duration,
            ):
                return {
                    **base,
                    "state": UNPLACED,
                    "reason": REASON_FIXED_EXTERNAL_CONFLICT,
                    "evidence": {
                        "arena": interval.arena,
                        "interval": interval.interval_label,
                        "host_club": interval.host_club,
                    },
                }
    return {**base, "state": PLACED, "reason": "verified"}


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
    from .occupancy import occupancy_components, round_count_for_games
    from .serialization.season_plan import tournament_from_dict

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
    from .unplaced_placement_repair import unplaced_placement_search_capability

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


def normalize_unplaced_placements(
    plan: dict[str, Any],
    problem: Optional[Mapping[str, Any]] = None,
    *,
    approvals: Any = None,
) -> dict[str, Any]:
    """Remove genuinely unplaced tournaments and record stable obligations.

    Mutates *plan* in place: unplaced tournaments are removed from
    ``tournaments``; ``unresolved_tournament_placements`` owns them instead.
    Provisional placements are retained but marked so they are never rendered
    as verified free ice. Returns a report of what changed.
    """
    tournaments = [t for t in (plan.get("tournaments") or []) if isinstance(t, dict)]
    ice_time = (problem or {}).get("ice_time_minutes") or (problem or {}).get("round_length_minutes") or {}
    busy = _busy_intervals(problem, plan)
    approvals_index = _normalized_approvals(approvals)

    obligations = [dict(entry) for entry in (plan.get("unresolved_tournament_placements") or []) if isinstance(entry, Mapping)]
    used_obligations: "set[int]" = set()
    kept: list[dict[str, Any]] = []
    provisional: list[dict[str, Any]] = []
    unplaced: list[dict[str, Any]] = []
    removed_ids: set[str] = set()

    for tournament in tournaments:
        if not tournament.get("cancelled"):
            # A calendar-unavailable placement with no explicit marker would
            # otherwise be indistinguishable from verified free ice; mark it
            # before classifying so the classification and the render agree.
            status = _calendar_status(problem)
            host = str(tournament.get("host_club") or "")
            if (
                not tournament.get("manual_booking_reason")
                and not tournament.get("requires_host_confirmation")
                and tournament.get("start_time")
                and status
                and host
                and not _is_operator_confirmed(approvals_index.get(str(tournament.get("id") or "")))
                and not _calendar_verified(host, status)
            ):
                tournament["manual_booking_reason"] = (
                    f"Kalender utilgjengelig for {host} — istid må bookes/verifiseres manuelt."
                )

        verdict = classify_tournament(
            tournament,
            problem,
            approvals=approvals_index,
            busy_intervals=busy,
            ice_time_for_age_group=ice_time,
        )
        if verdict["state"] == UNPLACED:
            obligation = _obligation_from_tournament(tournament, verdict, problem, ice_time)
            merged = _reuse_or_create_obligation(obligations, obligation, used_obligations)
            unplaced.append(
                {
                    "tournament_id": verdict["tournament_id"],
                    "_obligation": merged,
                    "age_group": verdict["age_group"],
                    "date": verdict["date"],
                    "host_club": verdict["host_club"],
                    "reason": verdict["reason"],
                }
            )
            removed_ids.add(verdict["tournament_id"])
            continue
        if verdict["state"] == PROVISIONAL:
            provisional.append(
                {
                    "tournament_id": verdict["tournament_id"],
                    "age_group": verdict["age_group"],
                    "date": verdict["date"],
                    "reason": verdict["reason"],
                }
            )
        kept.append(tournament)

    if unplaced:
        _assign_finding_ids(obligations)
        for entry in unplaced:
            obligation = entry.pop("_obligation", None)
            if isinstance(obligation, Mapping):
                entry["finding_id"] = str(obligation.get("id") or "")
        plan["tournaments"] = kept
        plan["unresolved_tournament_placements"] = obligations
        # Drop arena-conflict records whose tournament is now owned by a
        # placement obligation; keep the rest untouched.
        conflict_records = plan.get("manual_arena_conflict_placements")
        if isinstance(conflict_records, list):
            plan["manual_arena_conflict_placements"] = [
                entry
                for entry in conflict_records
                if not (isinstance(entry, Mapping) and str(entry.get("tournament_id")) in removed_ids)
            ]
        # A demoted tournament's placeholder start time is not a booking;
        # ensure the obligation is reflected in the plan-level external
        # conflict projection on the next reconcile.
        removed = sorted(removed_ids)
    else:
        removed = []

    return {
        "changed": bool(unplaced),
        "placed_count": len(kept) - len(provisional),
        "provisional": provisional,
        "unplaced": unplaced,
        "removed_tournament_ids": removed,
        "obligation_count": len(obligations),
    }


def _calendar_verified(host: str, status: Mapping[str, str]) -> bool:
    constituents = [part.strip() for part in str(host).split("/") if part.strip()] or [host]
    return any(status.get(part) == "known" for part in constituents)


def demote_tournament_to_unplaced(
    plan: dict[str, Any],
    tournament_id: str,
    *,
    reason: str,
    problem: Optional[Mapping[str, Any]] = None,
    ice_time_for_age_group: Optional[Mapping[str, int]] = None,
    evidence: Optional[Mapping[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Move one tournament out of ``tournaments`` into an obligation.

    Shared by the arena-conflict decision and any repair that proves a
    placement cannot be kept. Returns the recorded obligation, or ``None``
    when the tournament does not exist.
    """
    tournaments = plan.get("tournaments") or []
    target = next(
        (t for t in tournaments if isinstance(t, dict) and str(t.get("id")) == str(tournament_id)),
        None,
    )
    if target is None:
        return None
    ice_time = ice_time_for_age_group
    if ice_time is None:
        ice_time = (problem or {}).get("ice_time_minutes") or (problem or {}).get("round_length_minutes") or {}
    verdict = {
        "state": UNPLACED,
        "reason": reason,
        "tournament_id": str(tournament_id),
        "age_group": str(target.get("age_group") or ""),
        "date": str(target.get("date") or ""),
        "host_club": str(target.get("host_club") or ""),
    }
    if evidence:
        verdict["evidence"] = dict(evidence)
    obligation = _obligation_from_tournament(target, verdict, problem, ice_time)
    obligations = [
        dict(entry)
        for entry in (plan.get("unresolved_tournament_placements") or [])
        if isinstance(entry, Mapping)
    ]
    merged = _reuse_or_create_obligation(obligations, obligation, set())
    _assign_finding_ids(obligations)
    plan["tournaments"] = [
        t for t in tournaments if str(t.get("id")) != str(tournament_id)
    ]
    plan["unresolved_tournament_placements"] = obligations
    records = plan.get("manual_arena_conflict_placements")
    if isinstance(records, list):
        plan["manual_arena_conflict_placements"] = [
            entry
            for entry in records
            if not (isinstance(entry, Mapping) and str(entry.get("tournament_id")) == str(tournament_id))
        ]
    return merged


__all__ = [
    "PLACED",
    "PROVISIONAL",
    "UNPLACED",
    "REASON_ARENA_CONFLICT",
    "REASON_CALENDAR_UNAVAILABLE",
    "REASON_EXHAUSTED_SEARCH",
    "REASON_FIXED_EXTERNAL_CONFLICT",
    "REASON_HOST_CONFIRMATION_REQUIRED",
    "REASON_NO_CONCRETE_SLOT",
    "classify_tournament",
    "demote_tournament_to_unplaced",
    "normalize_unplaced_placements",
]
