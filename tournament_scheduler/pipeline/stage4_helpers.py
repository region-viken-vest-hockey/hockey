"""Stage 4 export helpers."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from ..models import Game, SeasonPlan, Team, Tournament

logger = logging.getLogger(__name__)


def build_tournament_placement_entries(plan: SeasonPlan) -> list[dict[str, str]]:
    """Build manual-placement report rows for `plan.unresolved_tournament_placements`.

    issue #323 P0: a tournament whose participant set (selected first, per
    all hard constraints) had no candidate host among the participants' own
    physical clubs with a free arena/time slot -- no tournament exists to
    attach this to (the point is that nothing was placed), so
    tournament-shaped fields are left blank. The original participant set
    was never mutated to fit an unrelated host.
    """
    entries: list[dict[str, str]] = []
    for item in getattr(plan, "unresolved_tournament_placements", None) or []:
        age_group = str(item.get("age_group", "") or "")
        date_value = str(item.get("date", "") or "")
        candidate_hosts = item.get("candidate_hosts") or []
        participant_clubs = item.get("participant_clubs") or []
        participant_teams = item.get("participant_teams") or []
        # issue #330: prefer the exact roster over the deduplicated club set
        # -- "Participants: Frisk Asker, Jar" reads as a two-team tournament
        # when it may have been several Frisk/Jar teams. Older/hand-built
        # checkpoint records without `participant_teams` fall back to an
        # explicit "unknown" marker rather than guessing a team count from
        # the club list.
        if participant_teams:
            team_count = item.get("participant_team_count", len(participant_teams))
            team_labels = ", ".join(str(t.get("label") or t.get("club") or "?") for t in participant_teams)
            roster_clause = f"Team count: {team_count}. Teams: {team_labels}. "
        else:
            roster_clause = "Team count: ukjent (eldre eksportdata uten lagliste). "
        # issue #329: whether a hosting-deficit-biased alternate roster was
        # tried before giving up -- lets the operator/auditor tell "no
        # alternative composition existed at all" apart from "an alternative
        # was tried and still failed".
        if item.get("required_duration_minutes"):
            duration_clause = (
                f"Required duration: {item.get('required_duration_minutes')} min "
                f"(base ice {item.get('configured_ice_time_minutes', 'ukjent')} min + "
                f"round buffer {item.get('round_buffer_minutes', 'ukjent')} min for "
                f"{item.get('round_count', 'ukjent')} rounds). "
            )
        else:
            duration_clause = ""
        if "alternate_roster_attempted" in item:
            retry_clause = (
                "Alternativ lagsammensetning forsøkt: "
                f"{'ja' if item.get('alternate_roster_attempted') else 'nei'}. "
            )
        else:
            retry_clause = ""
        entries.append(
            {
                "type": "MANUAL PLACEMENT REQUIRED — ingen vertsklubb blant deltakerne",
                "category": str(item.get("category") or "manual_tournament_placement"),
                "date": date_value,
                "arena": "",
                "host_club": "",
                "age_group": age_group,
                "tournament_id": "",
                "interval": "",
                "conflicting_tournament_id": "",
                "conflicting_age_group": "",
                "conflicting_interval": "",
                "required_duration_minutes": str(item.get("required_duration_minutes") or ""),
                "configured_ice_time_minutes": str(item.get("configured_ice_time_minutes") or ""),
                "round_count": str(item.get("round_count") or ""),
                "round_buffer_minutes": str(item.get("round_buffer_minutes") or ""),
                "message": (
                    "MANUAL PLACEMENT REQUIRED. "
                    f"Age group: {age_group}. Date: {date_value or 'ukjent'}. "
                    f"Participant clubs: {', '.join(str(c) for c in participant_clubs) or 'ukjent'}. "
                    f"{roster_clause}"
                    f"{duration_clause}"
                    f"{retry_clause}"
                    f"Candidate hosts tried: {', '.join(str(c) for c in candidate_hosts) or 'ingen'}. "
                    "Reason: none of the selected participants' clubs had a legal, free arena/time slot. "
                    "Action: RVV must manually assign a host/arena/time for this age group and date."
                ),
            }
        )
    return entries


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
        same_date_capacity_evidence=list(d.get("same_date_capacity_evidence", [])),
        arena_day_collisions=list(d.get("arena_day_collisions", [])),
        unresolved_hosting_obligations=list(d.get("unresolved_hosting_obligations", [])),
        targeted_roster_repairs=list(d.get("targeted_roster_repairs", [])),
        same_age_hosting_repairs=list(d.get("same_age_hosting_repairs", [])),
        cross_age_hosting_repairs=list(d.get("cross_age_hosting_repairs", [])),
        unresolved_external_conflicts=list(d.get("unresolved_external_conflicts", [])),
        unresolved_participation_shortfalls=list(d.get("unresolved_participation_shortfalls", [])),
        unresolved_tournament_placements=list(d.get("unresolved_tournament_placements", [])),
        club_participation_fairness=list(d.get("club_participation_fairness", [])),
        participation_targets_by_age_group={
            ag: dict(targets) for ag, targets in d.get("participation_targets_by_age_group", {}).items()
        },
        shared_host_decisions=list(d.get("shared_host_decisions", [])),
        operator_waivers=list(d.get("operator_waivers", [])),
        operator_waived_violations=list(d.get("operator_waived_violations", [])),
        team_last_game_dates={
            k: date.fromisoformat(v) for k, v in d.get("team_last_game_dates", {}).items()
        },
        manual_adjustments=dict(d.get("manual_adjustments", {})),
    )


# ---------------------------------------------------------------------------
