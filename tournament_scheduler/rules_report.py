"""Rules-report helper for `SeasonPlanner`."""

from __future__ import annotations

from typing import Dict, List

from tournament_scheduler.rules_report_capacity_rules import capacity_and_config_rule_entries
from tournament_scheduler.rules_report_operational_rules import operational_rule_entries
from tournament_scheduler.rule_catalog import active_catalog_references


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
                "foretrekkes, for å unngå svært tidlige eller sene starttider. Hvis den ansvarlige/opprinnelige vertsklubben er representert og fortsatt "
                "skylder vertskapsansvar, prøver planleggeren først andre lovlige datoer/tidsluker for samme klubb i stedet for å overføre ansvaret. "
                "Ellers prøver planleggeren andre deltakerklubber med ledig kapasitet på samme dato. Hvis ingen verifisert kandidat passer innenfor "
                "det avgrensede søket, registreres en manuell plassering for den ansvarlige klubben i stedet for å overføre ansvaret."
            ),
            "kategori": "Automatisk avgjørelse",
        })

    return report


_MARKDOWN_SECTION_ORDER: tuple[tuple[str, str], ...] = (
    ("Hard krav", "Policy rules"),
    ("Konfigurasjonsstandard", "Configuration and guardrails"),
    ("Mykt planleggingskrav", "Configuration and guardrails"),
    ("Automatisk avgjørelse", "Implementation rules"),
    ("Advarsel", "Warnings / diagnostics"),
    ("Anbefaling", "Warnings / diagnostics"),
)

_MARKDOWN_SECTION_INTROS: dict[str, str] = {
    "Policy rules": "| Rule | What it does | Kind |\n|---|---|---|",
    "Configuration and guardrails": "| Rule | What it does | Kind |\n|---|---|---|",
    "Implementation rules": "| Rule | What it does | Kind |\n|---|---|---|",
    "Warnings / diagnostics": "| Rule | What it does | Kind |\n|---|---|---|",
}

_PRIMARY_SOURCE_FILES: tuple[str, ...] = (
    "tournament_scheduler/rule_catalog.py",
    "tournament_scheduler/rules_report.py",
    "tournament_scheduler/rules_report_capacity_rules.py",
    "tournament_scheduler/rules_report_operational_rules.py",
    "tournament_scheduler/final_verification.py",
    "tournament_scheduler/planning_contract.py",
    "tournament_scheduler/arena_conflicts.py",
    "tournament_scheduler/participant_selection.py",
    "tournament_scheduler/warnings.py",
    "tournament_scheduler/host_assignment.py",
    "tournament_scheduler/season_planner.py",
    "tournament_scheduler/game_generation.py",
    "tournament_scheduler/models.py",
    "tournament_scheduler/season_config.py",
)


def _markdown_table_cell(value: str) -> str:
    """Return *value* escaped for a GitHub-flavoured Markdown table cell."""
    return str(value).replace("\n", " ").replace("|", "\\|")


def _render_rules_table(entries: list[Dict[str, str]]) -> list[str]:
    rows = [_MARKDOWN_SECTION_INTROS["Policy rules"]]
    rows.extend(
        "| "
        + " | ".join(
            _markdown_table_cell(entry.get(key, ""))
            for key in ("regel", "forklaring", "kategori")
        )
        + " |"
        for entry in entries
    )
    return rows


def _render_catalog_reference(entries: list[Dict[str, object]]) -> list[str]:
    """Render the canonical catalog itself as the report's semantic reference.

    The planner-derived tables elsewhere in this document are this run's
    results; this section is generated from ``rule_catalog`` so the report
    does not maintain a second, hand-written list of rule semantics.
    """
    lines = [
        "## Canonical rule catalog reference",
        "",
        "Generated from `tournament_scheduler/rule_catalog.py`. The planner-derived tables "
        "above are this run's results; this table is the canonical semantic identity "
        "(classification, meaning, canonical owner, precedence) that the planner, verifier, "
        "search, findings and reports all reference.",
        "",
        "| Rule ID | Classification | Meaning | Canonical owner | Precedence |",
        "|---|---|---|---|---|",
    ]
    for entry in entries:
        precedence_bits: list[str] = []
        if entry.get("precedes"):
            precedence_bits.append(
                "before " + ", ".join(f"`{item}`" for item in entry["precedes"])
            )
        if entry.get("depends_on"):
            precedence_bits.append(
                "depends on " + ", ".join(f"`{item}`" for item in entry["depends_on"])
            )
        lines.append(
            "| `{id}` | {classification} | {meaning} | `{owner}` | {precedence} |".format(
                id=entry.get("id", ""),
                classification=entry.get("classification", ""),
                meaning=_markdown_table_cell(str(entry.get("meaning", ""))),
                owner=entry.get("canonical_owner", ""),
                precedence="; ".join(precedence_bits) or "—",
            )
        )
    lines.append("")
    return lines


def render_rules_markdown(planner) -> str:
    """Render the committed rules-report snapshot for docs / review.

    The snapshot is generated from ``rules_report(planner)`` so regeneration
    catches drift between the planner's structured rule entries and the
    committed Markdown document.
    """
    report = rules_report(planner)
    by_section: dict[str, list[Dict[str, str]]] = {}
    category_to_section = dict(_MARKDOWN_SECTION_ORDER)
    for entry in report:
        section = category_to_section.get(entry.get("kategori", ""), "Implementation rules")
        by_section.setdefault(section, []).append(entry)

    lines: list[str] = [
        "# RVV Miniputt rules report",
        "",
        "This is a review/discussion snapshot of the current season-planning logic.",
        "It is based on the planner code, not on the marketing/docs wording, so it calls out where a rule is truly hard, soft, automatic, or only a warning.",
        "",
        "## Policy vs implementation",
        "",
    ]

    emitted_sections: set[str] = set()
    for _, section in _MARKDOWN_SECTION_ORDER:
        if section in emitted_sections:
            continue
        emitted_sections.add(section)
        entries = by_section.get(section, [])
        if not entries:
            continue
        lines.extend([f"### {section}", ""])
        lines.extend(_render_rules_table(entries))
        lines.append("")

    lines.extend(_render_catalog_reference(list(active_catalog_references())))

    lines.extend([
        "## Final verification and publication readiness",
        "",
        "The search-time planning contract keeps `ok` narrowly defined as structural validity so planners and optimizers can compare candidates without turning every unresolved operational task into a hard solver failure. Immediately before production export, the stricter final verifier additionally checks that production tournaments are not below the three-team minimum and that each tournament's game list is a complete, non-duplicated round robin with no team scheduled twice in one round.",
        "",
        "The final verifier publishes a separate readiness state:",
        "",
        "- `INVALID`: at least one hard structural/integrity violation exists.",
        "- `REVIEW_REQUIRED`: structurally valid, but unresolved hosting, calendar placement, external calendar conflict, participation shortfall, or incomplete verification remains.",
        "- `PUBLISHABLE`: hard verification passed and none of those unresolved obligations remains.",
        "",
        "This classification is evidence for the operator/publication decision; it does not weaken the existing explicit human confirmation required to publish.",
        "",
        "## Primary source files",
        "",
    ])
    lines.extend(f"- `{path}`" for path in _PRIMARY_SOURCE_FILES)
    return "\n".join(lines) + "\n"
