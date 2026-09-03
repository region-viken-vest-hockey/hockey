"""Stage 4 export helpers."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from ..models import Game, SeasonPlan, Team, Tournament

logger = logging.getLogger(__name__)


def _dict_to_plan(d: dict[str, Any]) -> SeasonPlan:
    """Reconstruct a :class:`SeasonPlan` from the checkpoint dict."""
    tournaments: list[Tournament] = []

    for t_dict in d.get("tournaments", []):
        teams = [
            Team(
                club=tm["club"],
                label=tm["label"],
                age_group=tm["age_group"],
                target_tournament_count=tm.get("target_tournament_count"),
            )
            for tm in t_dict.get("teams", [])
        ]
        team_by_label = {t.label: t for t in teams}

        games = []
        tournament_id = t_dict.get("id", "")
        date_str = t_dict.get("date", "")
        for g_dict in t_dict.get("games", []):
            home_label = g_dict.get("home", "")
            away_label = g_dict.get("away", "")
            home = team_by_label.get(home_label)
            away = team_by_label.get(away_label)
            if home and away:
                games.append(
                    Game(
                        home=home,
                        away=away,
                        parallel_slot=int(g_dict.get("parallel_slot", 0)),
                        round_number=int(g_dict.get("round_number", 0)),
                    )
                )
            else:
                logger.warning(
                    "Droppet kamp i turnering %s (%s): fant ikke laglabel(s) home=%r away=%r.",
                    tournament_id or "ukjent-id",
                    date_str or "ukjent-dato",
                    home_label,
                    away_label,
                )

        if not date_str:
            raise ValueError("Tournament date is required but missing or empty")
        tournament_date = date.fromisoformat(date_str)

        tournament_kwargs: dict[str, Any] = {
            "date": tournament_date,
            "arena": t_dict.get("arena", ""),
            "age_group": t_dict.get("age_group", ""),
            "teams": teams,
            "games": games,
            "host_club": t_dict.get("host_club"),
            "cancelled": bool(t_dict.get("cancelled", False)),
            "cancellation_reason": t_dict.get("cancellation_reason"),
            "start_time": t_dict.get("start_time"),
            "manual_booking_reason": t_dict.get("manual_booking_reason"),
        }
        if tournament_id:
            tournament_kwargs["id"] = tournament_id
        tournaments.append(Tournament(**tournament_kwargs))

    start_str = d.get("start_date")
    end_str = d.get("end_date")

    return SeasonPlan(
        tournaments=tournaments,
        start_date=date.fromisoformat(start_str) if start_str else None,
        end_date=date.fromisoformat(end_str) if end_str else None,
        diversity_score=float(d.get("diversity_score", 0.0)),
        pairwise_matchup_score=float(d.get("pairwise_matchup_score", 0.0)),
        month_balance_score=float(d.get("month_balance_score", 0.0)),
        arena_counts=dict(d.get("arena_counts", {})),
        team_game_counts=dict(d.get("team_game_counts", {})),
        game_count_spread=int(d.get("game_count_spread", 0)),
        game_count_spread_by_age_group=dict(d.get("game_count_spread_by_age_group", {})),
        fairness_gate=dict(d.get("fairness_gate", {})),
        skipped_age_groups=list(d.get("skipped_age_groups", [])),
        arena_day_collisions=list(d.get("arena_day_collisions", [])),
        team_last_game_dates={
            k: date.fromisoformat(v) for k, v in d.get("team_last_game_dates", {}).items()
        },
        manual_adjustments=dict(d.get("manual_adjustments", {})),
    )


# ---------------------------------------------------------------------------
