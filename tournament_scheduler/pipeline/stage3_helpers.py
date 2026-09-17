"""Stage 3 planning helpers."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from ..calendar_availability import (
    classify_club_event,
    legacy_kind,
)
from ..club_registry import CLUB_REGISTRY, canonicalize_club_name
from ..models import CalendarEvent, Roster, Team
from ..season_planner import SeasonPlanner
from ..serialization.season_plan import resolve_plan_dict

logger = logging.getLogger(__name__)


def _resolve_plan_dict(plan_raw: Any) -> dict[str, Any]:
    """Return a JSON-serialisable dict from *plan_raw*.

    Accepts either a :class:`SeasonPlan` object (converted via the public
    codec) or a plain :class:`dict` (returned as-is).
    Returns an empty dict for any other input so callers never receive
    ``None``.
    """
    return resolve_plan_dict(plan_raw)


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------


def _build_roster(config: dict[str, Any]) -> Roster:
    """Build a :class:`Roster` from the Stage 1 config."""
    teams_data = config.get("teams", [])
    teams = [
        Team(
            club=canonicalize_club_name(t["club"]),
            label=t["label"],
            age_group=t["age_group"],
            target_tournament_count=t.get("target_tournament_count"),
        )
        for t in teams_data
        if isinstance(t, dict)
    ]
    return Roster(teams=teams)


def _build_parallel_games(config: dict[str, Any]) -> dict[str, int]:
    """Extract parallel-games mapping from config."""
    return dict(config.get("parallel_games", {}))


def _build_round_length(config: dict[str, Any]) -> dict[str, int]:
    """Extract round-length-minutes mapping from config."""
    return dict(config.get("round_length_minutes", {}))


def _build_rounds_per_tournament(config: dict[str, Any]) -> dict[str, int]:
    """Extract configured tournament round counts from config."""
    return dict(config.get("rounds_per_tournament", {}))


def _build_ice_time(config: dict[str, Any]) -> dict[str, int]:
    """Extract configured base ice-time-minutes mapping from config."""
    return dict(config.get("ice_time_minutes", {}))


def _build_club_arenas(config: dict[str, Any]) -> dict[str, str]:
    """Build club→arena mapping, falling back to the global club registry."""
    return {
        club: entry.arena
        for club, entry in CLUB_REGISTRY.items()
        if hasattr(entry, "arena") and entry.arena
    }


def _build_events_by_club(scraping_result: dict[str, Any] | None) -> dict[str, list[CalendarEvent]]:
    """Reconstruct per-club `CalendarEvent` lists from the Stage 2 checkpoint.

    Stage 2 (`stage2_scraping.run`) writes an `"events_by_club"` key
    containing event dicts (as produced by `_events_to_dicts`) keyed by RVV
    club name. This converts those dicts back into `CalendarEvent` objects
    for use by `TournamentScheduler.find_arena_slot_for_date`.

    Returns an empty dict if *scraping_result* is missing or has no
    `"events_by_club"` key (e.g. older checkpoints, or partial Stage 2 runs).
    An empty per-club events list here is NOT the same as "this club is free
    to host" -- callers must additionally consult
    :func:`_build_club_calendar_status` (issue #262 P0): only a club whose
    status is `"known"` may be treated as having a genuinely empty calendar.
    A club missing from this dict entirely, or with `"unknown"` status, must
    be treated as unavailable, not free.
    """
    if not scraping_result:
        return {}

    events_by_club_raw = scraping_result.get("events_by_club", {})
    result: dict[str, list[CalendarEvent]] = {}
    for club_name, events in events_by_club_raw.items():
        club_events: list[CalendarEvent] = []
        for e in events:
            try:
                club_events.append(
                    CalendarEvent(
                        date=e["date"],
                        name=e.get("name", ""),
                        datetime=datetime.fromisoformat(e["datetime"]),
                        duration_hours=e.get("duration_hours", 0.0),
                    )
                )
            except (KeyError, ValueError) as exc:
                logger.warning(
                    "Dropped malformed event for club %r: %s — raw: %s",
                    club_name,
                    exc,
                    e,
                )
                continue
        result[club_name] = club_events
    return result


def _build_club_calendar_status(scraping_result: dict[str, Any] | None) -> dict[str, str]:
    """Reconstruct per-club calendar-evidence status from the Stage 2 checkpoint.

    Mirrors `_build_events_by_club`'s checkpoint access, but with the
    opposite fail-safe default: a missing checkpoint, a missing
    `"club_calendar_status"` key, or a club absent from that dict all mean
    "no trustworthy evidence this run" and must be treated as `"unknown"`
    by callers -- never as `"known"`/free. This is what prevents a club
    whose scrape was blocked, skipped, or never configured (e.g. Tønsberg's
    BookUp calendar in the 2026-09-06 run, issue #262) from silently being
    scheduled as if its whole calendar were open.
    """
    if not scraping_result:
        return {}
    raw = scraping_result.get("club_calendar_status", {})
    if not isinstance(raw, dict):
        return {}
    return {str(club): str(value) for club, value in raw.items()}


def _build_club_busy_intervals(
    scraping_result: dict[str, Any] | None,
) -> dict[str, list[dict[str, str]]]:
    """Reconstruct per-club, per-date busy *intervals* from the Stage 2 checkpoint.

    `_build_club_calendar_status`/`club_busy_dates` (see `build_planning_problem`)
    only carry coarse per-club/per-date "this club has something booked"
    facts, which is enough to tell a host apart from an unscraped club but not
    enough to prove that an arbitrary v2-generated start time on that date is
    actually free (issue #264 P0) -- a club can have one morning booking and
    still be free all afternoon. This reuses the exact overnight-aware
    interval math `utils.slot_finder.find_available_slots` already applies to
    live `CalendarEvent`s (via `_event_busy_range_on_date`) so the JSON
    `planning_problem` contract carries the same evidence, serialized as
    plain ``HH:MM`` start/end strings per date.

    Only includes clubs/dates with at least one dropped-in event -- an empty
    result for a club does NOT mean "free all day"; callers must still gate
    on `club_calendar_status` (only a `"known"` club's absence of entries here
    means genuinely free) exactly like `_build_events_by_club`.
    """
    events_by_club = _build_events_by_club(scraping_result)
    if not events_by_club:
        return {}

    from ..utils.slot_finder import _event_busy_range_on_date, minutes_to_time
    from ..utils.date_parser import DateParser

    result: dict[str, list[dict[str, str]]] = {}
    for club_name, events in events_by_club.items():
        # issue #264: classify every interval's availability so a
        # genuine external booking (fixed_busy) can be told apart from a
        # host-controlled interval the club itself may displace
        # (movable_busy -- either a per-club event rule such as Kongsberg's
        # "Åpen ishall", or a club whose whole scraped calendar is declared
        # club-controlled in the registry). See
        # `calendar_availability.classify_club_event` and
        # `planning_contract.external_calendar_conflict` /
        # `movable_calendar_opportunity`.
        intervals: list[dict[str, str]] = []
        for event in events:
            parsed = DateParser.parse(event.date)
            if not parsed:
                continue
            availability, reason = classify_club_event(club_name, event.name)
            event_date = parsed.date()
            # A booking can only ever spill into the day right after the one
            # it's recorded on (see `_event_busy_range_on_date`'s midnight
            # projection) -- checking exactly those two candidate dates
            # mirrors that function's own contract instead of re-deriving it.
            for check_date in (event_date, event_date + timedelta(days=1)):
                busy_range = _event_busy_range_on_date(event, check_date)
                if busy_range is None:
                    continue
                start_minutes, end_minutes = busy_range
                entry: dict[str, str] = {
                    "date": check_date.isoformat(),
                    "start": minutes_to_time(start_minutes),
                    "end": minutes_to_time(end_minutes),
                    "kind": legacy_kind(availability),
                    "availability": availability.value,
                    "calendar_event": event.name,
                }
                if reason:
                    entry["reason"] = reason
                intervals.append(entry)
        if intervals:
            intervals.sort(key=lambda entry: (entry["date"], entry["start"]))
            result[club_name] = intervals
    return result


def _make_planner(
    roster: Roster,
    pg_config: dict[str, int],
    club_arenas: dict[str, str],
    max_hosting_deviation: int = 1,
    round_length_config: dict[str, int] | None = None,
    ice_time_config: dict[str, int] | None = None,
    events_by_club: dict[str, list[CalendarEvent]] | None = None,
    fairness_thresholds: dict[str, float] | None = None,
    target_tournament_count: int | None = None,
    participation_targets_by_age_group: dict[str, dict[str, int]] | None = None,
    seed: int | None = None,
    max_hosting_days_per_month: int | None = None,
    penalty_hints: dict[str, float] | None = None,
    allow_penalty_hint_relaxation: bool = True,
    *,
    club_calendar_status: dict[str, str] | None = None,
    club_busy_intervals: dict[str, list[dict[str, str]]] | None = None,
    cheap_baseline: bool = False,
    shared_host_decisions: dict[tuple[str, str], str] | None = None,
    rounds_per_tournament_config: dict[str, int] | None = None,
) -> SeasonPlanner:
    """Construct a :class:`SeasonPlanner` with derived tournament sizing.

    The planner now sizes tournaments from the parallel-games config and no
    longer relies on a separate max-teams cap.
    """
    from ..scheduler import TournamentScheduler
    from ..conflict_checkers.holiday_checker import HolidayConflictChecker
    from ..utils.date_parser import DateParser

    scheduler = TournamentScheduler(
        calendar_sources=[],
        conflict_checkers=[HolidayConflictChecker()],
        date_parser=DateParser(),
    )
    return SeasonPlanner(
        scheduler=scheduler,
        roster=roster,
        club_arenas=club_arenas,
        parallel_games_for_age_group=pg_config or None,
        round_length_for_age_group=round_length_config or None,
        ice_time_for_age_group=ice_time_config or None,
        rounds_per_tournament_for_age_group=rounds_per_tournament_config or None,
        target_tournament_count=target_tournament_count,
        participation_targets_by_age_group=participation_targets_by_age_group,
        max_hosting_deviation=max_hosting_deviation,
        max_hosting_days_per_month=max_hosting_days_per_month,
        events_by_club=events_by_club or None,
        club_calendar_status=club_calendar_status or None,
        club_busy_intervals=club_busy_intervals or None,
        fairness_thresholds=fairness_thresholds or None,
        seed=seed,
        penalty_hints=penalty_hints,
        allow_penalty_hint_relaxation=allow_penalty_hint_relaxation,
        cheap_baseline=cheap_baseline,
        shared_host_decisions=shared_host_decisions,
    )


# ---------------------------------------------------------------------------
