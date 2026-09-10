"""Planner-independent canonical rules/audit model (issue #277).

Unlike :mod:`tournament_scheduler.rules_report`, which formats a prose
explanation from `SeasonPlanner` private attributes, this module builds a
small, structured list of rule entries purely from the public,
serializable fields already on :class:`~tournament_scheduler.models.SeasonPlan`
(``fairness_gate``, ``arena_day_collisions``, ``unresolved_hosting_obligations``,
``unresolved_external_conflicts``, ``unresolved_participation_shortfalls``,
``shared_host_decisions``).

Because the source data is planner-independent, the same rules model can
describe a candidate produced by any engine (``local_search``, a future
``cp_sat`` shadow run from issue #276, ...) without depending on that
engine's internals.

A rule entry has the shape described in issue #277::

    {
        "id": str,
        "title": str,
        "type": "hard" | "required_obligation" | "soft" | "advisory" | "default",
        "scope": str,
        "owner": str,
        "description": str,
        "configured_value": object | None,
        "status": str,
    }
"""

from __future__ import annotations

from typing import Any

from .models import SeasonPlan

# Scope labels for the fixed set of fairness-gate metric keys. Keys not
# listed default to "sesong".
_METRIC_SCOPES: dict[str, str] = {
    "game_count_spread": "lag",
    "hosting_deviation": "klubb",
    "travel_distance": "lag",
    "opponent_diversity": "lag",
    "pairwise_matchups": "aldersgruppe",
    "month_balance": "sesong",
    "same_weekend_club_load": "klubb/helg",
    "consecutive_weekend_club_load": "klubb",
    "holiday_stretch_club_load": "klubb",
    "arena_day_collisions": "arena/dato",
}


def _metric_rule(metric: dict[str, Any]) -> dict[str, Any]:
    key = str(metric.get("key", ""))
    provenance = str(metric.get("provenance", "measurement"))
    if provenance == "hard_invariant":
        rule_type = "hard"
        owner = "deterministic_verifier"
    elif provenance == "configured":
        rule_type = "required_obligation"
        owner = "deterministic_measurement"
    else:
        rule_type = "soft"
        owner = "deterministic_measurement"
    status = str(metric.get("status", "pass"))
    detail = str(metric.get("detail", ""))
    value = metric.get("value")
    unit = str(metric.get("unit", ""))
    value_str = f"{value}{unit}" if unit else str(value)
    return {
        "id": f"metric_{key}",
        "title": str(metric.get("label", key)),
        "type": rule_type,
        "scope": _METRIC_SCOPES.get(key, "sesong"),
        "owner": owner,
        "description": detail or str(metric.get("label", key)),
        "configured_value": metric.get("threshold"),
        "status": f"{status} — {value_str}" if detail else status,
    }


def _hosting_obligation_rule(plan: SeasonPlan) -> dict[str, Any]:
    unresolved = list(plan.unresolved_hosting_obligations or [])
    if unresolved:
        names = ", ".join(
            f"{item.get('club', '?')} ({item.get('age_group', '?')})" for item in unresolved
        )
        status = f"{len(unresolved)} uløst: {names}"
    else:
        status = "Oppfylt"
    return {
        "id": "hosting_obligation_coverage",
        "title": "Klubb × aldersgruppe skal ha minst én hjemmeturnering",
        "type": "required_obligation",
        "scope": "klubb × aldersgruppe",
        "owner": "deterministic_placement_workflow",
        "description": (
            "Hver klubb med minst ett registrert lag i en aldersgruppe skal ha minst én "
            "hjemmeturnering i akkurat den aldersgruppen. Uløste krav holdes uløst i stedet "
            "for å stille en annen klubb som vert, og legges i «Må planlegges manuelt»."
        ),
        "configured_value": "≥1 hjemmeturnering",
        "status": status,
    }


def _external_conflict_rule(plan: SeasonPlan) -> dict[str, Any]:
    unresolved = list(plan.unresolved_external_conflicts or [])
    if unresolved:
        names = ", ".join(
            f"{item.get('tournament_id', '?')} ({item.get('host_club', '?')})" for item in unresolved
        )
        status = f"{len(unresolved)} uløst: {names}"
    else:
        status = "Oppfylt"
    return {
        "id": "external_calendar_conflicts",
        "title": "Vertskapets istid må ikke kollidere med en ekstern hallbooking",
        "type": "required_obligation",
        "scope": "turnering × arena",
        "owner": "deterministic_placement_workflow",
        "description": (
            "Når vertskapet har en reell kollisjon med en kjent ekstern kalenderbooking som "
            "verken planleggeren eller optimeringen klarte å unngå, avvises ikke hele planen — "
            "konflikten legges i «Må planlegges manuelt» i stedet."
        ),
        "configured_value": "Ingen kollisjon",
        "status": status,
    }


def _participation_shortfall_rule(plan: SeasonPlan) -> dict[str, Any]:
    unresolved = list(plan.unresolved_participation_shortfalls or [])
    if unresolved:
        names = ", ".join(
            f"{item.get('label', '?')} ({item.get('actual', '?')}/{item.get('target', '?')})"
            for item in unresolved
        )
        status = f"{len(unresolved)} avvik: {names}"
    else:
        status = "Oppfylt"
    return {
        "id": "participation_shortfalls",
        "title": "Lag bør nå sitt mål for antall turneringsdeltakelser",
        "type": "required_obligation",
        "scope": "lag",
        "owner": "deterministic_placement_workflow",
        "description": (
            "Når et lags faktiske antall turneringer avviker fra måltallet (oftest fordi det "
            "ikke fantes nok ledige turneringsplasser, eller fordi laget fikk flere enn "
            "målet), avvises ikke hele planen — avviket er et planleggingskvalitet-avvik, "
            "ikke istidsarbeid, og rapporteres her i stedet for i «Må planlegges manuelt»."
        ),
        "configured_value": "Faktisk = mål",
        "status": status,
    }


def _shared_host_rules(plan: SeasonPlan) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for decision in plan.shared_host_decisions or []:
        registration = str(decision.get("registration", "?"))
        age_group = str(decision.get("age_group", "?"))
        chosen_club = str(decision.get("chosen_club", "?"))
        decided_by = str(decision.get("decided_by", "")) or "ukjent"
        rules.append(
            {
                "id": f"shared_host_{registration}_{age_group}",
                "title": f"Delt vertskap: {registration} ({age_group})",
                "type": "soft",
                "scope": "delt klubbregistrering",
                "owner": "llm_controller",
                "description": (
                    str(decision.get("rationale", ""))
                    or "Hvilken konstituerende klubb som bærer vertskapsansvaret for denne "
                    "aldersgruppen er en kontekstuell rettferdighetsvurdering avgjort av "
                    "LLM/kontroller blant registreringens egne deltakerklubber."
                ),
                "configured_value": chosen_club,
                "status": f"Løst av {decided_by}" if decided_by != "ukjent" else "Løst",
            }
        )
    return rules


def build_rules_model(plan: SeasonPlan) -> list[dict[str, Any]]:
    """Return the planner-independent canonical rules list for *plan*.

    Sourced only from public :class:`SeasonPlan` fields, never from
    `SeasonPlanner` private attributes, so the same function works
    regardless of which engine produced the candidate.
    """
    gate = plan.fairness_gate if isinstance(plan.fairness_gate, dict) else {}
    policy_gate = gate.get("policy_gate", {}) if isinstance(gate.get("policy_gate"), dict) else {}
    policy_metrics = list(policy_gate.get("metrics", []) or [])
    measurement_metrics = list(gate.get("measurements", []) or [])
    # Fall back to the legacy blended `metrics` list when the canonical
    # split isn't present (e.g. hand-built plans in tests).
    if not policy_metrics and not measurement_metrics:
        all_metrics = list(gate.get("metrics", []) or [])
    else:
        all_metrics = policy_metrics + measurement_metrics

    rules: list[dict[str, Any]] = [_metric_rule(m) for m in all_metrics if isinstance(m, dict)]
    rules.append(_hosting_obligation_rule(plan))
    rules.append(_external_conflict_rule(plan))
    rules.append(_participation_shortfall_rule(plan))
    rules.extend(_shared_host_rules(plan))
    return rules
