"""Fairness scoring helpers for `SeasonPlanner`."""

from __future__ import annotations

from typing import Dict, List

from tournament_scheduler.club_distances import compute_team_travel_distances
from tournament_scheduler.models import SeasonPlan
from tournament_scheduler.warnings import hosting_weekend_balance_breakdown

DEFAULT_FAIRNESS_THRESHOLDS = {
    "max_game_count_spread": 2,
    "max_hosting_deviation": 1,
    # Cumulative season travel across all away tournaments. RVV seasons with
    # nine clubs routinely land in the low-thousands of km, so a 700 km cap
    # was really a per-trip guess, not a realistic season-total threshold.
    "max_team_travel_km": 4000,
    "min_diversity_score": 0.75,
    "min_pairwise_matchup_score": 0.25,
    "min_month_balance_score": 0.75,
    "max_same_weekend_club_load": 3,
    "max_consecutive_weekend_club_load": 2,
    "max_holiday_stretch_club_load": 2,
}


def build_fairness_gate(planner, plan: SeasonPlan) -> Dict[str, object]:
    """Return a structured pass/warn/fail summary for key fairness metrics."""
    thresholds = planner.fairness_thresholds
    metrics: List[Dict[str, object]] = []

    def add_metric(
        key: str,
        label: str,
        value: float | int,
        threshold: float | int,
        *,
        direction: str,
        severity: str,
        detail: str,
        unit: str = "",
    ) -> None:
        if threshold is None:
            threshold_value = 0.0
        else:
            threshold_value = float(threshold)
        value_float = float(value)
        if direction == "max":
            within = value_float <= threshold_value
            if threshold_value <= 0:
                score = 100 if value_float <= 0 else 0
            elif within:
                score = 100
            else:
                score = max(0, int(round(100 * max(0.0, 2 - (value_float / threshold_value)))))
        else:
            within = value_float >= threshold_value
            if threshold_value <= 0:
                score = 100 if value_float > 0 else 0
            elif within:
                score = 100
            else:
                score = max(0, int(round(100 * max(0.0, value_float / threshold_value))))
        status = "pass" if within else ("fail" if severity == "fail" else "warn")
        metrics.append(
            {
                "key": key,
                "label": label,
                "value": value,
                "threshold": threshold,
                "direction": direction,
                "severity": severity,
                "status": status,
                "score": score,
                "unit": unit,
                "detail": detail,
            }
        )

    team_travel = compute_team_travel_distances(plan)
    max_team_travel = max(team_travel.values()) if team_travel else 0

    hosting_breakdown = planner._hosting_fairness_breakdown(plan)
    hosting_deviation = float(hosting_breakdown.get("max_deviation", 0.0))
    hosting_detail = str(hosting_breakdown.get("detail", ""))
    hosting_capacity_explained = bool(hosting_breakdown.get("max_deviation_capacity_explained", False))
    missing_calendar_clubs = list(hosting_breakdown.get("missing_calendar_clubs", []))

    same_weekend_load = 0
    weekend_loads: Dict[tuple[int, int], Dict[str, int]] = {}
    for tournament in plan.tournaments:
        iso_year, iso_week, _ = tournament.date.isocalendar()
        bucket = weekend_loads.setdefault((iso_year, iso_week), {})
        host_club = tournament.host_club or ""
        if host_club:
            bucket[host_club] = bucket.get(host_club, 0) + 1
    for loads in weekend_loads.values():
        if loads:
            same_weekend_load = max(same_weekend_load, max(loads.values()))
    weekend_detail = f"maks {same_weekend_load} turneringer fra samme klubb i samme uke"

    weekend_balance = hosting_weekend_balance_breakdown(planner, plan)
    consecutive_weekend_load = int(weekend_balance.get("max_consecutive_weekend_load", 0) or 0)
    holiday_stretch_load = int(weekend_balance.get("max_holiday_stretch_load", 0) or 0)
    consecutive_detail = str(weekend_balance.get("consecutive_detail", ""))
    holiday_detail = str(weekend_balance.get("holiday_detail", ""))

    age_group_spreads: List[float] = []
    skipped_age_groups_set = {entry["age_group"] for entry in plan.skipped_age_groups}
    teams_by_age_group: Dict[str, List] = {}
    for team in planner.roster.teams:
        teams_by_age_group.setdefault(team.age_group, []).append(team)
    for age_group, teams in teams_by_age_group.items():
        if age_group in skipped_age_groups_set:
            continue
        counts = [planner._team_game_counts.get(planner._team_key(team), 0) for team in teams]
        if counts:
            average = sum(counts) / len(counts)
            spread = max(counts) - min(counts)
            normalized = spread / max(average, 1.0)
            age_group_spreads.append(min(normalized, 1.0))
    normalized_game_count_spread = max(age_group_spreads) if age_group_spreads else float(plan.game_count_spread)

    add_metric(
        "game_count_spread",
        "Kamper per lag",
        round(normalized_game_count_spread, 3),
        thresholds.get("max_game_count_spread", planner.max_game_count_spread),
        direction="max",
        # Not a hard invariant like arena_day_collisions: an uneven game
        # count per team is a fairness quality concern, not an operationally
        # impossible schedule. Unlike hosting_deviation (wired to the
        # operator-configurable maxHostingDeviation federation default),
        # max_game_count_spread is not currently threaded through from Stage
        # 1 config in the main pipeline (tournament_scheduler.pipeline
        # .stage3_helpers) at all, so today this threshold is a
        # code-invented default (2), not an explicitly configured business
        # rule — exactly the kind of soft threshold issue #260 Phase 4 says
        # should not unconditionally fail a plan. Downgraded to "warn": the
        # measurement itself (value/threshold/detail) is unchanged, so the
        # LLM/agent controller and operator still see it, they just no
        # longer get a hard "fail" imposed by an unconfigured default.
        severity="warn",
        detail=f"Normalisert spredning per aldersgruppe er {normalized_game_count_spread:.3f} (rå spredning: {plan.game_count_spread} kamper, tak på [0, 1]).",
    )
    add_metric(
        "hosting_deviation",
        "Hjemmebanebelastning",
        hosting_deviation,
        thresholds.get("max_hosting_deviation", planner.max_hosting_deviation),
        direction="max",
        # A deviation the planner can trace to a real arena-capacity limit
        # (fallback_host_substitutions shows it tried and found no free
        # slot) isn't something retrying or replanning can fix — downgrade
        # to a warning so it's visible without blocking on something only
        # a human (freeing up ice time, adjusting the target) can resolve.
        severity="warn" if hosting_capacity_explained else "fail",
        detail=hosting_detail or "Aldersgruppevis fordeling av hjemmeturneringer ligger innenfor terskelen.",
    )
    if metrics and metrics[-1].get("key") == "hosting_deviation":
        metrics[-1]["age_group_breakdown"] = hosting_breakdown.get("age_group_breakdown", [])
    notes: List[Dict[str, object]] = []
    if missing_calendar_clubs:
        notes.append(
            {
                "key": "missing_calendar_clubs",
                "label": "Manglende kalenderdata",
                "detail": (
                    f"Kalenderdata mangler for {', '.join(missing_calendar_clubs)}; disse klubbene "
                    "er fortsatt med i avviksberegningen og beholder sin forholdsmessige "
                    "andel hjemmeturneringer. Istiden må planlegges manuelt i "
                    "«Må planlegges manuelt»-visningen."
                ),
                # Legacy key retained for existing consumers; these clubs are
                # no longer excluded from the actual fairness calculation.
                "excluded_clubs": missing_calendar_clubs,
                "manual_calendar_clubs": missing_calendar_clubs,
            }
        )
    add_metric(
        "travel_distance",
        "Reisebelastning",
        max_team_travel,
        thresholds.get("max_team_travel_km", DEFAULT_FAIRNESS_THRESHOLDS["max_team_travel_km"]),
        direction="max",
        severity="warn",
        detail=f"Lengst reisende lag har {max_team_travel} km total reise gjennom sesongen.",
        unit="km",
    )
    add_metric(
        "opponent_diversity",
        "Motstandervariasjon",
        plan.diversity_score,
        thresholds.get("min_diversity_score", DEFAULT_FAIRNESS_THRESHOLDS["min_diversity_score"]),
        direction="min",
        severity="warn",
        detail=f"Snittet av unik motstanderdekning er {plan.diversity_score:.3f}.",
    )
    add_metric(
        "pairwise_matchups",
        "Nye matchups",
        plan.pairwise_matchup_score,
        thresholds.get("min_pairwise_matchup_score", DEFAULT_FAIRNESS_THRESHOLDS["min_pairwise_matchup_score"]),
        direction="min",
        severity="warn",
        detail=f"Andel nye kampoppsett er {plan.pairwise_matchup_score:.3f}.",
    )
    add_metric(
        "month_balance",
        "Månedsbalanse",
        plan.month_balance_score,
        thresholds.get("min_month_balance_score", DEFAULT_FAIRNESS_THRESHOLDS["min_month_balance_score"]),
        direction="min",
        severity="warn",
        detail=f"Månedsbalansen er {plan.month_balance_score:.3f}.",
    )
    add_metric(
        "same_weekend_club_load",
        "Klubblast per helg",
        same_weekend_load,
        thresholds.get("max_same_weekend_club_load", DEFAULT_FAIRNESS_THRESHOLDS["max_same_weekend_club_load"]),
        direction="max",
        severity="warn",
        detail=weekend_detail,
    )
    add_metric(
        "consecutive_weekend_club_load",
        "Sammenhengende vertskapshelger",
        consecutive_weekend_load,
        thresholds.get("max_consecutive_weekend_club_load", DEFAULT_FAIRNESS_THRESHOLDS["max_consecutive_weekend_club_load"]),
        direction="max",
        severity="warn",
        detail=consecutive_detail or f"Maks {consecutive_weekend_load} sammenhengende helger for samme klubb.",
    )
    add_metric(
        "holiday_stretch_club_load",
        "Feriehelgelast",
        holiday_stretch_load,
        thresholds.get("max_holiday_stretch_club_load", DEFAULT_FAIRNESS_THRESHOLDS["max_holiday_stretch_club_load"]),
        direction="max",
        severity="warn",
        detail=holiday_detail or f"Maks {holiday_stretch_load} ferie-/helligdagshelger for samme klubb.",
    )
    add_metric(
        "arena_day_collisions",
        "Arena-/dagskollisjoner",
        len(getattr(plan, "arena_day_collisions", []) or []),
        0,
        direction="max",
        severity="fail",
        detail=(
            "Ingen dobbeltbooking av samme arena samme dag."
            if not getattr(plan, "arena_day_collisions", None)
            else f"{len(plan.arena_day_collisions)} kollisjon(er) der samme arena ble tildelt mer enn én turnering samme dag."
        ),
    )

    statuses = [str(m["status"]) for m in metrics]
    if "fail" in statuses:
        overall_status = "fail"
    elif "warn" in statuses:
        overall_status = "warn"
    else:
        overall_status = "pass"
    overall_score = int(round(sum(float(m["score"]) for m in metrics) / len(metrics))) if metrics else 100
    return {
        "status": overall_status,
        "score": overall_score,
        "metrics": metrics,
        "notes": notes,
        "thresholds": dict(thresholds),
    }
