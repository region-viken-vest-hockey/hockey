"""Fairness scoring helpers for `SeasonPlanner`."""

from __future__ import annotations

from typing import Dict, List

from tournament_scheduler.club_distances import compute_team_travel_distances
from tournament_scheduler.models import SeasonPlan, find_duplicate_labels, team_key
from tournament_scheduler.temporal_coverage import season_temporal_coverage, temporal_offenders
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
    # Largest gap (in weeks) allowed anywhere along a team's own
    # season_start -> first tournament -> ... -> last tournament -> season_end
    # chain. Replaces the older, narrower team_finish_gap (only compared a
    # team's last tournament to its age group's peers) and
    # team_intra_season_gap (only looked at gaps between existing
    # tournaments, ignoring the season boundaries) with one consolidated
    # temporal-coverage measurement. Set comfortably above the ordinary
    # Christmas/New Year break (which alone typically spans several weeks
    # for every team) so that break is not itself flagged as an excessive
    # hole.
    "max_team_temporal_gap_weeks": 8.0,
}


def _team_participation_records(plan: SeasonPlan) -> Dict[str, Dict[str, object]]:
    """Return per-team participation records built from *plan.tournaments*.

    Each record is ``{"club", "label", "age_group", "dates"}`` where
    ``dates`` is every date the team participated in a non-cancelled
    tournament (host or away — participation, not hosting, is what matters
    for temporal fairness). Keyed by the same disambiguated key as
    ``plan.team_game_counts`` so a label shared by teams in different clubs
    or age groups is never merged.
    """
    all_teams = [
        team
        for tournament in plan.tournaments
        if not tournament.cancelled
        for team in tournament.teams
    ]
    duplicate_labels = find_duplicate_labels(all_teams)

    records: Dict[str, Dict[str, object]] = {}
    for tournament in plan.tournaments:
        if tournament.cancelled:
            continue
        for team in tournament.teams:
            key = team_key(team, duplicate_labels)
            record = records.setdefault(
                key,
                {"club": team.club, "label": team.label, "age_group": team.age_group, "dates": []},
            )
            record["dates"].append(tournament.date)  # type: ignore[union-attr]
    return records


def build_fairness_gate(planner, plan: SeasonPlan) -> Dict[str, object]:
    """Return a structured pass/warn/fail summary for key fairness metrics.

    Issue #260 Phase 4 ("separate fairness measurement from soft default
    threshold policy"): the returned dict carries both the pre-existing
    blended report shape (``status``/``score``/``metrics``/``notes``/
    ``thresholds`` — kept at the top level and duplicated under
    ``legacy_summary`` for report/export/no-judge compatibility, unchanged)
    and a canonical split:

    - ``policy_gate``: only metrics with ``provenance`` ``"hard_invariant"``
      (a physically impossible schedule, e.g. an arena double-booking) or
      ``"configured"`` (a threshold actually threaded through from Stage 1
      config, e.g. ``maxHostingDeviation`` — not merely present as a code
      default). This is the only part of this return value the canonical
      LLM-directed decision path may treat as acceptance/control authority.
    - ``measurements``: every other metric — raw value/threshold/detail for
      context, never a canonical pass/warn/fail authority by itself. A
      soft/default metric outside its reference threshold must not make
      ``policy_gate`` warn/fail.

    A metric's ``provenance`` is a structural fact about *this pipeline's
    wiring* (is the threshold actually threaded through from Stage 1->3
    config, or only ever a code-level default in this module), not about
    whether an operator happened to override the default value.
    """
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
        provenance: str = "measurement",
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
                "provenance": provenance,
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
    # issue #305: this groups by ISO (year, week) -- an actual calendar week,
    # not a weekend -- so both the label and detail text must say "uke", not
    # "helg", to match what is actually computed.
    weekend_detail = f"maks {same_weekend_load} turneringer fra samme klubb i samme kalenderuke"

    weekend_balance = hosting_weekend_balance_breakdown(planner, plan)
    consecutive_weekend_load = int(weekend_balance.get("max_consecutive_weekend_load", 0) or 0)
    holiday_stretch_load = int(weekend_balance.get("max_holiday_stretch_load", 0) or 0)
    consecutive_detail = str(weekend_balance.get("consecutive_detail", ""))
    holiday_detail = str(weekend_balance.get("holiday_detail", ""))

    age_group_spread_values: List[int] = []
    skipped_age_groups_set = {entry["age_group"] for entry in plan.skipped_age_groups}
    teams_by_age_group: Dict[str, List] = {}
    for team in planner.roster.teams:
        teams_by_age_group.setdefault(team.age_group, []).append(team)
    for age_group, teams in teams_by_age_group.items():
        if age_group in skipped_age_groups_set:
            continue
        counts = [planner._team_game_counts.get(planner._team_key(team), 0) for team in teams]
        if counts:
            age_group_spread_values.append(max(counts) - min(counts))
    # issue #305: this used to report a spread normalized (and capped) to
    # [0, 1] compared against a raw-games threshold like 2 — a value that can
    # never warn/fail against that threshold, since it is mathematically
    # bounded below it. Report the raw per-age-group game-count spread
    # (whole games) instead, so the value and the threshold share the same
    # unit.
    worst_game_count_spread = max(age_group_spread_values) if age_group_spread_values else int(plan.game_count_spread)

    add_metric(
        "game_count_spread",
        "Kamper per lag",
        worst_game_count_spread,
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
        detail=f"Største spredning i én aldersgruppe er {worst_game_count_spread} kamper (mellom laget med flest og laget med færrest kamper).",
        unit=" kamper",
    )

    participation_records = _team_participation_records(plan)
    worst_temporal_gap_weeks = 0.0
    temporal_gap_detail = (
        "Ingen lag har et opphold i sesongdekningen (før første, mellom to, "
        "eller etter siste turnering) som overstiger terskelen."
    )
    temporal_offenders_detail: List[Dict[str, object]] = []
    if plan.start_date is not None and plan.end_date is not None:
        dates_by_team = {key: record["dates"] for key, record in participation_records.items()}
        team_meta = {
            key: (str(record["club"]), str(record["age_group"]))
            for key, record in participation_records.items()
        }
        coverages = season_temporal_coverage(plan.start_date, plan.end_date, dates_by_team, team_meta)
        worst_temporal_gap_days = max((c.max_gap_days for c in coverages), default=0)
        worst_temporal_gap_weeks = round(worst_temporal_gap_days / 7.0, 1)

        threshold_weeks = float(
            thresholds.get("max_team_temporal_gap_weeks", DEFAULT_FAIRNESS_THRESHOLDS["max_team_temporal_gap_weeks"])
        )
        offenders = temporal_offenders(coverages, threshold_days=int(round(threshold_weeks * 7)))
        for offender in offenders:
            record = participation_records.get(offender.team_key, {})
            label = record.get("label", offender.team_key)
            temporal_offenders_detail.append(
                {
                    "team": label,
                    "club": offender.club,
                    "age_group": offender.age_group,
                    "lead_gap_days": offender.lead_gap_days,
                    "finish_gap_days": offender.finish_gap_days,
                    "max_intra_gap_days": offender.max_intra_gap_days,
                    "max_gap_days": offender.max_gap_days,
                }
            )
        if offenders:
            worst = offenders[0]
            worst_record = participation_records.get(worst.team_key, {})
            worst_label = worst_record.get("label", worst.team_key)
            gap_kind = "opphold før første turnering"
            if worst.max_gap_days == worst.finish_gap_days and worst.finish_gap_days >= worst.lead_gap_days:
                gap_kind = "opphold etter siste turnering"
            elif worst.max_gap_days == worst.max_intra_gap_days:
                gap_kind = "opphold mellom to turneringer"
            worst_weeks = round(worst.max_gap_days / 7.0, 1)
            temporal_gap_detail = (
                f"{worst_label} ({worst.club}, {worst.age_group}) har det største oppholdet i "
                f"sesongdekningen: {worst_weeks:.1f} uker ({gap_kind}). "
                f"{len(offenders)} lag totalt overstiger terskelen på {threshold_weeks:.1f} uker."
            )
    add_metric(
        "team_temporal_coverage",
        "Sesongdekning per lag",
        worst_temporal_gap_weeks,
        thresholds.get("max_team_temporal_gap_weeks", DEFAULT_FAIRNESS_THRESHOLDS["max_team_temporal_gap_weeks"]),
        direction="max",
        severity="warn",
        detail=temporal_gap_detail,
        unit=" uker",
    )
    if metrics and metrics[-1].get("key") == "team_temporal_coverage":
        metrics[-1]["offenders"] = temporal_offenders_detail

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
        # Configured policy: max_hosting_deviation is threaded through from
        # Stage 1 config (cfg["maxHostingDeviation"]) into the planner
        # constructor in the main pipeline, unlike max_game_count_spread.
        provenance="configured",
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
        "Klubblast per uke",
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
        "Arena-/tidskollisjon",
        len(getattr(plan, "arena_day_collisions", []) or []),
        0,
        direction="max",
        severity="fail",
        # issue #305: turneringer kan dele samme arena samme dag -- det er
        # kun overlappende reserverte tidsintervaller (inkludert buffer) som
        # er en kollisjon. `plan.arena_day_collisions` bygges allerede fra
        # `arena_conflicts.find_arena_interval_collisions` (full
        # start-/sluttintervall-sjekk), så bare teksten under var feil.
        detail=(
            "Turneringer kan bruke samme arena samme dag, men reserverte "
            "tidsintervaller (inkludert nødvendig buffer) må ikke overlappe."
            if not getattr(plan, "arena_day_collisions", None)
            else f"{len(plan.arena_day_collisions)} kollisjon(er) der reserverte tidsintervaller i samme arena overlapper."
        ),
        # True hard invariant: an arena physically cannot host two
        # overlapping tournament intervals at once.
        provenance="hard_invariant",
    )

    statuses = [str(m["status"]) for m in metrics]
    if "fail" in statuses:
        overall_status = "fail"
    elif "warn" in statuses:
        overall_status = "warn"
    else:
        overall_status = "pass"
    overall_score = int(round(sum(float(m["score"]) for m in metrics) / len(metrics))) if metrics else 100
    legacy_summary = {
        "status": overall_status,
        "score": overall_score,
        "metrics": metrics,
        "notes": notes,
        "thresholds": dict(thresholds),
    }

    # Canonical split (issue #260 Phase 4): only hard invariants and
    # metrics whose threshold is actually threaded through from Stage 1
    # config are policy-gate authority; everything else is a measurement/
    # hint. A soft/default metric outside its reference threshold does not
    # make the policy gate warn/fail by itself.
    policy_gate_metrics = [m for m in metrics if m.get("provenance") in ("hard_invariant", "configured")]
    measurement_metrics = [m for m in metrics if m.get("provenance") not in ("hard_invariant", "configured")]
    policy_statuses = [str(m["status"]) for m in policy_gate_metrics]
    if "fail" in policy_statuses:
        policy_gate_status = "fail"
    elif "warn" in policy_statuses:
        policy_gate_status = "warn"
    else:
        policy_gate_status = "pass"

    return {
        # Legacy/report-compatible blended shape — unchanged, still the
        # top-level fields so existing exports/consumers/no-judge behavior
        # do not need to change in this pass. Duplicated under
        # legacy_summary to make explicit that it is not canonical policy
        # authority going forward.
        "status": overall_status,
        "score": overall_score,
        "metrics": metrics,
        "notes": notes,
        "thresholds": dict(thresholds),
        "legacy_summary": legacy_summary,
        # Canonical fields: the only part of this return value the
        # LLM-directed decision path may treat as acceptance/control
        # authority.
        "policy_gate": {
            "status": policy_gate_status,
            "metrics": policy_gate_metrics,
        },
        "measurements": measurement_metrics,
    }
