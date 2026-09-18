"""Versioned serialization boundary for :class:`SeasonPlan`.

The default ``to_dict`` output is the historical Stage 3 checkpoint payload
(``v1``) so existing checkpoints, fingerprints and exports remain stable.
Callers that need a self-describing standalone payload may request the explicit
``schema_version`` field with ``include_schema_version=True``. ``from_dict``
loads both historical unversioned v1 payloads and explicit v1 payloads.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from tournament_scheduler.club_registry import canonicalize_club_name
from tournament_scheduler.models import Game, SeasonPlan, Team, Tournament

SEASON_PLAN_SCHEMA_VERSION = 1
logger = logging.getLogger(__name__)


class SeasonPlanCodec:
    """Public codec for stable ``SeasonPlan <-> dict`` conversion."""

    schema_version = SEASON_PLAN_SCHEMA_VERSION

    @classmethod
    def to_dict(cls, plan: SeasonPlan, *, include_schema_version: bool = False) -> dict[str, Any]:
        """Convert *plan* to the canonical v1 checkpoint dict.

        ``include_schema_version`` is opt-in to preserve existing checkpoint
        bytes/keys where the plan is embedded in older Stage 3 payloads.
        """

        participations: dict[str, int] = {}
        known_tournament_ids = {t.id for t in plan.tournaments if t.id}
        for tournament in plan.tournaments:
            known_tournament_ids.update(tournament.derived_from or [])
            for team in tournament.teams:
                participations[team.label] = participations.get(team.label, 0) + 1

        checkpoint: dict[str, Any] = {
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
            "team_last_game_dates": {k: v.isoformat() for k, v in plan.team_last_game_dates.items()},
            "skipped_age_groups": list(plan.skipped_age_groups),
            "same_date_capacity_evidence": list(plan.same_date_capacity_evidence),
            "arena_day_collisions": list(plan.arena_day_collisions),
            "unresolved_hosting_obligations": list(plan.unresolved_hosting_obligations),
            "targeted_roster_repairs": list(plan.targeted_roster_repairs),
            "same_age_hosting_repairs": list(plan.same_age_hosting_repairs),
            "cross_age_hosting_repairs": list(plan.cross_age_hosting_repairs),
            "unresolved_external_conflicts": list(plan.unresolved_external_conflicts),
            "unresolved_participation_shortfalls": list(plan.unresolved_participation_shortfalls),
            "participation_club_pools": list(plan.participation_club_pools),
            "unresolved_tournament_placements": list(plan.unresolved_tournament_placements),
            "club_participation_fairness": list(plan.club_participation_fairness),
            "participation_targets_by_age_group": dict(plan.participation_targets_by_age_group),
            "shared_host_decisions": list(plan.shared_host_decisions),
            "operator_waivers": list(plan.operator_waivers),
            "operator_waived_violations": list(plan.operator_waived_violations),
            "calendar_interpretations": list(plan.calendar_interpretations),
            "tournaments": [cls.tournament_to_dict(t) for t in plan.tournaments],
            "identity_registry": {"known_tournament_ids": sorted(known_tournament_ids)},
        }
        if include_schema_version:
            checkpoint["schema_version"] = cls.schema_version
        if plan.manual_adjustments:
            checkpoint["manual_adjustments"] = plan.manual_adjustments
        if plan.date_preference_weights:
            checkpoint["date_preference_weights"] = list(plan.date_preference_weights)
        return checkpoint

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SeasonPlan:
        """Reconstruct a :class:`SeasonPlan` from a v1 checkpoint dict."""
        version = data.get("schema_version", cls.schema_version)
        if int(version) != cls.schema_version:
            raise ValueError(f"Unsupported SeasonPlan schema_version: {version!r}")

        start_str = data.get("start_date")
        end_str = data.get("end_date")
        return SeasonPlan(
            tournaments=[cls.tournament_from_dict(t) for t in data.get("tournaments", [])],
            start_date=date.fromisoformat(start_str) if start_str else None,
            end_date=date.fromisoformat(end_str) if end_str else None,
            diversity_score=float(data.get("diversity_score", 0.0)),
            pairwise_matchup_score=float(data.get("pairwise_matchup_score", 0.0)),
            month_balance_score=float(data.get("month_balance_score", 0.0)),
            arena_counts=dict(data.get("arena_counts", {})),
            team_game_counts=dict(data.get("team_game_counts", {})),
            game_count_spread=int(data.get("game_count_spread", 0)),
            game_count_spread_by_age_group=dict(data.get("game_count_spread_by_age_group", {})),
            fairness_gate=dict(data.get("fairness_gate", {})),
            team_last_game_dates={k: date.fromisoformat(v) for k, v in data.get("team_last_game_dates", {}).items()},
            skipped_age_groups=list(data.get("skipped_age_groups", [])),
            same_date_capacity_evidence=list(data.get("same_date_capacity_evidence", [])),
            manual_adjustments=dict(data.get("manual_adjustments", {})),
            arena_day_collisions=list(data.get("arena_day_collisions", [])),
            date_preference_weights=list(data.get("date_preference_weights", [])),
            unresolved_hosting_obligations=list(data.get("unresolved_hosting_obligations", [])),
            targeted_roster_repairs=list(data.get("targeted_roster_repairs", [])),
            same_age_hosting_repairs=list(data.get("same_age_hosting_repairs", [])),
            cross_age_hosting_repairs=list(data.get("cross_age_hosting_repairs", [])),
            unresolved_external_conflicts=list(data.get("unresolved_external_conflicts", [])),
            unresolved_participation_shortfalls=list(data.get("unresolved_participation_shortfalls", [])),
            participation_club_pools=list(data.get("participation_club_pools", [])),
            unresolved_tournament_placements=list(data.get("unresolved_tournament_placements", [])),
            club_participation_fairness=list(data.get("club_participation_fairness", [])),
            participation_targets_by_age_group={
                ag: dict(targets) for ag, targets in data.get("participation_targets_by_age_group", {}).items()
            },
            shared_host_decisions=list(data.get("shared_host_decisions", [])),
            operator_waivers=list(data.get("operator_waivers", [])),
            operator_waived_violations=list(data.get("operator_waived_violations", [])),
            calendar_interpretations=list(data.get("calendar_interpretations", [])),
        )

    @staticmethod
    def team_to_dict(team: Team) -> dict[str, Any]:
        payload: dict[str, Any] = {"club": team.club, "label": team.label, "age_group": team.age_group}
        if team.target_tournament_count is not None:
            payload["target_tournament_count"] = team.target_tournament_count
        return payload

    @classmethod
    def tournament_to_dict(cls, tournament: Tournament) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": tournament.id,
            "date": tournament.date.isoformat(),
            "arena": tournament.arena,
            "age_group": tournament.age_group,
            "host_club": tournament.host_club,
            "teams": [cls.team_to_dict(team) for team in tournament.teams],
            "games": [cls.game_to_dict(game) for game in tournament.games],
            "start_time": tournament.start_time,
        }
        if tournament.derived_from:
            payload["derived_from"] = list(tournament.derived_from)
        if tournament.cancelled:
            payload["cancelled"] = True
            payload["cancellation_reason"] = tournament.cancellation_reason
        if tournament.preferanse_vekt != 0.0:
            payload["preferanse_vekt"] = tournament.preferanse_vekt
        if tournament.scoring_weight_term != 0.0:
            payload["scoring_weight_term"] = tournament.scoring_weight_term
        if tournament.manual_booking_reason:
            payload["manual_booking_reason"] = tournament.manual_booking_reason
        if tournament.requires_host_confirmation:
            payload["requires_host_confirmation"] = True
        if tournament.host_confirmation_reason:
            payload["host_confirmation_reason"] = tournament.host_confirmation_reason
        return payload

    @staticmethod
    def game_to_dict(game: Game) -> dict[str, Any]:
        return {
            "home": game.home.label,
            "away": game.away.label,
            "parallel_slot": game.parallel_slot,
            "round_number": game.round_number,
        }

    @classmethod
    def tournament_from_dict(cls, data: dict[str, Any]) -> Tournament:
        teams = [cls.team_from_dict(t) for t in data.get("teams", [])]
        team_by_label = {team.label: team for team in teams}
        games = []
        tournament_id = data.get("id", "")
        for game_data in data.get("games", []):
            home_label = game_data.get("home", "")
            away_label = game_data.get("away", "")
            home = team_by_label.get(home_label)
            away = team_by_label.get(away_label)
            if home and away:
                games.append(
                    Game(
                        home=home,
                        away=away,
                        parallel_slot=int(game_data.get("parallel_slot", 0)),
                        round_number=int(game_data.get("round_number", 0)),
                    )
                )
            else:
                logger.warning(
                    "Droppet kamp i turnering %s (%s): fant ikke laglabel(s) home=%r away=%r.",
                    tournament_id or "ukjent-id",
                    data.get("date", "") or "ukjent-dato",
                    home_label,
                    away_label,
                )
        date_str = data.get("date", "")
        if not date_str:
            raise ValueError("Tournament date is required but missing or empty")
        kwargs: dict[str, Any] = {
            "id": data.get("id", ""),
            "derived_from": list(data.get("derived_from", []) or []),
            "date": date.fromisoformat(date_str),
            "arena": data.get("arena", ""),
            "age_group": data.get("age_group", ""),
            "host_club": data.get("host_club"),
            "teams": teams,
            "games": games,
            "cancelled": bool(data.get("cancelled", False)),
            "cancellation_reason": data.get("cancellation_reason"),
            "start_time": data.get("start_time"),
            "preferanse_vekt": float(data.get("preferanse_vekt", 0.0)),
            "scoring_weight_term": float(data.get("scoring_weight_term", 0.0)),
            "manual_booking_reason": data.get("manual_booking_reason"),
            "requires_host_confirmation": bool(data.get("requires_host_confirmation", False)),
            "host_confirmation_reason": data.get("host_confirmation_reason"),
        }
        return Tournament(**kwargs)

    @staticmethod
    def team_from_dict(data: dict[str, Any]) -> Team:
        return Team(
            club=canonicalize_club_name(data["club"]),
            label=data["label"],
            age_group=data["age_group"],
            target_tournament_count=data.get("target_tournament_count"),
        )


def season_plan_to_dict(plan: SeasonPlan, *, include_schema_version: bool = False) -> dict[str, Any]:
    """Public function wrapper for :meth:`SeasonPlanCodec.to_dict`."""
    return SeasonPlanCodec.to_dict(plan, include_schema_version=include_schema_version)


def season_plan_from_dict(data: dict[str, Any]) -> SeasonPlan:
    """Public function wrapper for :meth:`SeasonPlanCodec.from_dict`."""
    return SeasonPlanCodec.from_dict(data)


def tournament_from_dict(data: dict[str, Any]) -> Tournament:
    """Public nested-object decoder for persisted tournament payloads."""
    return SeasonPlanCodec.tournament_from_dict(data)


def resolve_plan_dict(plan_raw: Any) -> dict[str, Any]:
    """Return a canonical dict from a ``SeasonPlan`` or already-encoded dict."""
    if hasattr(plan_raw, "__dict__") and not isinstance(plan_raw, dict):
        return season_plan_to_dict(plan_raw)
    if isinstance(plan_raw, dict):
        return plan_raw
    return {}
