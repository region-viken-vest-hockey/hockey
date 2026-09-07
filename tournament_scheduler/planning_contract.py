"""Harness-neutral Stage 3 planning contracts (issue #257).

Defines two stable, machine-readable JSON shapes so multiple planners
(the existing :class:`~tournament_scheduler.season_planner.SeasonPlanner`,
a future generic optimizer, or an LLM/harness-driven planner) can be
verified and scored the same way, independent of any single planner's
internals:

``planning_problem`` (see :func:`build_planning_problem`)
    A normalized snapshot of everything Stage 3 needs as input: roster,
    club/arena mapping, tournament sizing config, manual operator
    adjustments, date preferences, and per-club calendar availability.
    Produced from the same Stage 1 config + Stage 2 checkpoint that
    :mod:`tournament_scheduler.pipeline.stage3_planning` already reads.

``candidate`` (see :func:`candidate_from_plan_dict` / :func:`extract_candidate`)
    A proposed season plan: a list of tournaments with their teams and
    games. This is the same shape Stage 3 already writes to the pipeline
    checkpoint under the ``"plan"`` key (see ``_plan_to_dict`` in
    ``pipeline/stage3_helpers.py``) plus a small schema/source envelope,
    so existing checkpoints can be verified/scored without conversion.

``verify_candidate`` and ``score_candidate`` are pure functions over these
two dicts: no LLM calls, no dependency on ``SeasonPlanner`` internals, so
they can judge candidates produced by any planner implementation.
"""

from __future__ import annotations

from datetime import date
from itertools import combinations
from typing import Any, Dict, Iterable, List, Optional, Tuple

PLANNING_PROBLEM_SCHEMA_VERSION = 1
CANDIDATE_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# planning_problem.json
# ---------------------------------------------------------------------------


def build_planning_problem(
    config: Dict[str, Any],
    scraping_result: Optional[Dict[str, Any]],
    start_date: date,
    end_date: date,
) -> Dict[str, Any]:
    """Build a normalized ``planning_problem`` dict from Stage 1/2 outputs.

    Reuses the same builder helpers Stage 3 already relies on
    (``pipeline/stage3_helpers.py``) so the contract always matches what
    ``SeasonPlanner`` is actually given, rather than drifting out of sync
    with a hand-maintained parallel representation.
    """
    from tournament_scheduler.pipeline.stage3_helpers import (
        _build_club_arenas,
        _build_club_busy_intervals,
        _build_club_calendar_status,
        _build_events_by_club,
        _build_parallel_games,
        _build_round_length,
        _build_roster,
    )

    roster = _build_roster(config)
    club_arenas = _build_club_arenas(config)
    events_by_club = _build_events_by_club(scraping_result)
    club_calendar_status = _build_club_calendar_status(scraping_result)

    teams = [
        {
            "club": team.club,
            "label": team.label,
            "age_group": team.age_group,
            "target_tournament_count": team.target_tournament_count,
        }
        for team in roster.teams
    ]

    manual_adjustments_raw = config.get("manual_adjustments", {}) or {}
    manual_adjustments = {
        "locked_dates": sorted(str(d) for d in manual_adjustments_raw.get("locked_dates", []) or []),
        "banned_dates": sorted(str(d) for d in manual_adjustments_raw.get("banned_dates", []) or []),
        "forced_host_clubs": list(manual_adjustments_raw.get("forced_host_clubs", []) or []),
        "excluded_host_clubs": sorted(manual_adjustments_raw.get("excluded_host_clubs", []) or []),
        "pinned_tournament_ids": sorted(
            str(v) for v in (manual_adjustments_raw.get("pinned_tournament_ids", []) or [])
        ),
    }

    date_preferences = [
        {"fra": str(p.get("fra")), "til": str(p.get("til")), "vekt": float(p.get("vekt", 0.0))}
        for p in config.get("date_preferences", []) or []
        if isinstance(p, dict)
    ]

    club_busy_dates: Dict[str, List[str]] = {}
    for club, events in events_by_club.items():
        dates = sorted({event.datetime.date().isoformat() for event in events})
        if dates:
            club_busy_dates[club] = dates

    return {
        "schema_version": PLANNING_PROBLEM_SCHEMA_VERSION,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "teams": teams,
        "age_groups": roster.age_groups(),
        "clubs": dict(club_arenas),
        "parallel_games": _build_parallel_games(config),
        "round_length_minutes": _build_round_length(config),
        "max_hosting_deviation": config.get("maxHostingDeviation", 1),
        "target_tournament_count": config.get("target_tournament_count"),
        "target_tournament_counts_by_age_group": config.get("target_tournament_counts_by_age_group") or {},
        "manual_adjustments": manual_adjustments,
        "date_preferences": date_preferences,
        "club_busy_dates": club_busy_dates,
        "club_calendar_status": club_calendar_status,
        "club_busy_intervals": _build_club_busy_intervals(scraping_result),
    }


# ---------------------------------------------------------------------------
# candidate.json
# ---------------------------------------------------------------------------


def candidate_from_plan_dict(
    plan_dict: Dict[str, Any],
    *,
    source: str = "SeasonPlanner",
    planner_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Wrap a Stage 3 ``_plan_to_dict`` payload in the stable candidate envelope."""
    candidate = dict(plan_dict)
    candidate["schema_version"] = CANDIDATE_SCHEMA_VERSION
    candidate["source"] = {"planner": source, "version": planner_version}
    return candidate


def extract_candidate(data: Dict[str, Any]) -> Dict[str, Any]:
    """Return the candidate payload from *data*, whatever shape it was loaded in.

    Accepts a raw candidate dict (top-level ``"tournaments"`` key), a Stage 3
    pipeline checkpoint payload (``{"plan": {...}, "rules_report": ...}``),
    or a full checkpoint file envelope (``{"stage": ..., "data": {"plan":
    ...}}`` as written by ``PipelineState.write_stage``). Always returns a
    dict with a ``"schema_version"`` set so downstream code doesn't need to
    special-case older, unversioned checkpoints.
    """
    if "tournaments" not in data and isinstance(data.get("data"), dict):
        data = data["data"]
    if "tournaments" in data:
        candidate = dict(data)
    elif isinstance(data.get("plan"), dict):
        candidate = dict(data["plan"])
    else:
        raise ValueError("Could not find a candidate plan (expected a 'tournaments' or 'plan' key)")
    candidate.setdefault("schema_version", CANDIDATE_SCHEMA_VERSION)
    return candidate


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------


def _parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


TeamIdentity = Tuple[str, str, str]


def _time_to_minutes(value: str) -> int:
    """Parse an ``HH:MM`` clock string into minutes since midnight.

    Accepts ``"24:00"`` (and other hour values >= 24) as the end-of-day
    boundary `pipeline.stage3_helpers._build_club_busy_intervals` emits for
    events running to/through midnight -- ``datetime.time`` itself rejects
    an hour of 24, so this can't just reuse `utils.slot_finder.parse_time`.
    """
    hour, minute = (int(part) for part in value.split(":", 1))
    return hour * 60 + minute


def external_busy_windows(
    club_busy_intervals: Optional[Dict[str, List[Dict[str, str]]]],
    club: Optional[str],
    on_date: date,
    *,
    kind: Optional[str] = "external",
) -> List[Tuple[int, int]]:
    """Return ``(start_minutes, end_minutes)`` busy windows for *club* on *on_date*.

    Reads the ``club_busy_intervals`` evidence a ``planning_problem`` carries
    (issue #264 P0) -- real scraped/fixed-allocation calendar bookings, not
    just the coarse ``club_busy_dates`` date list. An empty result does NOT
    mean "free all day": callers must additionally confirm the club's
    ``club_calendar_status`` is ``"known"`` before treating the absence of
    entries here as evidence of availability.

    *kind* filters by each interval's ``"kind"`` tag (issue #264: hard
    external bookings vs. a club-controlled allocation the club itself may
    still use -- see ``ClubCalendarSource.club_controlled_calendar``).
    Defaults to ``"external"`` -- only genuine external bookings -- since
    that is what every hard-conflict caller wants; entries with no ``"kind"``
    key (older checkpoints) are treated as ``"external"`` too, the safe
    default. Pass ``kind=None`` to return every interval regardless of kind,
    or ``kind="club_controlled"`` to inspect only club-controlled ones.
    """
    if not club_busy_intervals or not club:
        return []
    date_str = on_date.isoformat()
    windows: List[Tuple[int, int]] = []
    for entry in club_busy_intervals.get(club, []):
        if entry.get("date") != date_str:
            continue
        if kind is not None and entry.get("kind", "external") != kind:
            continue
        try:
            windows.append((_time_to_minutes(entry["start"]), _time_to_minutes(entry["end"])))
        except (KeyError, ValueError):
            continue
    return windows


def external_calendar_conflict(
    club_busy_intervals: Optional[Dict[str, List[Dict[str, str]]]],
    club: Optional[str],
    on_date: date,
    start_time: Optional[str],
    duration_minutes: int,
) -> bool:
    """True if ``[start_time, start_time + duration_minutes)`` on *on_date*
    overlaps any of *club*'s external busy windows (issue #264 P0).

    Shared by :func:`verify_candidate` (rejecting an already-built candidate)
    and the Stage 3 v2 optimizer's ``move_dates``/``move_hosts``/``move_slots``
    (rejecting an infeasible move before it's ever proposed as a candidate)
    so both enforce exactly the same external-calendar evidence. Only
    ``"external"``-kind intervals count -- a club-controlled allocation
    (issue #264, ``ClubCalendarSource.club_controlled_calendar``) is not a
    hard conflict for that same club's own hosted tournaments; see
    :func:`club_controlled_allocation_conflict` to detect (non-blocking) use
    of one for the evidence bundle.
    """
    if not club or not start_time or duration_minutes <= 0:
        return False
    try:
        new_start = _time_to_minutes(start_time)
    except ValueError:
        return False
    new_end = new_start + duration_minutes
    for busy_start, busy_end in external_busy_windows(club_busy_intervals, club, on_date):
        if new_start < busy_end and busy_start < new_end:
            return True
    return False


def club_controlled_allocation_conflict(
    club_busy_intervals: Optional[Dict[str, List[Dict[str, str]]]],
    club: Optional[str],
    on_date: date,
    start_time: Optional[str],
    duration_minutes: int,
) -> bool:
    """True if the tournament interval overlaps a *club-controlled* busy
    window rather than (or in addition to) a genuine external one
    (issue #264).

    Non-blocking counterpart to :func:`external_calendar_conflict` -- a club
    placed inside its own controlled allocation is feasible, but
    :func:`verify_candidate` still records it so the evidence bundle shows
    when a selected tournament relied on club-controlled allocation rather
    than an unconditionally free interval.
    """
    if not club or not start_time or duration_minutes <= 0:
        return False
    try:
        new_start = _time_to_minutes(start_time)
    except ValueError:
        return False
    new_end = new_start + duration_minutes
    for busy_start, busy_end in external_busy_windows(club_busy_intervals, club, on_date, kind="club_controlled"):
        if new_start < busy_end and busy_start < new_end:
            return True
    return False


def _team_identity(team: Dict[str, Any]) -> TeamIdentity:
    """Return a (club, label, age_group) identity for *team*.

    Team labels (e.g. "Ringerike 1") are only unique *within* an age group's
    roster — the same label is routinely reused across age groups (and
    occasionally across clubs), so any cross-tournament bookkeeping (season
    participation counts, per-team dates, opponent-pair history) must key on
    the full triple, not the label alone, or unrelated teams that happen to
    share a label get merged together. See `team_key()` in `models.py` for
    the same disambiguation applied to display strings.
    """
    return (team.get("club", ""), team.get("label", ""), team.get("age_group", ""))


def _display_label(identity: TeamIdentity, duplicate_labels: "set[str]") -> str:
    club, label, age_group = identity
    if label in duplicate_labels:
        return f"{label} ({club}, {age_group})"
    return label


def _duplicate_labels(tournaments: Iterable[Dict[str, Any]]) -> "set[str]":
    seen: Dict[str, set[TeamIdentity]] = {}
    for t in tournaments:
        for team in t.get("teams", []):
            identity = _team_identity(team)
            seen.setdefault(identity[1], set()).add(identity)
    return {label for label, identities in seen.items() if len(identities) > 1}


def verify_candidate(
    candidate: Dict[str, Any],
    problem: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Deterministically check *candidate* against hard planning requirements.

    Does not depend on an LLM or on ``SeasonPlanner`` internals — only on
    the stable candidate/problem contracts. When *problem* is omitted,
    only self-consistency checks are run (duplicate participation, arena
    double-booking, tournament roster sanity); checks that need the
    original planning inputs (registered teams, calendar validity,
    participation targets, manual restrictions) are skipped and reported
    as such.
    """
    violations: List[Dict[str, Any]] = []
    skipped: List[str] = []
    # issue #264: non-blocking record of tournaments placed inside a
    # club-controlled allocation window rather than an unconditionally free
    # interval -- not a violation, but the evidence bundle should show when
    # a selected candidate relied on one.
    club_controlled_allocations_used: List[Dict[str, Any]] = []

    tournaments = [t for t in candidate.get("tournaments", []) if not t.get("cancelled")]

    def _violate(code: str, message: str, tournament_id: Optional[str] = None) -> None:
        entry: Dict[str, Any] = {"code": code, "message": message}
        if tournament_id is not None:
            entry["tournament_id"] = tournament_id
        violations.append(entry)

    # --- self-consistent checks (no problem required) ----------------------

    duplicate_labels = _duplicate_labels(tournaments)
    team_dates: Dict[TeamIdentity, List[Tuple[date, str]]] = {}
    participations: Dict[TeamIdentity, int] = {}

    for t in tournaments:
        t_id = t.get("id", "?")
        t_date = _parse_date(t.get("date"))
        if t_date is None:
            _violate("invalid_date", f"Tournament {t_id} has a missing/invalid date", t_id)
            continue

        seen_identities: set[TeamIdentity] = set()
        for team in t.get("teams", []):
            if not team.get("label"):
                continue
            identity = _team_identity(team)
            display = _display_label(identity, duplicate_labels)
            if identity in seen_identities:
                _violate(
                    "duplicate_team_in_tournament",
                    f"Team {display!r} appears twice in tournament {t_id}",
                    t_id,
                )
            seen_identities.add(identity)
            if team.get("age_group") and team.get("age_group") != t.get("age_group"):
                _violate(
                    "age_group_mismatch",
                    f"Team {display!r} ({team.get('age_group')}) does not match tournament "
                    f"{t_id} age group {t.get('age_group')}",
                    t_id,
                )
            participations[identity] = participations.get(identity, 0) + 1
            team_dates.setdefault(identity, []).append((t_date, t_id))

    for identity, entries in team_dates.items():
        display = _display_label(identity, duplicate_labels)
        by_date: Dict[date, List[str]] = {}
        for t_date, t_id in entries:
            by_date.setdefault(t_date, []).append(t_id)
        for t_date, ids in by_date.items():
            if len(ids) > 1:
                _violate(
                    "duplicate_participation_same_date",
                    f"Team {display!r} is scheduled in {len(ids)} tournaments on {t_date.isoformat()}: {ids}",
                )

    if problem is None:
        skipped.extend(
            [
                "registered_teams",
                "valid_slot_window",
                "tournament_capacity",
                "participation_targets",
                "manual_restrictions",
                "calendar_validity",
                "arena_interval_conflicts",
                "external_calendar_conflicts",
            ]
        )
        return {
            "ok": not violations,
            "violations": violations,
            "skipped": skipped,
            "club_controlled_allocations_used": club_controlled_allocations_used,
            "unresolved_hosting_obligations": [],
        }

    # --- problem-dependent checks -------------------------------------------

    valid_teams = {
        (t["club"], t["label"], t["age_group"]) for t in problem.get("teams", [])
    }
    target_by_identity = {
        (t["club"], t["label"], t["age_group"]): t.get("target_tournament_count")
        for t in problem.get("teams", [])
    }
    default_target = problem.get("target_tournament_count")

    window_start = _parse_date(problem.get("start_date"))
    window_end = _parse_date(problem.get("end_date"))

    # Arena occupancy is a full datetime interval (start_time + computed
    # duration), not just an arena/date pair — arenas routinely host more
    # than one age group's tournament in a day back-to-back. Reuse the same
    # planner-independent interval-collision logic Stage 3/4 already share
    # (`arena_conflicts.py`) instead of a naive same-arena/same-date check,
    # which would flag normal same-day scheduling as a false positive.
    from tournament_scheduler.arena_conflicts import find_arena_interval_collisions, tournament_interval
    from tournament_scheduler.pipeline.stage3_helpers import _tournament_from_dict

    round_length_minutes = problem.get("round_length_minutes") or {}
    club_calendar_status_for_conflicts = problem.get("club_calendar_status") or {}
    club_busy_intervals = problem.get("club_busy_intervals") or {}
    try:
        tournament_objs = [_tournament_from_dict(t) for t in candidate.get("tournaments", []) if not t.get("cancelled")]
        for collision in find_arena_interval_collisions(tournament_objs, round_length_minutes):
            _violate(
                "arena_interval_conflict",
                collision["message"],
                collision.get("tournament_id"),
            )
        # issue #264 P0: a "known" calendar status only proves the host club
        # was scraped this run -- it does NOT prove every candidate start
        # time on a given date is free. Independently check each
        # tournament's actual hall interval against the host's real busy
        # windows, so a host/date/time combination the candidate's own
        # planner never validated against real evidence (e.g. a v2 optimizer
        # move) is still caught here rather than only by re-running the
        # planner that produced it.
        for tournament_obj in tournament_objs:
            interval = tournament_interval(tournament_obj, round_length_minutes)
            if interval is None or not interval.host_club:
                continue
            if (
                club_calendar_status_for_conflicts
                and club_calendar_status_for_conflicts.get(interval.host_club, "unknown") != "known"
            ):
                continue  # already reported as host_calendar_status_unknown below
            duration_minutes = int((interval.end - interval.start).total_seconds() // 60)
            if external_calendar_conflict(
                club_busy_intervals,
                interval.host_club,
                interval.start.date(),
                interval.start.strftime("%H:%M"),
                duration_minutes,
            ):
                _violate(
                    "external_calendar_conflict",
                    f"Tournament {interval.tournament_id} hosted by {interval.host_club!r} on "
                    f"{interval.date} ({interval.interval_label}) overlaps a known external "
                    "calendar booking",
                    interval.tournament_id,
                )
            elif club_controlled_allocation_conflict(
                club_busy_intervals,
                interval.host_club,
                interval.start.date(),
                interval.start.strftime("%H:%M"),
                duration_minutes,
            ):
                club_controlled_allocations_used.append(
                    {
                        "tournament_id": interval.tournament_id,
                        "host_club": interval.host_club,
                        "date": interval.date,
                        "interval": interval.interval_label,
                    }
                )
    except (KeyError, ValueError) as exc:
        _violate("arena_interval_check_failed", f"Could not evaluate arena interval conflicts: {exc}")

    parallel_games = problem.get("parallel_games") or {}
    # Note: `club_busy_dates` (in the problem contract) is intentionally not
    # used for a "host already busy" hard check here — a club's own scraped
    # calendar almost always shows the very tournament it is hosting, so a
    # naive date-membership check is a near-universal false positive. Real
    # host/calendar conflict detection needs event-level matching (is the
    # busy event actually a *different* commitment?), which the current
    # per-club date-only export can't distinguish. Left as future work.

    manual = problem.get("manual_adjustments") or {}
    banned_dates = {_parse_date(d) for d in manual.get("banned_dates", [])}
    locked_dates = {_parse_date(d) for d in manual.get("locked_dates", [])}
    excluded_host_clubs = set(manual.get("excluded_host_clubs", []))
    pinned_ids = set(manual.get("pinned_tournament_ids", []))

    scheduled_dates: set[date] = set()
    for t in tournaments:
        t_id = t.get("id", "?")
        t_date = _parse_date(t.get("date"))
        if t_date is None:
            continue
        scheduled_dates.add(t_date)

        if window_start and window_end and not (window_start <= t_date <= window_end):
            _violate(
                "date_outside_window",
                f"Tournament {t_id} on {t_date.isoformat()} is outside the planning "
                f"window {window_start.isoformat()}..{window_end.isoformat()}",
                t_id,
            )

        if t_date in banned_dates:
            _violate("banned_date_used", f"Tournament {t_id} is scheduled on banned date {t_date.isoformat()}", t_id)

        host_club = t.get("host_club")
        if host_club and host_club in excluded_host_clubs:
            _violate(
                "excluded_host_club_used",
                f"Tournament {t_id} is hosted by excluded club {host_club!r}",
                t_id,
            )

        # issue #262 P0: a host club with no trustworthy calendar evidence
        # this run (blocked/skipped/missing scrape) must never be silently
        # treated as available. Only enforced when the problem actually
        # carries a (non-empty) calendar-status map -- older/hand-built
        # problem dicts without it skip this check rather than falsely
        # flagging every host as unknown.
        club_calendar_status = problem.get("club_calendar_status") or {}
        if host_club and club_calendar_status and club_calendar_status.get(host_club, "unknown") != "known":
            _violate(
                "host_calendar_status_unknown",
                f"Tournament {t_id} is hosted by {host_club!r}, whose calendar "
                "availability is unknown this run (no valid scrape evidence) "
                "-- it cannot be treated as free to host",
                t_id,
            )

        max_teams = parallel_games.get(t.get("age_group"))
        team_count = len(t.get("teams", []))
        if isinstance(max_teams, int) and max_teams > 0 and team_count > max_teams * 2:
            _violate(
                "tournament_over_capacity",
                f"Tournament {t_id} has {team_count} teams, exceeding the configured "
                f"capacity for {t.get('age_group')} ({max_teams * 2} for {max_teams} parallel games)",
                t_id,
            )

        for team in t.get("teams", []):
            identity = _team_identity(team)
            if valid_teams and identity not in valid_teams:
                _violate(
                    "unregistered_team",
                    f"Team {_display_label(identity, duplicate_labels)!r} in tournament {t_id} is not in "
                    f"the registered roster ({identity[0]}/{identity[2]})",
                    t_id,
                )

    for locked in locked_dates:
        if locked and locked not in scheduled_dates:
            _violate("locked_date_missing", f"Locked date {locked.isoformat()} has no scheduled tournament")

    candidate_ids = {t.get("id") for t in candidate.get("tournaments", [])}
    for pinned in pinned_ids:
        if pinned not in candidate_ids:
            _violate("pinned_tournament_missing", f"Pinned tournament {pinned!r} is missing from the candidate")

    for identity, count in participations.items():
        # Resolution order mirrors SeasonPlanner's own precedence (see
        # `season_planner.SeasonPlanner._team_target_tournament_count`): an
        # explicit per-team override, then the global default. When neither
        # gives a concrete number, the target is planner-inferred from
        # tournament capacity — deliberately not reproduced here, since
        # duplicating that heuristic in the verifier is exactly the coupling
        # issue #257 asks the contract to avoid, so the check is skipped.
        #
        # `target_tournament_counts_by_age_group`'s before/after-Christmas
        # entries are NOT a per-team participation target — they're weights
        # `SeasonPlanner._split_tournament_counts_for_age_groups` uses to
        # split an age group's *tournament count* across the two halves of
        # the season. Treating "before + after" as a per-team target here
        # produced 100+ false-positive participation_target_mismatch
        # violations against a correct baseline plan (issue #257 A/B
        # benchmark, 2026-09-03).
        target = target_by_identity.get(identity)
        if target is None:
            target = default_target
        if isinstance(target, int) and count != target:
            _violate(
                "participation_target_mismatch",
                f"Team {_display_label(identity, duplicate_labels)!r} participates {count} times, "
                f"expected {target}",
            )

    # issue #266 P0: club x age-group hosting coverage is a required
    # planning obligation, not a hard `violation` -- a candidate with an
    # unresolved obligation is still `ok` (it must not silently give the
    # hosting burden to another club and be rejected outright), but the
    # obligation must be visible so it can be surfaced as a manual-placement
    # item rather than swallowed.
    from tournament_scheduler.hosting_coverage import hosting_coverage_matrix, unresolved_from_matrix

    coverage_rows = hosting_coverage_matrix(problem.get("teams", []), tournaments)
    unresolved_hosting_obligations = unresolved_from_matrix(coverage_rows)

    return {
        "ok": not violations,
        "violations": violations,
        "skipped": skipped,
        "club_controlled_allocations_used": club_controlled_allocations_used,
        "unresolved_hosting_obligations": unresolved_hosting_obligations,
    }


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------


def score_candidate(
    candidate: Dict[str, Any],
    gap_thresholds: Iterable[int] = (7, 14),
    *,
    problem: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute deterministic quality metrics for *candidate*.

    Self-contained: needs no ``planning_problem`` and no LLM. Metrics are
    grouped to mirror issue #257's scope: participation, opponent
    diversity (inter-club vs. same-club kept separate), turnaround
    spacing, and hosting fairness.

    *problem* is optional (issue #266) -- when given, ``hosting`` also
    reports a club x age-group coverage matrix (every registered club x
    age-group pair, hosted count, and whether it is an unresolved
    obligation). Without it, only the pre-existing club-level
    ``counts_by_host``/``spread`` are returned, since the full registered
    roster (including clubs with zero hosted tournaments) isn't derivable
    from the candidate's tournaments alone.
    """
    tournaments = sorted(
        (t for t in candidate.get("tournaments", []) if not t.get("cancelled")),
        key=lambda t: _parse_date(t.get("date")) or date.min,
    )
    duplicate_labels = _duplicate_labels(tournaments)

    # --- participation -------------------------------------------------
    participations: Dict[TeamIdentity, int] = {}
    for t in tournaments:
        for team in t.get("teams", []):
            if team.get("label"):
                identity = _team_identity(team)
                participations[identity] = participations.get(identity, 0) + 1
    counts = list(participations.values())
    participation_spread = (max(counts) - min(counts)) if counts else 0

    # --- opponent diversity ---------------------------------------------
    pair_counts: Dict[Tuple[TeamIdentity, TeamIdentity], int] = {}
    same_club_pairs: set[Tuple[TeamIdentity, TeamIdentity]] = set()
    inter_club_pairs: set[Tuple[TeamIdentity, TeamIdentity]] = set()
    novel_games = 0
    total_games = 0
    # The universe of *possible* inter-club opponents is every cross-club
    # pair drawn from all teams that appear anywhere in the season for an
    # age group — not just pairs that happened to share a tournament, which
    # would make `inter_club_diversity` trivially ~100% (round-robin means
    # co-attendance already implies they played).
    teams_by_age_group: Dict[Optional[str], set[TeamIdentity]] = {}

    for t in tournaments:
        teams = t.get("teams", [])
        age_group = t.get("age_group")
        teams_by_age_group.setdefault(age_group, set()).update(_team_identity(tm) for tm in teams)

        identity_by_label = {team.get("label"): _team_identity(team) for team in teams}
        for game in t.get("games", []):
            home_label, away_label = game.get("home"), game.get("away")
            home_identity = identity_by_label.get(home_label)
            away_identity = identity_by_label.get(away_label)
            if home_identity is None or away_identity is None:
                continue
            pair = tuple(sorted((home_identity, away_identity)))
            total_games += 1
            if pair not in pair_counts:
                novel_games += 1
            pair_counts[pair] = pair_counts.get(pair, 0) + 1

            if home_identity[0] == away_identity[0]:
                same_club_pairs.add(pair)
            else:
                inter_club_pairs.add(pair)

    unique_pairs = len(pair_counts)
    pairwise_novelty = (novel_games / total_games) if total_games else 0.0
    repeat_distribution: Dict[int, int] = {}
    for count in pair_counts.values():
        repeat_distribution[count] = repeat_distribution.get(count, 0) + 1
    pairs_meeting_3_plus = sum(v for k, v in repeat_distribution.items() if k >= 3)
    max_pair_repeat = max(pair_counts.values()) if pair_counts else 0

    inter_club_universe: set[Tuple[TeamIdentity, TeamIdentity]] = set()
    for identities in teams_by_age_group.values():
        for a, b in combinations(sorted(identities), 2):
            if a[0] != b[0]:
                inter_club_universe.add((a, b))
    inter_club_diversity = (len(inter_club_pairs) / len(inter_club_universe)) if inter_club_universe else 0.0

    # --- same-club clustering -------------------------------------------
    max_same_club_per_tournament = 0
    for t in tournaments:
        club_counts: Dict[str, int] = {}
        for team in t.get("teams", []):
            club = team.get("club")
            if club:
                club_counts[club] = club_counts.get(club, 0) + 1
        if club_counts:
            max_same_club_per_tournament = max(max_same_club_per_tournament, max(club_counts.values()))

    # --- turnaround spacing ----------------------------------------------
    gap_thresholds = list(gap_thresholds)
    gaps_under: Dict[int, int] = {threshold: 0 for threshold in gap_thresholds}
    min_turnaround: Optional[int] = None
    for identity in participations:
        dates = sorted(
            _parse_date(t.get("date"))
            for t in tournaments
            if any(_team_identity(team) == identity for team in t.get("teams", []))
        )
        dates = [d for d in dates if d is not None]
        for prev, nxt in zip(dates, dates[1:]):
            gap = (nxt - prev).days
            if min_turnaround is None or gap < min_turnaround:
                min_turnaround = gap
            for threshold in gap_thresholds:
                if gap < threshold:
                    gaps_under[threshold] += 1

    # --- hosting fairness --------------------------------------------------
    host_counts: Dict[str, int] = {}
    for t in tournaments:
        host = t.get("host_club") or t.get("arena")
        if host:
            host_counts[host] = host_counts.get(host, 0) + 1
    hosting_spread = (max(host_counts.values()) - min(host_counts.values())) if host_counts else 0

    # issue #266 P0: a single global spread scalar hides a club that hosts
    # nothing in one age group while over-hosting in another. When *problem*
    # carries the registered roster, report the full club x age-group
    # coverage matrix (and its per-club/per-age-group rollups) alongside it.
    hosting_coverage: Dict[str, Any] = {}
    if problem is not None:
        from tournament_scheduler.hosting_coverage import (
            hosting_breakdown_by_club_and_age_group,
            hosting_coverage_matrix,
            unresolved_from_matrix,
        )

        coverage_rows = hosting_coverage_matrix(problem.get("teams", []), tournaments)
        hosting_coverage = {
            "club_age_group_matrix": coverage_rows,
            "unresolved_obligations": unresolved_from_matrix(coverage_rows),
            **hosting_breakdown_by_club_and_age_group(coverage_rows),
        }

    # --- month distribution --------------------------------------------------
    month_counts: Dict[str, int] = {}
    for t in tournaments:
        t_date = _parse_date(t.get("date"))
        if t_date:
            key = f"{t_date.year:04d}-{t_date.month:02d}"
            month_counts[key] = month_counts.get(key, 0) + 1

    return {
        "participation": {
            "counts_by_team": {
                _display_label(identity, duplicate_labels): count
                for identity, count in participations.items()
            },
            "spread": participation_spread,
        },
        "opponent_diversity": {
            "unique_pairs": unique_pairs,
            "pairwise_novelty": pairwise_novelty,
            "pair_repeat_distribution": repeat_distribution,
            "pairs_meeting_3_plus": pairs_meeting_3_plus,
            "max_pair_repeat": max_pair_repeat,
            "inter_club_diversity": inter_club_diversity,
            "same_club_pairing_count": len(same_club_pairs),
            "max_same_club_teams_per_tournament": max_same_club_per_tournament,
        },
        "turnaround": {
            "min_turnaround_days": min_turnaround,
            "gaps_under_days": gaps_under,
        },
        "hosting": {
            "counts_by_host": host_counts,
            "spread": hosting_spread,
            **hosting_coverage,
        },
        "month_distribution": month_counts,
    }
