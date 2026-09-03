"""Fairness-gate and fairness-adjustment HTML renderers.

Extracted from ``HtmlExporter._fairness_gate_html`` and
``HtmlExporter._fairness_adjustments_html``.
"""

from __future__ import annotations

import html as _html
from typing import Any

from tournament_scheduler.fairness_model import SeasonFairnessModel


def render_fairness_gate_html(fairness_gate: dict[str, Any] | None) -> str:
    """Render the fairness gate summary and metric cards.

    This is a standalone version of the former
    ``HtmlExporter._fairness_gate_html`` static method.
    """
    if not fairness_gate or not isinstance(fairness_gate, dict):
        return ""

    metrics = fairness_gate.get("metrics", [])
    notes = fairness_gate.get("notes", [])
    if not metrics and not notes:
        return ""

    status = str(fairness_gate.get("status", "pass")).lower()
    score = int(fairness_gate.get("score", 0) or 0)
    status_labels = {"pass": "PASS", "warn": "VARSEL", "fail": "FEIL"}
    status_label = status_labels.get(status, "PASS")

    note_blocks = []
    for note in (notes if isinstance(notes, list) else []):
        if not isinstance(note, dict):
            continue
        if note.get("key") != "missing_calendar_clubs":
            continue
        manual = note.get("manual_calendar_clubs") or []
        excluded = [] if manual else (note.get("excluded_clubs") or [])
        manual_text = ", ".join(_html.escape(str(club)) for club in manual)
        excluded_text = ", ".join(_html.escape(str(club)) for club in excluded)
        extra = ""
        if manual_text:
            extra = f' <span class="fairness-gate-note-extra">Manuell istid: {manual_text}</span>'
        elif excluded_text:
            extra = f' <span class="fairness-gate-note-extra">Utelatt: {excluded_text}</span>'
        note_blocks.append(
            '<div class="fairness-gate-note">'
            f'<strong>{_html.escape(str(note.get("label", "Informasjon")))}</strong>: '
            f'{_html.escape(str(note.get("detail", "")))}'
            + extra
            + '</div>'
        )

    metric_cards = []
    breakdown_blocks = []
    for metric in metrics:
        metric_status = str(metric.get("status", "pass")).lower()
        metric_label = _html.escape(str(metric.get("label", "")))
        value = metric.get("value", "")
        threshold = metric.get("threshold", "")
        unit = str(metric.get("unit", ""))
        if unit and value != "":
            value = f"{value} {unit}"
        if unit and threshold != "":
            threshold = f"{threshold} {unit}"
        breakdown_rows = metric.get("age_group_breakdown", [])
        if isinstance(breakdown_rows, list) and breakdown_rows:
            row_html = "".join(
                "<tr>"
                f"<td>{_html.escape(str(row.get('age_group', '')))}</td>"
                f"<td>{_html.escape(str(row.get('club', '')))}</td>"
                f"<td class=\"numeric-cell\">{_html.escape(str(row.get('actual', '')))}</td>"
                f"<td class=\"numeric-cell\">{float(row.get('expected', 0.0)):.1f}</td>"
                "</tr>"
                for row in breakdown_rows[:12]
                if isinstance(row, dict)
            )
            breakdown_blocks.append(
                '<div class="fairness-breakdown-row">'
                '<div class="fairness-breakdown-label">Per aldersgruppe og klubb: faktisk vs forventet hjemmeturneringer</div>'
                '<div class="fairness-breakdown-scroll">'
                '<table class="fairness-breakdown-table"><thead><tr>'
                '<th>Aldersgruppe</th><th>Klubb</th><th>Faktisk</th><th>Forventet</th>'
                f'</tr></thead><tbody>{row_html}</tbody></table>'
                '</div>'
                '</div>'
            )
        metric_cards.append(
            f"<div class=\"fairness-metric fairness-metric--{metric_status}\">"
            "<div class=\"fairness-metric-head\">"
            f"<span class=\"fairness-metric-label\">{metric_label}</span>"
            f"<span class=\"fairness-metric-status fairness-metric-status--{metric_status}\">{status_labels.get(metric_status, metric_status.upper())}</span>"
            "</div>"
            f"<div class=\"fairness-metric-value\"><strong>{_html.escape(str(value))}</strong> \u00b7 terskel {_html.escape(str(threshold))}</div>"
            f"<div class=\"fairness-metric-score\">Score {int(metric.get('score', 0) or 0)}%</div>"
            f"<div class=\"fairness-metric-detail\">{_html.escape(str(metric.get('detail', '')))}</div>"
            "</div>"
        )

    return (
        '<div class="fairness-gate-panel">'
        '<div class="fairness-gate-head">'
        '<div>'
        '<div class="metrics-group-label">Ser planen jevn ut?</div>'
        f'<div class="metrics-group-value"><strong>{score}%</strong> \u00b7 {status_label}</div>'
        '</div>'
        f'<span class="fairness-gate-status fairness-gate-status--{status}">{status_label}</span>'
        '</div>'
        f'{"".join(note_blocks)}'
        f'<div class="fairness-gate-grid">{"".join(metric_cards)}</div>'
        f'{"".join(breakdown_blocks)}'
        '</div>'
    )

def render_fairness_adjustments_html(plan: object) -> str:
    """Render the fairness adjustment overview table.

    This is a standalone version of the former
    ``HtmlExporter._fairness_adjustments_html`` static method.
    """
    rows = SeasonFairnessModel().adjustment_rows_for_plan(plan)
    if not rows:
        return ""

    total_abs = sum(abs(float(row.get("adjustment", 0.0))) for row in rows)
    avg_abs = total_abs / len(rows)
    max_row = rows[0]
    under_count = sum(1 for row in rows if str(row.get("status", "")) == "under")
    over_count = sum(1 for row in rows if str(row.get("status", "")) == "over")

    def fmt(value: float) -> str:
        return f"{value:+.1f}".replace(".", ",")

    summary = (
        '<div class="fairness-adjustment-summary">'
        f'<div class="metrics-group"><span class="metrics-group-label">Lag med positiv rettferdighetsjustering</span><span class="metrics-group-value"><strong>{under_count}</strong></span></div>'
        f'<div class="metrics-group"><span class="metrics-group-label">Lag over m\u00e5l</span><span class="metrics-group-value"><strong>{over_count}</strong></span></div>'
        f'<div class="metrics-group"><span class="metrics-group-label">Snitt absolutt avvik</span><span class="metrics-group-value"><strong>{fmt(avg_abs)}</strong></span></div>'
        f'<div class="metrics-group"><span class="metrics-group-label">St\u00f8rste avvik</span><span class="metrics-group-value"><strong>{_html.escape(str(max_row["label"]))}</strong> {fmt(abs(float(max_row["adjustment"])))}</span></div>'
        '</div>'
    )

    status_labels = {"under": "UNDER M\u00c5L", "over": "OVER M\u00c5L", "on_target": "P\u00c5 M\u00c5L"}
    table_rows = []
    has_tournament_targets = any(
        row.get("target_tournaments") is not None for row in rows
    )
    for row in rows:
        status = str(row.get("status", ""))
        adj = float(row.get("adjustment", 0.0))
        target_tt = row.get("target_tournaments")
        actual_tt = row.get("actual_tournaments", 0)
        tt_cell = f'<td style="text-align:right">{actual_tt}</td>'
        if target_tt is not None:
            tt_cell = f'<td style="text-align:right">{actual_tt}/{target_tt}</td>'
        if adj > 0.5:
            adjustment_note = "Mangler flere kamper"
        elif adj < -0.5:
            adjustment_note = "For mange kamper"
        else:
            adjustment_note = "På mål"
        table_rows.append(
            '<tr class="fairness-adjustment-row fairness-adjustment-row--' + _html.escape(status) + '">' 
            f'<td>{_html.escape(str(row.get("label", "")))}</td>'
            f'<td>{_html.escape(str(row.get("club", "")))}</td>'
            f'<td>{_html.escape(str(row.get("age_group", "")))}</td>'
            f'<td style="text-align:right">{int(row.get("actual", 0))}</td>'
            f'<td style="text-align:right">{fmt(float(row.get("target", 0.0)))}</td>'
            f'<td class="fairness-adjustment-adjustment fairness-adjustment-adjustment--{_html.escape(status)}" style="text-align:right">{fmt(adj)}</td>'
            f'<td>{status_labels.get(status, status.upper())}</td>'
            f'<td>{adjustment_note}</td>'
            f'{tt_cell}'
            '</tr>'
        )

    tt_header = '<th>Deltakelser</th>'
    if has_tournament_targets:
        tt_header = '<th>Deltakelser (faktisk/m\u00e5l)</th>'

    return (
        '<section class="fairness-adjustment-panel">'
        '<div class="fairness-adjustment-head">'
        '<div>'
        '<div class="metrics-group-label">Rettferdighetsjusteringer</div>'
        '<div class="metrics-group-value">Forskjell mellom faktisk kampantall og rettferdighetsm\u00e5l</div>'
        '</div>'
        f'<span class="fairness-gate-status fairness-gate-status--warn">{len(rows)} lag</span>'
        '</div>'
        f'{summary}'
        '<table class="fairness-adjustment-table">'
        '<thead><tr>'
        f'<th>Lag</th><th>Klubb</th><th>Aldersgruppe</th><th>Kamper (faktisk)</th><th>Kamper (m\u00e5l)</th><th>Justering</th><th>Status</th><th>Kommentar</th>{tt_header}'
        '</tr></thead><tbody>'
        f'{"".join(table_rows)}'
        '</tbody></table>'
        '</section>'
    )
