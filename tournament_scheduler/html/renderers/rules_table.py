"""Canonical Rules table HTML renderer (issue #277).

Renders the planner-independent rule entries produced by
:func:`tournament_scheduler.rules_model.build_rules_model` as a first-class
report table, distinguishing hard/required/soft/advisory/default rules.
"""

from __future__ import annotations

import html as _html
from typing import Any

_TYPE_LABELS: dict[str, str] = {
    "hard": "Hard krav",
    "required_obligation": "Påkrevd forpliktelse",
    "soft": "Myk preferanse",
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


def render_rules_table_html(rules: list[dict[str, Any]]) -> str:
    """Render the canonical rules list as a table.

    Returns an empty string when *rules* is empty, so callers can omit the
    whole section rather than showing an empty table.
    """
    if not rules:
        return ""

    def _status_class(status: str) -> str:
        lowered = status.lower()
        if lowered.startswith("fail") or "uløst" in lowered or "avvik" in lowered:
            return "rules-status--attention"
        if lowered.startswith("warn"):
            return "rules-status--warn"
        return "rules-status--ok"

    rows = []
    for rule in rules:
        rule_type = str(rule.get("type", "advisory"))
        type_label = _TYPE_LABELS.get(rule_type, rule_type)
        owner = str(rule.get("owner", ""))
        owner_label = _OWNER_LABELS.get(owner, owner)
        status = str(rule.get("status", ""))
        configured_value = rule.get("configured_value")
        configured_str = "" if configured_value is None else str(configured_value)
        rows.append(
            "<tr>"
            f'<td><strong>{_html.escape(str(rule.get("title", "")))}</strong>'
            f'<div class="rules-description">{_html.escape(str(rule.get("description", "")))}</div></td>'
            f'<td><span class="rules-type-badge rules-type-badge--{_html.escape(rule_type)}">{_html.escape(type_label)}</span></td>'
            f'<td>{_html.escape(str(rule.get("scope", "")))}</td>'
            f'<td>{_html.escape(owner_label)}</td>'
            f'<td>{_html.escape(configured_str)}</td>'
            f'<td><span class="rules-status {_status_class(status)}">{_html.escape(status)}</span></td>'
            "</tr>"
        )

    return (
        '<div class="table-wrap">'
        '<table class="report-table rules-table">'
        "<thead><tr>"
        "<th>Regel</th><th>Type</th><th>Omfang</th><th>Eier / håndhevelse</th>"
        "<th>Konfigurert verdi / mål</th><th>Status</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table></div>"
    )
