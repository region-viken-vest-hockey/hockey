"""Render ``season_changes.html`` -- the public season change-request view.

This page-shell module assembles the standalone HTML page (navbar, hero,
overview table and per-request details) from the canonical request-grouped
ledger owned by
:func:`tournament_scheduler.application.canonical_season.changes.change_request_ledger`.
It never reconstructs, reclassifies or re-derives change requests from rendered
schedule data; the per-request fragments come from
:mod:`tournament_scheduler.pipeline.season_changes_sections`, so the HTML page
and ``make season-changes-markdown`` can never disagree about a request's
identity or status.

Provenance policy: the fields rendered here (request id, created/actor, types,
statuses, notes, affected teams/tournaments, protections, withdrawals and
before/after mutations) are the same canonical provenance facts already exposed
by the published ``manual_schedule.html`` view and the generated
``change-log.md``. The public-bundle sanitizer remains the final gate for
secrets, absolute paths and contact information.
"""

from __future__ import annotations

import html as _html
from pathlib import Path
from typing import Any, Mapping

from ..html import SEASON_CHANGES_FILENAME
from ..html.data_computation import (
    ICON_BAR_CHART,
    ICON_CALENDAR,
    ICON_CLIPBOARD,
    ICON_USERS,
    ICON_WARNING,
)
from ..html.templates import SEASON_CHANGES, STYLES_CSS
from .season_changes_sections import (
    details_html,
    overview_rows,
    request_label,
    status_label,
)

_UNRESOLVED = {"needs_action", "partially_resolved"}

__all__ = ["render_html", "write_html", "request_label", "status_label"]


def _nav_link(href: str, label: str, icon: str, active: bool = False) -> str:
    if not href:
        return ""
    cls = "active" if active else ""
    return f'<a href="{_html.escape(href)}" class="{cls}"><span class="nav-icon">{icon}</span> {_html.escape(label)}</a>'


def render_html(
    ledger: Mapping[str, Any],
    *,
    season_plan_href: str = "season_plan.html",
    report_href: str = "season_plan_report.html",
    calendars_href: str = "",
    cancelled_href: str = "",
    manual_href: str = "",
    input_href: str = "",
) -> str:
    """Render the standalone season-changes page from the canonical ledger."""

    requests = [item for item in ledger.get("requests") or [] if isinstance(item, Mapping)]
    unresolved = sum(1 for item in requests if item.get("status") in _UNRESOLVED)
    season = str(ledger.get("season") or "")

    subtitle = "RVV Hockey — endringer"
    if season:
        subtitle = f"{season} — endringer"

    if unresolved:
        hero_tone, pill_tone = "report-hero--warn", "report-status-pill--warn"
        pill = f"{unresolved} ÅPNE"
    else:
        hero_tone, pill_tone = "report-hero--pass", "report-status-pill--pass"
        pill = "ALLE LØST"

    intro = (
        f"{len(requests)} forespørsel(er) er registrert i den kanoniske endringsloggen. "
        f"{unresolved} krever fortsatt oppfølging. Siden er en read-only projeksjon av "
        "kanonisk schedule.json/decisions.json og endres ikke herfra."
    )

    hidden_parts = []
    if ledger.get("canonical_state_revision"):
        hidden_parts.append(f"Kanonisk revisjon: {ledger.get('canonical_state_revision')}")
    if ledger.get("revision"):
        hidden_parts.append(f"Planrevisjon: {ledger.get('revision')}")

    parts = {
        "$STYLES$": STYLES_CSS,
        "$ICON_CLIPBOARD$": ICON_CLIPBOARD,
        "$CALENDAR_NAV$": _nav_link(calendars_href, "Skrapede kalendere", ICON_CALENDAR),
        "$SEASON_PLAN_NAV$": _nav_link(season_plan_href, "Sesongplan", ICON_CLIPBOARD),
        "$REPORT_NAV$": _nav_link(report_href, "Regler", ICON_BAR_CHART),
        "$CANCELLED_NAV$": _nav_link(cancelled_href, "Avlyste turneringer", ICON_WARNING),
        "$MANUAL_NAV$": _nav_link(manual_href, "Må planlegges manuelt", ICON_WARNING),
        "$INPUT_NAV$": _nav_link(input_href, "Påmeldte lag", ICON_USERS),
        "$SCRAPE_META$": "",
        "$SUBTITLE$": _html.escape(subtitle),
        "$HERO_TONE$": hero_tone,
        "$PILL_TONE$": pill_tone,
        "$SUMMARY_PILL$": pill,
        "$INTRO$": _html.escape(intro),
        "$ROWS$": overview_rows(requests),
        "$DETAILS$": "".join(details_html(item) for item in requests),
        "$HIDDEN_CONTEXT$": _html.escape(" · ".join(hidden_parts)),
    }
    html = SEASON_CHANGES
    for token, value in parts.items():
        html = html.replace(token, value)
    return html


def write_html(
    ledger: Mapping[str, Any],
    export_dir: str | Path,
    **hrefs: str,
) -> str:
    """Write ``season_changes.html`` into *export_dir* and return its path."""

    target = Path(export_dir) / SEASON_CHANGES_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_html(ledger, **hrefs), encoding="utf-8")
    return str(target)
