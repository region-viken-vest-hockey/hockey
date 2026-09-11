"""Planner-independent canonical rules/audit model (issue #277, #305).

Unlike :mod:`tournament_scheduler.rules_report`, which formats a prose
explanation from `SeasonPlanner` private attributes, this module builds a
small, structured list of rule entries purely from the public, serializable
fields already on :class:`~tournament_scheduler.models.SeasonPlan`
(``fairness_gate``, ``arena_day_collisions``, ``unresolved_hosting_obligations``,
``unresolved_external_conflicts``, ``unresolved_participation_shortfalls``,
``shared_host_decisions``, ``manual_adjustments``, ``tournaments`` /
``Tournament.manual_booking_reason``).

Because the source data is planner-independent, the same rules model can
describe a candidate produced by any engine (``local_search``, a future
``cp_sat`` shadow run from issue #276, ...) without depending on that
engine's internals.

A rule entry has the shape::

    {
        "id": str,
        "title": str,
        "type": "hard" | "required_obligation" | "soft" | "advisory" | "decision" | "default",
        "scope": str,
        "owner": str,
        "description": str,
        "configured_value": object | None,
        "status": str,
        "ok": bool,
        "detail_rows": list[dict] | None,   # optional drill-down evidence
    }

``type`` groups the entries into the four sections the Regler view shows:
hard constraints, operational obligations, soft quality goals, and
decisions/exceptions for this run (see :func:`group_rules_by_type`).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .models import SeasonPlan

# Scope labels for the fixed set of fairness-gate metric keys. Keys not
# listed default to "sesong".
_METRIC_SCOPES: dict[str, str] = {
    "game_count_spread": "lag",
    "team_temporal_coverage": "lag",
    "hosting_deviation": "klubb",
    "travel_distance": "lag",
    "opponent_diversity": "lag",
    "pairwise_matchups": "aldersgruppe",
    "month_balance": "sesong",
    "same_weekend_club_load": "klubb/uke",
    "consecutive_weekend_club_load": "klubb",
    "holiday_stretch_club_load": "klubb",
    "arena_day_collisions": "arena/tid",
}

RULE_TYPES = ("hard", "required_obligation", "soft", "decision", "advisory", "default")


def _active_tournaments(plan: SeasonPlan) -> list[Any]:
    return [t for t in plan.tournaments if not t.cancelled]


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
    rule: dict[str, Any] = {
        "id": f"metric_{key}",
        "title": str(metric.get("label", key)),
        "type": rule_type,
        "scope": _METRIC_SCOPES.get(key, "sesong"),
        "owner": owner,
        "description": detail or str(metric.get("label", key)),
        "configured_value": metric.get("threshold"),
        "status": f"{status} — {value_str}" if detail else status,
        "ok": status not in ("fail", "warn"),
    }
    if key == "hosting_deviation" and metric.get("age_group_breakdown"):
        rule["detail_rows"] = {
            "label": "Vis vertskapsfordeling",
            "kind": "hosting",
            "rows": list(metric["age_group_breakdown"]),
        }
    return rule


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
        "ok": not unresolved,
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
        "ok": not unresolved,
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
        "title": "Lag under sitt mål for antall turneringsdeltakelser",
        "type": "required_obligation",
        "scope": "lag",
        "owner": "deterministic_placement_workflow",
        "description": (
            "Når et lags faktiske antall turneringer er lavere enn måltallet (oftest fordi det "
            "ikke fantes nok ledige turneringsplasser), avvises ikke hele planen — dette er en "
            "ikke-blokkerende mangel, ikke istidsarbeid, og rapporteres her i stedet for i "
            "«Må planlegges manuelt». Et lag som er planlagt *over* et eksplisitt mål er derimot "
            "et hardt verifikatoravvik — se «Deltakelsesmål må ikke overskrides» blant hardkravene."
        ),
        "configured_value": "Faktisk ≥ mål",
        "status": status,
        "ok": not unresolved,
    }


def _participation_target_exceeded_rule(plan: SeasonPlan) -> dict[str, Any]:
    """Hard rule: a team scheduled above its *explicit* participation target.

    Mirrors ``planning_contract.verify_candidate``'s ``participation_target_exceeded``
    violation code. Only teams with an explicit per-team
    ``target_tournament_count`` are checked — a planner-inferred target is
    deliberately never reproduced here (same rationale as the verifier).
    """
    counts: dict[tuple[str, str, str], int] = {}
    targets: dict[tuple[str, str, str], int] = {}
    for tournament in _active_tournaments(plan):
        for team in tournament.teams:
            identity = (team.club, team.label, team.age_group)
            counts[identity] = counts.get(identity, 0) + 1
            if team.target_tournament_count is not None:
                targets[identity] = team.target_tournament_count

    exceeded = [
        f"{identity[1]} ({counts[identity]}/{targets[identity]})"
        for identity, target in targets.items()
        if counts.get(identity, 0) > target
    ]
    status = "Oppfylt" if not exceeded else f"{len(exceeded)} avvik: {', '.join(sorted(exceeded))}"
    return {
        "id": "participation_target_exceeded",
        "title": "Deltakelsesmål må ikke overskrides",
        "type": "hard",
        "scope": "lag",
        "owner": "deterministic_verifier",
        "description": (
            "Et lag med et eksplisitt turneringsmål (satt per lag) skal aldri planlegges i flere "
            "turneringer enn det målet. Dette avvises som et hardt verifikatoravvik "
            "(`participation_target_exceeded`), ulikt et lag som havner *under* målet, som er en "
            "ikke-blokkerende mangel (se «Lag under sitt mål» blant forpliktelsene)."
        ),
        "configured_value": "Faktisk ≤ eksplisitt mål",
        "status": status,
        "ok": not exceeded,
    }


def _age_group_exact_match_rule(plan: SeasonPlan) -> dict[str, Any]:
    """Hard rule: a team's age group must exactly equal its tournament's.

    Mirrors ``verify_candidate``'s ``age_group_mismatch`` check. This is
    also how U/JU separation is enforced: "U10" and "JU10" are always two
    distinct category strings and are never mixed in the same tournament.
    """
    mismatches = [
        f"{team.label} ({team.age_group}) i {tournament.id} ({tournament.age_group})"
        for tournament in _active_tournaments(plan)
        for team in tournament.teams
        if team.age_group and team.age_group != tournament.age_group
    ]
    status = "Oppfylt" if not mismatches else f"{len(mismatches)} avvik: {', '.join(mismatches)}"
    return {
        "id": "age_group_exact_match",
        "title": "Lagets aldersgruppe må være identisk med turneringens",
        "type": "hard",
        "scope": "lag × turnering",
        "owner": "deterministic_verifier",
        "description": (
            "Aldersgruppen er en eksakt kategori-streng (f.eks. «U10» eller «JU10»), aldri bare "
            "det numeriske alderstallet. U- og JU-kategorier blandes derfor aldri i samme "
            "turnering, selv om de deler samme tall — «U10» og «JU10» er alltid to forskjellige "
            "turneringer."
        ),
        "configured_value": "Eksakt match",
        "status": status,
        "ok": not mismatches,
    }


def _no_double_participation_rule(plan: SeasonPlan) -> dict[str, Any]:
    """Hard rule: a team cannot play in two tournaments on the same date."""
    by_identity: dict[tuple[str, str, str], dict[Any, list[str]]] = defaultdict(lambda: defaultdict(list))
    for tournament in _active_tournaments(plan):
        for team in tournament.teams:
            identity = (team.club, team.label, team.age_group)
            by_identity[identity][tournament.date].append(tournament.id)

    conflicts = [
        f"{identity[1]} ({tdate.isoformat()}): {', '.join(ids)}"
        for identity, by_date in by_identity.items()
        for tdate, ids in by_date.items()
        if len(ids) > 1
    ]
    status = "Oppfylt" if not conflicts else f"{len(conflicts)} avvik: {', '.join(conflicts)}"
    return {
        "id": "no_same_date_double_participation",
        "title": "Et lag kan ikke delta i flere turneringer samme dato",
        "type": "hard",
        "scope": "lag × dato",
        "owner": "deterministic_verifier",
        "description": "Et lag kan ikke stå oppført i mer enn én turnering på samme dato.",
        "configured_value": "Maks 1 turnering per lag per dato",
        "status": status,
        "ok": not conflicts,
    }


def _date_window_rule(plan: SeasonPlan) -> dict[str, Any]:
    outside: list[str] = []
    if plan.start_date and plan.end_date:
        outside = [
            f"{tournament.id} ({tournament.date.isoformat()})"
            for tournament in _active_tournaments(plan)
            if not (plan.start_date <= tournament.date <= plan.end_date)
        ]
    status = "Oppfylt" if not outside else f"{len(outside)} avvik: {', '.join(outside)}"
    return {
        "id": "date_within_planning_window",
        "title": "Turneringsdatoer må ligge innenfor planleggingsvinduet",
        "type": "hard",
        "scope": "turnering",
        "owner": "deterministic_verifier",
        "description": "Alle turneringer skal ha en dato innenfor sesongens start-/sluttdato.",
        "configured_value": (
            f"{plan.start_date.isoformat()} – {plan.end_date.isoformat()}"
            if plan.start_date and plan.end_date
            else "Ikke satt"
        ),
        "status": status,
        "ok": not outside,
    }


def _manual_adjustment_rules(plan: SeasonPlan) -> list[dict[str, Any]]:
    """Hard rules sourced from ``plan.manual_adjustments`` (operator restrictions)."""
    manual = plan.manual_adjustments or {}
    active = _active_tournaments(plan)
    active_dates = {t.date for t in active}
    active_ids = {t.id for t in active}

    def _parse_dates(raw: list[str]) -> set[Any]:
        from datetime import date as _date

        parsed = set()
        for value in raw:
            try:
                parsed.add(_date.fromisoformat(str(value)))
            except ValueError:
                continue
        return parsed

    banned_dates = _parse_dates(list(manual.get("banned_dates", []) or []))
    used_banned = sorted(d.isoformat() for d in (banned_dates & active_dates))
    rules = [
        {
            "id": "banned_dates_not_used",
            "title": "Sperrede datoer må ikke brukes",
            "type": "hard",
            "scope": "dato",
            "owner": "deterministic_verifier",
            "description": "Datoer operatøren har sperret for planlegging skal aldri få en turnering.",
            "configured_value": f"{len(banned_dates)} sperret(e) dato(er)" if banned_dates else "Ingen konfigurert",
            "status": "Oppfylt" if not used_banned else f"{len(used_banned)} avvik: {', '.join(used_banned)}",
            "ok": not used_banned,
        }
    ]

    excluded_hosts = set(manual.get("excluded_host_clubs", []) or [])
    used_excluded = sorted(
        {t.host_club for t in active if t.host_club and t.host_club in excluded_hosts}
    )
    rules.append(
        {
            "id": "excluded_host_clubs_not_used",
            "title": "Ekskluderte vertsklubber må ikke brukes",
            "type": "hard",
            "scope": "klubb",
            "owner": "deterministic_verifier",
            "description": "Klubber operatøren har ekskludert fra vertskap skal aldri stå som vert.",
            "configured_value": f"{len(excluded_hosts)} ekskludert" if excluded_hosts else "Ingen konfigurert",
            "status": "Oppfylt" if not used_excluded else f"{len(used_excluded)} avvik: {', '.join(used_excluded)}",
            "ok": not used_excluded,
        }
    )

    locked_dates = _parse_dates(list(manual.get("locked_dates", []) or []))
    missing_locked = sorted(d.isoformat() for d in (locked_dates - active_dates))
    rules.append(
        {
            "id": "locked_dates_preserved",
            "title": "Låste datoer må ha en planlagt turnering",
            "type": "hard",
            "scope": "dato",
            "owner": "deterministic_verifier",
            "description": "Datoer operatøren har låst inn skal alltid ha minst én turnering planlagt.",
            "configured_value": f"{len(locked_dates)} låst(e) dato(er)" if locked_dates else "Ingen konfigurert",
            "status": "Oppfylt" if not missing_locked else f"{len(missing_locked)} mangler: {', '.join(missing_locked)}",
            "ok": not missing_locked,
        }
    )

    pinned_ids = set(manual.get("pinned_tournament_ids", []) or [])
    missing_pinned = sorted(pinned_ids - active_ids)
    rules.append(
        {
            "id": "pinned_tournaments_preserved",
            "title": "Fastnaglede turneringer må være med i planen",
            "type": "hard",
            "scope": "turnering",
            "owner": "deterministic_verifier",
            "description": "Turnering-ID-er operatøren har fastnaglet skal alltid finnes i den endelige planen.",
            "configured_value": f"{len(pinned_ids)} fastnaglet" if pinned_ids else "Ingen konfigurert",
            "status": "Oppfylt" if not missing_pinned else f"{len(missing_pinned)} mangler: {', '.join(missing_pinned)}",
            "ok": not missing_pinned,
        }
    )
    return rules


def _calendar_trust_rule(plan: SeasonPlan) -> dict[str, Any]:
    """Hard rule sourced from ``Tournament.manual_booking_reason``.

    ``manual_booking_reason`` is set at plan-build time directly from the
    canonical Stage 2 club-calendar trust status for the club actually
    hosting each tournament in *this* run — it is the single source of
    truth for which hosts need manual istid verification, so this rule
    (and the page it renders on) never has to reconcile a second,
    independently-derived "missing calendar" note that can drift out of
    sync with it.
    """
    active = _active_tournaments(plan)
    needs_manual = sorted(
        {t.host_club or t.arena for t in active if t.manual_booking_reason}
    )
    status = (
        "Ingen — alle vertskap har bekreftet kalender"
        if not needs_manual
        else f"{len(needs_manual)} vertskap krever manuell verifisering: {', '.join(needs_manual)}"
    )
    return {
        "id": "calendar_trust_by_host",
        "title": "Istid kan kun godkjennes automatisk mot en bekreftet («known») kalender",
        "type": "hard",
        "scope": "klubb",
        "owner": "deterministic_placement_workflow",
        "description": (
            "En klubbs kalenderstatus er «known» (nok bevis for automatisk plassering), "
            "«unknown» (blokkert/hoppet over/feilet skraping) eller «untrusted» (skrapingen "
            "lyktes, men klubbens registeroppføring sier dataene ikke er den reelle, "
            "fullstendige kalenderen). En klubb med «untrusted» eller «unknown» status beholder "
            "sin fulle andel av vertskapsansvaret, men enhver turnering den er vertskap for må "
            "planlegges manuelt i «Må planlegges manuelt» inntil et fullstendig, autentisert "
            "kalendersøk er bevist pålitelig."
        ),
        "configured_value": "Kun «known» godkjennes automatisk",
        "status": status,
        "ok": not needs_manual,
    }


def _static_enforced_rules() -> list[dict[str, Any]]:
    """Hard rules enforced upstream by ``verify_candidate()`` / the placement
    workflow that cannot be independently re-derived from the exported
    :class:`SeasonPlan` alone (the underlying registered roster / configured
    per-age-group capacity / planning-half boundary are Stage 1/3 inputs,
    not fields carried on the plan). Still surfaced here so the canonical
    constraint list is complete, but marked as enforced-at-planning-time
    rather than independently re-checked against this export.
    """
    return [
        {
            "id": "registered_teams_only",
            "title": "Kun registrerte lag kan delta",
            "type": "hard",
            "scope": "lag",
            "owner": "deterministic_verifier",
            "description": (
                "Ethvert lag i en turnering må finnes i den registrerte laglisten for sesongen "
                "(`verify_candidate`s `unregistered_team`-sjekk). Håndheves ved planlegging, ikke "
                "uavhengig av eksportert plan siden den registrerte rosteren ikke er en del av "
                "denne eksporten."
            ),
            "configured_value": "Kun registrerte lag",
            "status": "Håndheves ved planlegging (verify_candidate)",
            "ok": True,
        },
        {
            "id": "tournament_capacity",
            "title": "Turneringsstørrelse må respektere konfigurert kapasitet",
            "type": "hard",
            "scope": "turnering",
            "owner": "deterministic_verifier",
            "description": (
                "Antall lag i en turnering skal ikke overstige kapasiteten konfigurert antall "
                "parallelle kamper for aldersgruppen tillater (`verify_candidate`s "
                "`tournament_over_capacity`-sjekk)."
            ),
            "configured_value": "≤ konfigurert kapasitet",
            "status": "Håndheves ved planlegging (verify_candidate)",
            "ok": True,
        },
        {
            "id": "christmas_half_boundary",
            "title": "Ingen stille flytting over nyttårsskillet",
            "type": "hard",
            "scope": "sesong",
            "owner": "deterministic_verifier",
            "description": (
                "Sesongen deles i to uavhengige planleggingshalvdeler (før/etter 1. januar). En "
                "turnering kan flyttes til en ny dato innenfor sin egen halvdel, men flytting over "
                "nyttårsskillet krever et eksplisitt `allow_cross_half_moves`-unntak og skjer aldri "
                "som en bieffekt av optimaliseringssøket."
            ),
            "configured_value": "1. januar",
            "status": "Håndheves ved planlegging",
            "ok": True,
        },
    ]


def _shared_host_decisions(plan: SeasonPlan) -> list[dict[str, Any]]:
    """Run-specific controller decisions (issue #305: not generic rules)."""
    decisions: list[dict[str, Any]] = []
    for decision in plan.shared_host_decisions or []:
        registration = str(decision.get("registration", "?"))
        age_group = str(decision.get("age_group", "?"))
        chosen_club = str(decision.get("chosen_club", "?"))
        decided_by = str(decision.get("decided_by", "")) or "ukjent"
        decisions.append(
            {
                "id": f"shared_host_{registration}_{age_group}",
                "title": f"Delt vertskap: {registration} ({age_group})",
                "type": "decision",
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
                "ok": True,
            }
        )
    return decisions


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

    # Hard constraints directly re-derivable from the exported plan.
    rules.append(_age_group_exact_match_rule(plan))
    rules.append(_no_double_participation_rule(plan))
    rules.append(_participation_target_exceeded_rule(plan))
    rules.append(_date_window_rule(plan))
    rules.extend(_manual_adjustment_rules(plan))
    rules.append(_calendar_trust_rule(plan))
    rules.extend(_static_enforced_rules())

    # Operational obligations.
    rules.append(_hosting_obligation_rule(plan))
    rules.append(_external_conflict_rule(plan))
    rules.append(_participation_shortfall_rule(plan))

    # Decisions/exceptions for this run.
    rules.extend(_shared_host_decisions(plan))
    return rules


def group_rules_by_type(rules: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Split a flat rules list into the four Regler-page sections."""
    groups: dict[str, list[dict[str, Any]]] = {
        "hard": [],
        "required_obligation": [],
        "soft": [],
        "decision": [],
    }
    for rule in rules:
        groups.setdefault(str(rule.get("type", "soft")), []).append(rule)
    return groups


def rules_summary_counts(rules: list[dict[str, Any]]) -> dict[str, int]:
    """Return the compact top-summary counts shown on the Regler page."""
    hard = [r for r in rules if r.get("type") == "hard"]
    obligations = [r for r in rules if r.get("type") == "required_obligation"]
    soft = [r for r in rules if r.get("type") == "soft"]
    return {
        "hard_total": len(hard),
        "hard_ok": sum(1 for r in hard if r.get("ok")),
        "obligations_unresolved": sum(1 for r in obligations if not r.get("ok")),
        "soft_warnings": sum(1 for r in soft if not r.get("ok")),
    }
