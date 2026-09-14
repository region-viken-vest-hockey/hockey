"""Operational-obligation rule entries for `rules_model.build_rules_model`.

Split out of `rules_model.py` (which imports and calls these) to keep that
module under the file-length guideline -- these three rules share the same
"unresolved_* manual-placement list -> rule entry" shape and no dependency
on the rest of that module beyond `SeasonPlan`.
"""

from __future__ import annotations

from typing import Any

from .models import SeasonPlan


def hosting_obligation_rule(plan: SeasonPlan) -> dict[str, Any]:
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


def tournament_placement_rule(plan: SeasonPlan) -> dict[str, Any]:
    unresolved = list(plan.unresolved_tournament_placements or [])
    if unresolved:
        names = ", ".join(
            f"{item.get('age_group', '?')} ({item.get('date', '?')})" for item in unresolved
        )
        status = f"{len(unresolved)} uløst: {names}"
    else:
        status = "Oppfylt"
    return {
        "id": "tournament_placement_shortfall",
        "title": "Vertskap skal velges blant deltakerne, ikke omvendt",
        "type": "required_obligation",
        "scope": "turnering",
        "owner": "deterministic_placement_workflow",
        "description": (
            "Vertskap/arena velges kun blant klubbene som faktisk deltar i turneringen. "
            "Når ingen av deltakernes klubber har en lovlig ledig arena-/tidsluke, blir "
            "turneringen ikke automatisk plassert hos en urelatert klubb, og deltakerlisten "
            "endres ikke for å passe en urelatert arena -- den legges i «Må planlegges "
            "manuelt» i stedet."
        ),
        "configured_value": "0 uløste plasseringer",
        "status": status,
        "ok": not unresolved,
    }


def external_conflict_rule(plan: SeasonPlan) -> dict[str, Any]:
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
