"""Canonical Rules ("Regler") HTML renderer (issue #277, #305).

Renders the planner-independent rule entries produced by
:func:`tournament_scheduler.rules_model.build_rules_model`, split into the
four Regler-page sections (hard constraints, operational obligations, soft
quality goals, decisions/exceptions for this run) plus a compact top
summary.
"""

from __future__ import annotations

import html as _html
from typing import Any

from ...rules_model import group_rules_by_type, rules_summary_counts

_TYPE_LABELS: dict[str, str] = {
    "hard": "Hard krav",
    "required_obligation": "Påkrevd forpliktelse",
    "soft": "Myk preferanse",
    "decision": "Beslutning/unntak",
    "advisory": "Rådgivende",
    "default": "Standardverdi",
}

_OWNER_LABELS: dict[str, str] = {
    "deterministic_verifier": "Deterministisk verifikator",
    "deterministic_measurement": "Deterministisk måling",
    "deterministic_placement_workflow": "Deterministisk plasseringsflyt",
    "llm_controller": "LLM/kontroller",
    "operator": "Operatør",
}

_SECTION_DEFS: list[tuple[str, str, str]] = [
    ("hard", "Hard krav", "Må være sant for at planen skal være gyldig."),
    ("required_obligation", "Påkrevde forpliktelser", "Kan stå uløst, men krever manuell oppfølging."),
    ("soft", "Myke kvalitetsmål", "Optimeres, men avgjør ikke om planen er gyldig."),
    ("decision", "Beslutninger / unntak for denne kjøringen", "Kontekstuelle avgjørelser, ikke generelle regler."),
]


def _status_class(status: str, ok: bool | None) -> str:
    if ok is False:
        return "rules-status--attention"
    lowered = status.lower()
    if lowered.startswith("fail") or "uløst" in lowered or "avvik" in lowered or "mangler" in lowered:
        return "rules-status--attention"
    if lowered.startswith("warn"):
        return "rules-status--warn"
    return "rules-status--ok"


def _render_detail_rows(detail_rows: dict[str, Any] | None) -> str:
    if not detail_rows or not detail_rows.get("rows"):
        return ""
    label = _html.escape(str(detail_rows.get("label", "Vis detaljer")))
    kind = detail_rows.get("kind")
    rows = list(detail_rows["rows"])

    if kind == "hosting":
        # issue #305: prefer meaningful deviations first rather than always
        # dumping the full club x age-group matrix.
        rows = sorted(rows, key=lambda row: float(row.get("deviation", 0) or 0), reverse=True)
        shown = [row for row in rows if float(row.get("deviation", 0) or 0) > 0][:20] or rows[:10]
        body = "".join(
            "<tr>"
            f"<td>{_html.escape(str(row.get('age_group', '')))}</td>"
            f"<td>{_html.escape(str(row.get('club', '')))}</td>"
            f"<td class=\"numeric-cell\">{row.get('actual', 0)}</td>"
            f"<td class=\"numeric-cell\">{row.get('expected', 0)}</td>"
            f"<td class=\"numeric-cell\">{row.get('deviation', 0)}</td>"
            "</tr>"
            for row in shown
        )
        table = (
            '<div class="table-wrap"><table class="report-table"><thead><tr>'
            "<th>Aldersgruppe</th><th>Klubb</th><th>Faktisk</th><th>Forventet</th><th>Avvik</th>"
            f"</tr></thead><tbody>{body}</tbody></table></div>"
        )
        if len(rows) > len(shown):
            table += f'<p class="rules-detail-note">Viser {len(shown)} av {len(rows)} rader (størst avvik først).</p>'
        return f"<details class=\"rules-detail\"><summary>{label}</summary>{table}</details>"

    if kind == "travel":
        rows = sorted(rows, key=lambda row: float(row.get("km", 0) or 0), reverse=True)
        shown = rows[:30]
        body = "".join(
            "<tr>"
            f"<td>{_html.escape(str(row.get('team', '')))}</td>"
            f"<td class=\"numeric-cell\">{row.get('km', 0)}</td>"
            "</tr>"
            for row in shown
        )
        table = (
            '<div class="table-wrap"><table class="report-table"><thead><tr>'
            "<th>Lag</th><th>Anslått reise (km)</th>"
            f"</tr></thead><tbody>{body}</tbody></table></div>"
        )
        if len(rows) > len(shown):
            table += f'<p class="rules-detail-note">Viser {len(shown)} av {len(rows)} lag.</p>'
        return f"<details class=\"rules-detail\"><summary>{label}</summary>{table}</details>"

    return ""


def _render_rule_row(rule: dict[str, Any]) -> str:
    rule_type = str(rule.get("type", "advisory"))
    type_label = _TYPE_LABELS.get(rule_type, rule_type)
    owner = str(rule.get("owner", ""))
    owner_label = _OWNER_LABELS.get(owner, owner)
    status = str(rule.get("status", ""))
    configured_value = rule.get("configured_value")
    configured_str = "" if configured_value is None else str(configured_value)
    detail_html = _render_detail_rows(rule.get("detail_rows"))
    return (
        "<tr>"
        f'<td><strong>{_html.escape(str(rule.get("title", "")))}</strong>'
        f'<div class="rules-description">{_html.escape(str(rule.get("description", "")))}</div>'
        f"{detail_html}</td>"
        f'<td><span class="rules-type-badge rules-type-badge--{_html.escape(rule_type)}">{_html.escape(type_label)}</span></td>'
        f'<td>{_html.escape(str(rule.get("scope", "")))}</td>'
        f'<td>{_html.escape(owner_label)}</td>'
        f'<td>{_html.escape(configured_str)}</td>'
        f'<td><span class="rules-status {_status_class(status, rule.get("ok"))}">{_html.escape(status)}</span></td>'
        "</tr>"
    )


def render_rules_table_html(rules: list[dict[str, Any]]) -> str:
    """Render the canonical rules list as a single flat table (legacy shape).

    Kept for callers that want one undivided table. The Regler page itself
    uses :func:`render_rules_sections_html` for the grouped view.
    """
    if not rules:
        return ""
    rows = "".join(_render_rule_row(rule) for rule in rules)
    return (
        '<div class="table-wrap">'
        '<table class="report-table rules-table">'
        "<thead><tr>"
        "<th>Regel</th><th>Type</th><th>Omfang</th><th>Eier / håndhevelse</th>"
        "<th>Konfigurert verdi / mål</th><th>Status</th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table></div>"
    )


def render_rules_summary_html(rules: list[dict[str, Any]]) -> str:
    """Render the compact top summary (hard/obligation/soft counts)."""
    counts = rules_summary_counts(rules)
    hard_ok = counts["hard_ok"] == counts["hard_total"]
    lines = [
        (
            "ok" if hard_ok else "attention",
            "✓" if hard_ok else "⚠",
            f"Harde krav: {counts['hard_ok']}/{counts['hard_total']} oppfylt",
        ),
        (
            "ok" if not counts["obligations_unresolved"] else "attention",
            "✓" if not counts["obligations_unresolved"] else "⚠",
            f"Uløste forpliktelser: {counts['obligations_unresolved']}"
            + ("" if not counts["obligations_unresolved"] else " → Må planlegges manuelt"),
        ),
        (
            "ok" if not counts["soft_warnings"] else "warn",
            "✓" if not counts["soft_warnings"] else "⚠",
            f"Myke kvalitetsvarsler: {counts['soft_warnings']}",
        ),
    ]
    items = "".join(
        f'<li class="rules-summary-line rules-summary-line--{status}"><span class="rules-summary-icon">{icon}</span> {_html.escape(text)}</li>'
        for status, icon, text in lines
    )
    return f'<ul class="rules-summary">{items}</ul>'


def render_rules_sections_html(rules: list[dict[str, Any]]) -> str:
    """Render the grouped Regler view: summary + one table per rule type."""
    if not rules:
        return '<p class="empty-cell">Ingen regler å vise.</p>'

    groups = group_rules_by_type(rules)
    summary_html = render_rules_summary_html(rules)

    sections: list[str] = [summary_html]
    for rule_type, title, note in _SECTION_DEFS:
        group = groups.get(rule_type, [])
        if not group:
            continue
        rows = "".join(_render_rule_row(rule) for rule in group)
        table = (
            '<div class="table-wrap">'
            '<table class="report-table rules-table">'
            "<thead><tr>"
            "<th>Regel</th><th>Type</th><th>Omfang</th><th>Eier / håndhevelse</th>"
            "<th>Konfigurert verdi / mål</th><th>Status</th>"
            "</tr></thead>"
            f"<tbody>{rows}</tbody>"
            "</table></div>"
        )
        sections.append(
            '<section class="rules-subsection">'
            f"<h3>{_html.escape(title)}</h3>"
            f'<p class="section-note">{_html.escape(note)}</p>'
            f"{table}"
            "</section>"
        )
    return "".join(sections)
