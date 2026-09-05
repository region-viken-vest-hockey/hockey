"""Stage 3 planning helpers."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

from ..club_registry import CLUB_REGISTRY
from ..models import CalendarEvent, Game, Roster, SeasonPlan, Team, Tournament
from ..roster_loader import RosterLoader
from ..season_planner import SeasonPlanner

def _plan_to_dict(plan: SeasonPlan) -> dict[str, Any]:
    """Convert :class:`SeasonPlan` to a JSON-serialisable dict."""

    def _game_to_dict(g: Game) -> dict[str, Any]:
        return {
            "home": g.home.label,
            "away": g.away.label,
            "parallel_slot": g.parallel_slot,
            "round_number": g.round_number,
        }

    def _team_to_dict(t: Team) -> dict[str, Any]:
        d: dict[str, Any] = {"club": t.club, "label": t.label, "age_group": t.age_group}
        if t.target_tournament_count is not None:
            d["target_tournament_count"] = t.target_tournament_count
        return d

    def _tournament_to_dict(t: Tournament) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": t.id,
            "date": t.date.isoformat(),
            "arena": t.arena,
            "age_group": t.age_group,
            "host_club": t.host_club,
            "teams": [_team_to_dict(team) for team in t.teams],
            "games": [_game_to_dict(g) for g in t.games],
            "start_time": t.start_time,
        }
        if t.cancelled:
            d["cancelled"] = True
            d["cancellation_reason"] = t.cancellation_reason
        if t.preferanse_vekt != 0.0:
            d["preferanse_vekt"] = t.preferanse_vekt
        if t.scoring_weight_term != 0.0:
            d["scoring_weight_term"] = t.scoring_weight_term
        if t.manual_booking_reason:
            d["manual_booking_reason"] = t.manual_booking_reason
        return d

    # Compute per-team tournament participation counts from the tournament list
    participations: Dict[str, int] = {}
    for t in plan.tournaments:
        for team in t.teams:
            participations[team.label] = participations.get(team.label, 0) + 1

    checkpoint = {
        "start_date": plan.start_date.isoformat() if plan.start_date else None,
        "end_date": plan.end_date.isoformat() if plan.end_date else None,
        "diversity_score": plan.diversity_score,
        "pairwise_matchup_score": plan.pairwise_matchup_score,
        "month_balance_score": plan.month_balance_score,
        "arena_counts": plan.arena_counts,
        "team_game_counts": dict(plan.team_game_counts),
        "team_tournament_participations": participations,
        "game_count_spread": plan.game_count_spread,
        "game_count_spread_by_age_group": plan.game_count_spread_by_age_group,
        "fairness_gate": plan.fairness_gate,
        "team_last_game_dates": {
            k: v.isoformat() for k, v in plan.team_last_game_dates.items()
        },
        "skipped_age_groups": list(plan.skipped_age_groups),
        "arena_day_collisions": list(plan.arena_day_collisions),
        "tournaments": [_tournament_to_dict(t) for t in plan.tournaments],
    }
    if plan.manual_adjustments:
        checkpoint["manual_adjustments"] = plan.manual_adjustments
    if plan.date_preference_weights:
        checkpoint["date_preference_weights"] = list(plan.date_preference_weights)
    return checkpoint


def _resolve_plan_dict(plan_raw: Any) -> dict[str, Any]:
    """Return a JSON-serialisable dict from *plan_raw*.

    Accepts either a :class:`SeasonPlan` object (converted via
    :func:`_plan_to_dict`) or a plain :class:`dict` (returned as-is).
    Returns an empty dict for any other input so callers never receive
    ``None``.
    """
    if hasattr(plan_raw, "__dict__"):
        return _plan_to_dict(plan_raw)
    if isinstance(plan_raw, dict):
        return plan_raw
    return {}


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------


def _build_roster(config: dict[str, Any]) -> Roster:
    """Build a :class:`Roster` from the Stage 1 config."""
    teams_data = config.get("teams", [])
    teams = [
        Team(
            club=t["club"],
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
    `"events_by_club"` key (e.g. older checkpoints, or partial Stage 2 runs)
    -- callers should treat this as "no calendar data available" and fall
    back to default scheduling behavior.
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


def _make_planner(
    roster: Roster,
    pg_config: dict[str, int],
    club_arenas: dict[str, str],
    max_hosting_deviation: int = 1,
    round_length_config: dict[str, int] | None = None,
    events_by_club: dict[str, list[CalendarEvent]] | None = None,
    fairness_thresholds: dict[str, float] | None = None,
    target_tournament_count: int | None = None,
    target_tournament_counts_by_age_group: dict[str, dict[str, int]] | None = None,
    seed: int | None = None,
    max_hosting_days_per_month: int | None = None,
    penalty_hints: dict[str, float] | None = None,
    allow_penalty_hint_relaxation: bool = True,
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
        target_tournament_count=target_tournament_count,
        target_tournament_counts_by_age_group=target_tournament_counts_by_age_group,
        max_hosting_deviation=max_hosting_deviation,
        max_hosting_days_per_month=max_hosting_days_per_month,
        events_by_club=events_by_club or None,
        fairness_thresholds=fairness_thresholds or None,
        seed=seed,
        penalty_hints=penalty_hints,
        allow_penalty_hint_relaxation=allow_penalty_hint_relaxation,
    )


def _tournament_from_dict(data: dict[str, Any]) -> Tournament:
    """Reconstruct a :class:`Tournament` from a serialised dict."""
    teams = [
        Team(
            club=t["club"],
            label=t["label"],
            age_group=t["age_group"],
            target_tournament_count=t.get("target_tournament_count"),
        )
        for t in data.get("teams", [])
    ]
    games = [
        Game(
            home=_find_team(teams, g["home"]),
            away=_find_team(teams, g["away"]),
            parallel_slot=g.get("parallel_slot", 0),
            round_number=g.get("round_number", 0),
        )
        for g in data.get("games", [])
    ]
    return Tournament(
        id=data.get("id", ""),
        date=date.fromisoformat(data["date"]),
        arena=data["arena"],
        age_group=data["age_group"],
        host_club=data.get("host_club"),
        teams=teams,
        games=games,
        cancelled=bool(data.get("cancelled", False)),
        cancellation_reason=data.get("cancellation_reason"),
        start_time=data.get("start_time"),
        preferanse_vekt=float(data.get("preferanse_vekt", 0.0)),
        scoring_weight_term=float(data.get("scoring_weight_term", 0.0)),
        manual_booking_reason=data.get("manual_booking_reason"),
    )


def _find_team(teams: list[Team], label: str) -> Team:
    """Find a Team by label; return a placeholder if missing."""
    for t in teams:
        if t.label == label:
            return t
    return Team(club="", label=label, age_group="")


# ---------------------------------------------------------------------------
