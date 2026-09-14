"""Rules-report helper for `SeasonPlanner`."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from tournament_scheduler.rules_report_capacity_rules import capacity_and_config_rule_entries
from tournament_scheduler.rules_report_operational_rules import operational_rule_entries


def rules_report(planner) -> List[Dict[str, str]]:
    """Return a structured report of every constraint and automatic decision."""
    report: List[Dict[str, str]] = []

    report.extend(capacity_and_config_rule_entries(planner))
    report.extend(operational_rule_entries(planner))

    if planner.participation_targets_by_age_group:
        rows = ", ".join(
            f"{ag}: {targets.get('before_christmas')} før jul / {targets.get('after_christmas')} etter jul"
            for ag, targets in sorted(planner.participation_targets_by_age_group.items())
        )
        overridden = sorted(
            t.label for t in planner.roster.teams if t.target_tournament_count is not None
        )
        override_note = (
            f" Unntak med eget lagmål (sesongtotal, overstyrer aldersgruppens mål): {', '.join(overridden)}."
            if overridden
            else ""
        )
        report.append({
            "regel": "Deltakelsesmål per aldersgruppe (før/etter jul)",
            "forklaring": (
                f"Hver aktiv aldersgruppe har et konfigurert mål for antall turneringsdeltakelser per lag, "
                f"uavhengig før og etter nyttår: {rows}. Dette er det autoritative målet per lag og "
                "halvsesong — planleggeren, CP-SAT-optimaliseringen og verifikatoren bruker det direkte, "
                "ikke som en vekt for å fordele et sesongtotalt turneringsantall."
                f"{override_note}"
            ),
            "kategori": "Automatisk avgjørelse",
        })
    else:
        inferred_target = planner.target_tournament_count
        if inferred_target is None:
            inferred_target = max(1, len(planner.roster.teams))
        all_same_target = all(
            t.target_tournament_count is None or t.target_tournament_count == inferred_target
            for t in planner.roster.teams
        )
        if all_same_target:
            target_desc = f"cirka {inferred_target} turneringsdeltakelser per lag"
            target_detail = f"Hver aldersgruppe planlegges mot et mykt mål på rundt {inferred_target} turneringsdeltakelser per lag."
        else:
            targets = {t.target_tournament_count or inferred_target for t in planner.roster.teams}
            target_range = ", ".join(sorted(str(t) for t in targets))
            target_desc = f"{min(targets)}–{max(targets)} turneringsdeltakelser per lag (varierer per lag)"
            target_detail = (
                f"Lag planlegges mot individuelle mål for turneringsdeltakelser: {target_range}. "
                "Lag uten eget mål bruker et inferred sesongmål. "
            )
        report.append({
            "regel": f"Mykt mål: {target_desc}",
            "forklaring": (
                f"{target_detail} Tallet er en ønsket sesongbelastning — planleggeren vil heller lage færre, bedre turneringer "
                "enn å presse inn ekstra bare for å nå målet. Dersom en aldersgruppe har for få lag eller for få ledige helger "
                "til å oppfylle målet, justeres det ned."
            ),
            "kategori": "Automatisk avgjørelse",
        })

    if planner.events_by_club:
        report.append({
            "regel": "Tidspunkt på dagen velges ut fra vertsklubbens egen hallkalender",
            "forklaring": (
                "For hver turnering beregnes hvor lang tid hele turneringen tar (rundelengde × antall runder pluss buffer), og planleggeren "
                "ser etter en sammenhengende ledig luke av denne lengden i vertsklubbens egen hallkalender og i planens egne reservasjoner. Tidspunkt nærmest 11:00 "
                "foretrekkes, for å unngå svært tidlige eller sene starttider. Hvis den opprinnelige vertsklubben ikke har en passende "
                "ledig luke, prøver planleggeren andre klubber med ledig kapasitet på samme dato; hvis ingen kandidat passer, registreres en hard konflikt."
            ),
            "kategori": "Automatisk avgjørelse",
        })

    return report


def render_rules_markdown(planner) -> str:
    """Render the committed rules-report snapshot for docs / review."""
    doc_path = Path(__file__).resolve().parents[1] / "docs" / "rvv-miniputt-rules-report.md"
    return doc_path.read_text(encoding="utf-8")
