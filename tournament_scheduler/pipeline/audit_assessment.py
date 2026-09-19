"""Deterministic operator projection of the submitted harness assessment.

The submitted structured ``operator_assessment`` (see
:mod:`tournament_scheduler.pipeline.audit_result`) is the single source of
truth for the harness verdict shown to the operator. This module renders that
same fingerprint-bound assessment into the Stage 4 ``season_plan.html``
artifact.

It never authors a new conclusion: it only projects the persisted assessment
plus the deterministic audit identity/status, so the terminal summary, the
exported ``semantic_audit.json`` and the HTML remain three renderings of one
conclusion. The rendered block is marked with stable comment markers so
audit materialization can replace exactly that section without touching any
other Stage 4 content.
"""

from __future__ import annotations

from html import escape as _escape
from pathlib import Path
from typing import Any

ASSESSMENT_START_MARKER = "<!-- HARNESS_ASSESSMENT_START -->"
ASSESSMENT_END_MARKER = "<!-- HARNESS_ASSESSMENT_END -->"

_STATUS_PILL = {
    "PASS": ("report-status-pill--pass", "REVISJON: PASS"),
    "REVIEW_REQUIRED": ("report-status-pill--warn", "REVISJON: REVIEW_REQUIRED"),
    "FAIL": ("report-status-pill--fail", "REVISJON: FAIL"),
    "INCOMPLETE": ("report-status-pill--fail", "REVISJON: UFULLSTENDIG"),
}
_TONE_BY_STATUS = {
    "PASS": "report-section--judgment-strong",
    "REVIEW_REQUIRED": "report-section--judgment-mixed",
    "FAIL": "report-section--judgment-rough",
    "INCOMPLETE": "report-section--judgment-rough",
}
_SEVERITY_LABEL = {
    "info": "INFO",
    "minor": "MINDRE",
    "major": "STØRRE",
    "critical": "KRITISK",
}

_DEFAULT_SUMMARY = {
    "PASS": "Ingen materielle avvik funnet i den valgte planen.",
    "REVIEW_REQUIRED": "Planen er brukbar, men krever operatørgjennomgang før publisering.",
    "FAIL": "Revisjonen fant materielle feil i den valgte planen.",
    "INCOMPLETE": "Revisjonen kunne ikke etablere en fullstendig vurdering.",
}


def _text(value: Any) -> str:
    return _escape(str(value if value is not None else ""), quote=True)


def _assessment(artifact: dict[str, Any]) -> dict[str, Any]:
    assessment = artifact.get("operator_assessment")
    return assessment if isinstance(assessment, dict) else {}


def _render_tradeoffs(assessment: dict[str, Any]) -> str:
    entries = [entry for entry in assessment.get("key_tradeoffs") or [] if isinstance(entry, dict)]
    if not entries:
        return ""
    items = []
    for entry in entries:
        severity = str(entry.get("severity") or "info").lower()
        severity_label = _SEVERITY_LABEL.get(severity, severity.upper())
        items.append(
            '<article class="harness-assessment-item">'
            f'<h3>{_text(entry.get("title"))}'
            f'<span class="harness-assessment-severity harness-assessment-severity--{_text(severity)}">'
            f"{_text(severity_label)}</span></h3>"
            f'<p>{_text(entry.get("summary"))}</p>'
            "</article>"
        )
    return (
        '<div class="harness-assessment-block">'
        "<h3>Aksepterte avveininger</h3>"
        f'<div class="harness-assessment-list">{"".join(items)}</div>'
        "</div>"
    )


def _render_actions(assessment: dict[str, Any]) -> str:
    entries = [entry for entry in assessment.get("remaining_actions") or [] if isinstance(entry, dict)]
    if not entries:
        return ""
    items = []
    for entry in entries:
        category = str(entry.get("category") or "").strip()
        badge = (
            f'<span class="harness-assessment-category">{_text(category)}</span>'
            if category
            else ""
        )
        items.append(
            '<article class="harness-assessment-item">'
            f'<h3>{_text(entry.get("title"))}{badge}</h3>'
            f'<p>{_text(entry.get("summary"))}</p>'
            "</article>"
        )
    return (
        '<div class="harness-assessment-block">'
        "<h3>Gjenstående handlinger</h3>"
        f'<div class="harness-assessment-list">{"".join(items)}</div>'
        "</div>"
    )


def _render_limitations(assessment: dict[str, Any]) -> str:
    entries = [entry for entry in assessment.get("limitations") or [] if isinstance(entry, str) and entry.strip()]
    if not entries:
        return ""
    items = "".join(f"<li>{_text(entry)}</li>" for entry in entries)
    return (
        '<div class="harness-assessment-block">'
        "<h3>Forbehold / usikkerhet</h3>"
        f'<ul class="harness-assessment-limitations">{items}</ul>'
        "</div>"
    )


def render_harness_assessment_html(
    artifact: dict[str, Any],
    *,
    manual_schedule_available: bool = False,
) -> str:
    """Render the exact assessment block for one fingerprint-bound audit artifact."""
    status = str(artifact.get("status") or "INCOMPLETE")
    pill_class, status_label = _STATUS_PILL.get(status, ("report-status-pill--warn", f"REVISJON: {status}"))
    tone_class = _TONE_BY_STATUS.get(status, "report-section--judgment-mixed")
    assessment = _assessment(artifact)
    summary = str(assessment.get("operator_summary") or _DEFAULT_SUMMARY.get(status, "")).strip()

    fingerprint = str(artifact.get("export_fingerprint") or "")
    audit_id = str(artifact.get("audit_id") or "ukjent")
    run_id = str(artifact.get("run_id") or "ukjent")
    generated_at = str(artifact.get("generated_at") or "")

    manual_link = ""
    if manual_schedule_available:
        manual_link = (
            '<p class="harness-assessment-manual">'
            '<a href="manual_schedule.html">Se manuell istidsplanlegging &rarr;</a>'
            "</p>"
        )

    meta_parts = [
        f"Revisjon {_text(audit_id)}",
        f"kjøring {_text(run_id)}",
    ]
    if generated_at:
        meta_parts.append(_text(generated_at))
    if fingerprint:
        meta_parts.append(f"eksport {_text(fingerprint[:12])}")

    return (
        f"{ASSESSMENT_START_MARKER}\n"
        '<div class="report-overview" id="harnessAssessmentOverview">\n'
        f'  <section class="report-section report-section--judgment {tone_class}" id="harnessAssessment"'
        f' data-export-fingerprint="{_text(fingerprint)}" data-audit-status="{_text(status)}">\n'
        '    <div class="section-head">\n'
        "      <div>\n"
        '        <p class="eyebrow">Planleggingsassistent</p>\n'
        "        <h2>Vurdering fra planleggingsassistent</h2>\n"
        "      </div>\n"
        f'      <span class="report-status-pill {pill_class}">{_text(status_label)}</span>\n'
        "    </div>\n"
        f'    <p class="harness-assessment-summary">{_text(summary)}</p>\n'
        f"    {_render_tradeoffs(assessment)}\n"
        f"    {_render_actions(assessment)}\n"
        f"    {_render_limitations(assessment)}\n"
        f"    {manual_link}\n"
        '    <p class="harness-assessment-meta">'
        + " · ".join(meta_parts)
        + "</p>\n"
        "  </section>\n"
        "</div>\n"
        f"{ASSESSMENT_END_MARKER}"
    )


def apply_harness_assessment(
    artifact: dict[str, Any],
    *,
    html_path: str | Path,
    manual_schedule_available: bool = False,
) -> bool:
    """Replace only the marked assessment section of *html_path* in place.

    Returns ``True`` when the section was found and rewritten, ``False`` when
    the file or the markers are absent (an older export without the stable
    placeholder) — never raises for a missing optional artifact.
    """
    path = Path(html_path)
    if not path.exists():
        return False
    try:
        html = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if ASSESSMENT_START_MARKER not in html or ASSESSMENT_END_MARKER not in html:
        return False
    start = html.index(ASSESSMENT_START_MARKER)
    end = html.index(ASSESSMENT_END_MARKER, start) + len(ASSESSMENT_END_MARKER)
    rendered = render_harness_assessment_html(
        artifact, manual_schedule_available=manual_schedule_available
    )
    updated = html[:start] + rendered + html[end:]
    try:
        path.write_text(updated, encoding="utf-8")
    except OSError:
        return False
    return True
