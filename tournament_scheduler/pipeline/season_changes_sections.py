"""Render one canonical change-request entry into HTML fragments.

Owns the ledger-record presentation only: status labels/badges, the overview
row and the expandable per-request detail tables. It never fetches, groups,
reclassifies or re-derives requests -- those semantics stay in
:mod:`tournament_scheduler.application.canonical_season.changes` -- so this
module cannot become a second change-tracking model. The page shell and
navigation live in :mod:`tournament_scheduler.pipeline.season_changes_view`.
"""

from __future__ import annotations

import html as _html
from typing import Any, Mapping


# Status vocabulary is deliberately a label/tone map, not a second status
# engine: the semantic status string always comes from the canonical ledger.
_STATUS_LABELS: dict[str, str] = {
    "needs_action": "Må følges opp",
    "partially_resolved": "Delvis løst",
    "active": "Aktiv",
    "resolved": "Løst",
    "superseded": "Erstattet",
    "released": "Frigitt",
    "unknown": "Ukjent",
}
_STATUS_TONES: dict[str, str] = {
    "needs_action": "attention",
    "partially_resolved": "warn",
    "active": "info",
    "resolved": "ok",
    "superseded": "muted",
    "released": "muted",
    "unknown": "muted",
}


def _text(value: Any) -> str:
    return str(value or "")


def _e(value: Any) -> str:
    return _html.escape(_text(value))


def request_label(item: Mapping[str, Any]) -> str:
    """Return the stable display id shared with the Markdown projection."""

    return _text(item.get("request_id") or item.get("bucket_id")) or "unknown"


def status_label(status: str) -> str:
    """Return the human status label for a canonical ledger status."""

    return _STATUS_LABELS.get(_text(status), _text(status) or "Ukjent")


def status_badge(status: str) -> str:
    tone = _STATUS_TONES.get(_text(status), "muted")
    return f'<span class="rules-status rules-status--{tone}">{_e(status_label(status))}</span>'


def _format_placement(value: Any) -> str:
    if not isinstance(value, Mapping):
        return _e(value) or "—"
    parts = [value.get("date"), value.get("arena"), value.get("start_time")]
    text = " · ".join(_text(part) for part in parts if part)
    return _e(text) or "—"


def _format_team(value: Any) -> str:
    if not isinstance(value, Mapping):
        return _e(value) or "—"
    club = _text(value.get("club"))
    label = _text(value.get("label"))
    age_group = _text(value.get("age_group"))
    base = " ".join(part for part in (club, label) if part)
    if age_group:
        base = f"{base} ({age_group})".strip()
    return _e(base) or "—"


def _join(values: list[Any] | None) -> str:
    return _e(", ".join(_text(value) for value in values or [] if _text(value))) or "—"


def _join_html(values: list[str] | None) -> str:
    return ", ".join(value for value in values or [] if value) or "—"


def _mutation_detail(mutation: Mapping[str, Any]) -> str:
    details = mutation.get("details") if isinstance(mutation.get("details"), Mapping) else {}
    event = _text(mutation.get("event"))
    before = details.get("before")
    after = details.get("after")
    if before or after:
        return f"{_format_placement(before)} → {_format_placement(after)}"
    removed = details.get("removed_team")
    added = details.get("added_team") or details.get("restored_team")
    if removed or added:
        return f"Ut: {_format_team(removed)} · Inn: {_format_team(added)}"
    operations = details.get("operations")
    if isinstance(operations, list) and operations:
        return _e(f"{len(operations)} operasjon(er)")
    if event == "batch_maintenance":
        return _join(details.get("scope"))
    return "—"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return ""
    head = "".join(f"<th>{_e(header)}</th>" for header in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return (
        '<div class="table-wrap"><table class="report-table">'
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def overview_rows(requests: list[Mapping[str, Any]]) -> str:
    if not requests:
        return '<tr><td colspan="8" class="empty-cell">Ingen registrerte endringsforespørsler.</td></tr>'
    rows = []
    for item in requests:
        rows.append(
            "<tr>"
            f'<td><code>{_e(request_label(item))}</code></td>'
            f"<td>{_e(item.get('created_at')) or '—'}</td>"
            f"<td>{_join(item.get('sources') or item.get('actors'))}</td>"
            f"<td>{_join(item.get('types'))}</td>"
            f"<td>{_join(item.get('affected_tournaments'))}</td>"
            f"<td>{_join(item.get('affected_teams'))}</td>"
            f"<td>{_e(item.get('result_summary')) or '—'}</td>"
            f"<td>{status_badge(item.get('status'))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _constraint_rows(item: Mapping[str, Any]) -> list[list[str]]:
    rows = []
    for constraint in item.get("constraints") or []:
        if not isinstance(constraint, Mapping):
            continue
        period = " – ".join(
            part for part in (_text(constraint.get("date_from")), _text(constraint.get("date_to"))) if part
        )
        if not period and constraint.get("min_days"):
            period = f"min {constraint.get('min_days')} dager"
        state = "innfridd" if constraint.get("satisfied") else "ikke innfridd"
        if _text(constraint.get("status")) not in {"", "active"}:
            state = _text(constraint.get("status"))
        rows.append(
            [
                f"<code>{_e(constraint.get('id'))}</code>",
                _e(constraint.get("type")),
                _e(period) or "—",
                _join_html([_format_team(team) for team in constraint.get("teams") or []]),
                _e(state),
                _e(constraint.get("note")) or "—",
            ]
        )
    return rows


def _withdrawal_rows(item: Mapping[str, Any]) -> list[list[str]]:
    rows = []
    for withdrawal in item.get("withdrawals") or []:
        if not isinstance(withdrawal, Mapping):
            continue
        rows.append(
            [
                _format_team(withdrawal.get("team")),
                _join(withdrawal.get("tournament_ids")),
                _e(withdrawal.get("scope")) or "—",
                _e(withdrawal.get("status")) or "—",
                _e(withdrawal.get("note")) or "—",
            ]
        )
    return rows


def _mutation_rows(item: Mapping[str, Any]) -> list[list[str]]:
    rows = []
    for mutation in item.get("mutations") or []:
        if not isinstance(mutation, Mapping):
            continue
        inferred = " (utledet)" if mutation.get("request_id_inferred") else ""
        rows.append(
            [
                _e(mutation.get("timestamp")) or "—",
                f"{_e(mutation.get('event'))}{inferred}",
                f"<code>{_e(mutation.get('tournament_id'))}</code>" if mutation.get("tournament_id") else "—",
                _mutation_detail(mutation),
                _e(mutation.get("note")) or "—",
            ]
        )
    return rows


def _protection_rows(item: Mapping[str, Any]) -> list[list[str]]:
    rows = []
    for protection in item.get("protections") or []:
        if not isinstance(protection, Mapping):
            continue
        rows.append(
            [
                _e(protection.get("source_event") or protection.get("kind")),
                f"<code>{_e(protection.get('tournament_id'))}</code>" if protection.get("tournament_id") else "—",
                _e(protection.get("field")) or "—",
                _e(protection.get("value")) or "—",
                _e(protection.get("status")) or "—",
            ]
        )
    return rows


def _current_state_rows(item: Mapping[str, Any]) -> list[list[str]]:
    current = item.get("current_state") if isinstance(item.get("current_state"), Mapping) else {}
    rows = []
    for tournament_id, state in current.items():
        if not isinstance(state, Mapping):
            continue
        roster = state.get("roster") if isinstance(state.get("roster"), list) else []
        rows.append(
            [
                f"<code>{_e(tournament_id)}</code>",
                _format_placement(state.get("placement")),
                _e(len(roster)),
                _join_html(
                    [
                        _e(f"{_text(team.get('club'))} {_text(team.get('label'))}".strip())
                        for team in roster
                        if isinstance(team, Mapping)
                    ]
                ),
            ]
        )
    return rows


def _section(title: str, table_html: str) -> str:
    if not table_html:
        return ""
    return f'<div class="change-block"><p class="eyebrow">{_e(title)}</p>{table_html}</div>'


def details_html(item: Mapping[str, Any]) -> str:
    sections: list[str] = []
    notes = [_text(note) for note in item.get("notes") or [] if _text(note)]
    if notes:
        note_items = "".join(f"<li>{_e(note)}</li>" for note in notes)
        sections.append(f'<div class="change-notes"><p class="eyebrow">Notater/begrunnelse</p><ul>{note_items}</ul></div>')

    sections.append(
        _section(
            "Forespurte vilkår",
            _table(["Forespørsel", "Type", "Periode", "Lag", "Tilstand", "Notat"], _constraint_rows(item)),
        )
    )
    sections.append(
        _section(
            "Tilbaketrekninger",
            _table(["Lag", "Turneringer", "Omfang", "Status", "Notat"], _withdrawal_rows(item)),
        )
    )
    sections.append(
        _section(
            "Anvendte endringer",
            _table(["Tidspunkt", "Endring", "Turnering", "Før → etter", "Notat"], _mutation_rows(item)),
        )
    )
    sections.append(
        _section(
            "Beskyttelser",
            _table(["Type", "Turnering", "Felt", "Verdi", "Status"], _protection_rows(item)),
        )
    )
    sections.append(
        _section(
            "Nåværende kanoniske tilstand",
            _table(["Turnering", "Plassering", "Lag", "Deltakere"], _current_state_rows(item)),
        )
    )

    created = _e(item.get("created_at")) or "ukjent"
    sources = _join(item.get("sources") or item.get("actors"))
    return (
        '<details class="report-section report-section--collapsible">'
        '<summary class="section-head"><div>'
        f'<p class="eyebrow">Forespørsel</p><h2><code>{_e(request_label(item))}</code></h2>'
        "</div>"
        f'<p class="section-note">{status_badge(item.get("status"))}<br>Opprettet: {created} · Kilde/aktør: {sources}</p>'
        "</summary>"
        + "".join(sections)
        + "</details>"
    )
