"""Group manual-schedule findings into operator work items.

The Stage 4 manual view used to render one table row per evidence record, so
one underlying tournament/hosting obligation could appear several times (no
participant host slot, calendar source unavailable, external calendar
conflict, arena collision). That made the headline count an evidence count
instead of an operator-work count.

This module owns the projection from flat finding dicts to one *operator work
item* per underlying tournament/obligation, plus the read-only rendering of
the canonical conflict-aware candidate-weekend evidence. It performs no
scheduling, generates no repair candidates and never changes selected schedule
state: missing evidence stays visibly missing.
"""

from __future__ import annotations

import html as _html
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from ..html.data_computation import fmt_date

CATEGORY_LABELS = {
    "arena_collision": "Arena-/tidskollisjon",
    "manual_calendar_verification": "Kalender ikke verifisert",
    "manual_hosting_obligation": "Manglende vertskap",
    "manual_external_conflict": "Ekstern kalenderkonflikt",
    "manual_tournament_placement": "Ingen ledig vertsarena/-tid",
}

# Categories where the placement itself is not established: the arena/time is
# a proposal and must never be rendered as a confirmed booking.
UNCONFIRMED_CATEGORIES = frozenset({"manual_tournament_placement", "manual_calendar_verification"})

_AVAILABILITY_LABELS = {
    "free": "ledig istid",
    "movable_busy": "flyttbar/mulig istid",
    "unknown": "kalender ikke verifisert",
}

_REJECTION_LABELS = {
    "fixed_busy": "fast hallbooking overlapper",
    "team_already_plays": "lag spiller allerede",
    "replacement_roster_still_conflicts": "erstatterlag spiller allerede",
}


def work_item_key(entry: Mapping[str, Any]) -> tuple:
    """Stable identity for the underlying intervention, not the finding."""
    tournament_id = str(entry.get("tournament_id") or "").strip()
    if tournament_id:
        return ("tournament", tournament_id)
    club = str(entry.get("host_club") or "").strip()
    age_group = str(entry.get("age_group") or "").strip()
    if club or age_group:
        return ("obligation", club, age_group)
    return (
        "finding",
        str(entry.get("category") or ""),
        str(entry.get("date") or ""),
    )


def build_work_items(
    entries: Sequence[Mapping[str, Any]] | None,
    candidate_weekends_by_tournament: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Collapse flat manual findings into one work item per intervention."""
    candidate_weekends_by_tournament = candidate_weekends_by_tournament or {}
    grouped: dict[tuple, list[Mapping[str, Any]]] = {}
    for entry in entries or []:
        if not isinstance(entry, Mapping):
            continue
        grouped.setdefault(work_item_key(entry), []).append(entry)

    work_items: list[dict[str, Any]] = []
    for key, findings in grouped.items():
        first = findings[0]
        categories = sorted({str(item.get("category") or "") for item in findings if item.get("category")})
        tournament_id = str(first.get("tournament_id") or "").strip()
        work_items.append(
            {
                "key": key,
                "tournament_id": tournament_id,
                "date": _first_value(findings, "date"),
                "age_group": _first_value(findings, "age_group"),
                "host_club": _first_value(findings, "host_club"),
                "arena": _first_value(findings, "arena"),
                "interval": _first_value(findings, "interval"),
                "conflicting": _conflict_labels(findings),
                "categories": categories,
                "unconfirmed": any(category in UNCONFIRMED_CATEGORIES for category in categories),
                "findings": list(findings),
                "candidate_weekends": candidate_weekends_by_tournament.get(tournament_id),
            }
        )
    work_items.sort(key=_sort_key)
    return work_items


def _first_value(findings: Iterable[Mapping[str, Any]], field: str) -> str:
    for finding in findings:
        value = str(finding.get(field) or "").strip()
        if value:
            return value
    return ""


def _conflict_labels(findings: Iterable[Mapping[str, Any]]) -> list[str]:
    labels: list[str] = []
    for finding in findings:
        parts = [
            str(finding.get(field) or "")
            for field in ("conflicting_tournament_id", "conflicting_age_group", "conflicting_interval")
        ]
        label = " ".join(part for part in parts if part)
        if label and label not in labels:
            labels.append(label)
    return labels


def _sort_key(item: Mapping[str, Any]) -> tuple:
    return (
        str(item.get("date") or ""),
        str(item.get("host_club") or ""),
        str(item.get("age_group") or ""),
        str(item.get("tournament_id") or ""),
        str(item.get("key")),
    )


def format_date_label(value: str) -> str:
    """Render an ISO date as ``dd.mm.yyyy``, leaving anything else unchanged."""
    if not value:
        return ""
    try:
        return fmt_date(date.fromisoformat(value))
    except ValueError:
        return value


def render_findings_html(findings: Sequence[Mapping[str, Any]]) -> str:
    """The grouped evidence records attached to one work item."""
    items: list[str] = []
    for finding in findings:
        category = str(finding.get("category") or "")
        # Prefer the entry's own operator-facing type; fall back to the
        # structured category label so nothing is rendered without a reason.
        label = str(finding.get("type") or "").strip() or CATEGORY_LABELS.get(category, category or "Funn")
        message = str(finding.get("message") or finding.get("reason") or "").strip()
        items.append(f"<li><strong>{_html.escape(label)}</strong>: {_html.escape(message)}</li>")
    return f'<ul class="work-item-findings">{"".join(items)}</ul>'


def render_candidate_weekends_html(bundle: Mapping[str, Any] | None) -> str:
    """Ranked conflict-aware suggestions plus deterministic near-miss rejections.

    Every suggestion surfaces its interval, availability classification and
    confirmation requirement; rejections keep their deterministic reason so
    the operator can tell "no alternative existed" from "this one failed".
    """
    if not bundle:
        return ""
    candidates = list(bundle.get("candidate_weekends") or [])
    rejected = list(bundle.get("rejected_candidate_dates") or [])
    status = str(bundle.get("status") or "")

    blocks: list[str] = []
    if candidates:
        rows: list[str] = []
        for candidate in candidates:
            rows.append(f"<li>{_candidate_sentence(candidate)}</li>")
        blocks.append(
            '<div class="work-item-suggestions"><p class="work-item-subhead">Forslag '
            "(fra avgrenset, ansvarsbevarende søk)</p>"
            f"<ol>{''.join(rows)}</ol></div>"
        )
    else:
        blocks.append(
            '<div class="work-item-suggestions work-item-suggestions--empty">'
            f"<p>{_html.escape(_no_suggestion_note(status))}</p></div>"
        )

    if rejected:
        rows = [f"<li>{_rejection_sentence(entry)}</li>" for entry in rejected]
        blocks.append(
            '<div class="work-item-rejections"><p class="work-item-subhead">Nærmeste avviste datoer</p>'
            f"<ul>{''.join(rows)}</ul></div>"
        )
    return "".join(blocks)


def _candidate_sentence(candidate: Mapping[str, Any]) -> str:
    interval = format_date_label(str(candidate.get("date") or ""))
    start = str(candidate.get("start_time") or "")
    end = str(candidate.get("end_time") or "")
    if start:
        interval = f"{interval} {start}–{end}" if end else f"{interval} {start}"
    availability = _AVAILABILITY_LABELS.get(str(candidate.get("availability") or ""), "ukjent")
    event = str(candidate.get("calendar_event") or "").strip()
    detail = f"<strong>{_html.escape(interval)}</strong> — {_html.escape(availability)}"
    if event:
        detail += f" ({_html.escape(event)})"
    if candidate.get("requires_host_confirmation"):
        detail += " — krever bekreftelse fra vertsklubben"
    if candidate.get("roster_source") == "alternate":
        detail += " — krever alternativ lagsammensetning"
    detail += _roster_sentence(candidate)
    return detail


def _roster_sentence(candidate: Mapping[str, Any]) -> str:
    roster = list(candidate.get("roster") or [])
    if not roster:
        return ""
    labels = ", ".join(str(team.get("label") or team.get("club") or "?") for team in roster)
    return f" — lag: {_html.escape(labels)}"


def _rejection_sentence(entry: Mapping[str, Any]) -> str:
    date_label = format_date_label(str(entry.get("date") or entry.get("candidate_date") or "")) or "?"
    reason = str(entry.get("reason") or "avvist")
    label = _REJECTION_LABELS.get(reason, reason)
    conflicts = ", ".join(str(value) for value in (entry.get("team_conflicts") or []))
    if conflicts:
        label = f"{label}: {conflicts}"
    return f"{_html.escape(date_label)} — {_html.escape(label)}"


def _no_suggestion_note(status: str) -> str:
    if status == "search_budget_exhausted":
        return (
            "Ingen forslag innenfor søkebudsjettet. Budsjettet ble brukt opp før alle "
            "helgene var vurdert — dette er ikke bevis på at ingen helg passer."
        )
    if status == "bounded_date_set_exhausted":
        return (
            "Ingen forslag. Alle helgene i det avgrensede, ansvarsbevarende søket ble "
            "prøvd uten en verifisert plassering."
        )
    if status == "missing_host_club":
        return "Ingen forslag: turneringen mangler ansvarlig vertsklubb."
    if status == "no_required_duration":
        return "Ingen forslag: mangler påkrevd varighet for å vurdere helgene."
    return "Ingen forslag generert for denne plasseringen."
