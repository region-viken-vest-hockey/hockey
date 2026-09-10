"""Renders manual_schedule.html -- the "Må planlegges manuelt" view."""

from __future__ import annotations

import html as _html

from ..models import SeasonPlan
from ..html.data_computation import (
    ICON_BAR_CHART,
    ICON_CALENDAR,
    ICON_CLIPBOARD,
    ICON_USERS,
    ICON_WARNING,
    fmt_date,
    season_label,
)
from ..html.templates import MANUAL_SCHEDULE, STYLES_CSS

MANUAL_SCHEDULE_FILENAME = "manual_schedule.html"

# Only these structured categories represent genuine manual ice-time/booking
# work. Participation-target deviations (over or under) are team-level
# planning-quality signals, not ice-booking tasks, and must never be
# rendered on manual_schedule.html even if a caller passes one in.
MANUAL_SCHEDULE_CATEGORIES = frozenset(
    {
        "arena_collision",
        "manual_calendar_verification",
        "manual_hosting_obligation",
        "manual_external_conflict",
    }
)


def _manual_schedule_html(
    plan: SeasonPlan,
    *,
    manual_entries: list[dict[str, str]] | None = None,
    generated_at: str = "",
    input_path: str = "",
    date_range: str = "",
    source_count: int = 0,
    event_count: int = 0,
    blocked: list[str] | None = None,
    scrape_age: str = "",
    calendars_href: str = "",
    season_plan_href: str = "",
    report_href: str = "",
    input_href: str = "",
) -> str:
    """Render the dedicated “Må planlegges manuelt” page.

    Lists everything that cannot be treated as auto-confirmed hall time:

    - tournaments that ended up with a same-arena/sequence overflow collision
      during host/time assignment (the auto-planner already tried to shift
      them; hall time must be booked by hand or the plan re-run), and
    - tournaments hosted by clubs whose calendar source could not be scraped
      (they still receive their share of home tournaments, but the assigned
      start time is provisional — the istid must be booked/verified manually).

    Each entry carries a ``type`` (Grunn) so the arena scheduler can see why
    it must act.
    """
    from ..html.data_computation import canonical_rvv_club_name

    # Filter on the structured category, not the rendered reason text -- a
    # participation-target deviation must never inflate this page's count
    # even if a caller forgets to filter it out first.
    entries = sorted(
        (e for e in (manual_entries or []) if e.get("category") in MANUAL_SCHEDULE_CATEGORIES),
        key=lambda c: (c.get("date", ""), c.get("arena", ""), c.get("tournament_id", "")),
    )
    rows: list[str] = []
    for idx, c in enumerate(entries, start=1):
        arena = str(c.get("arena", "") or "")
        raw_host = str(c.get("host_club", "") or "")
        host = canonical_rvv_club_name(raw_host) if raw_host else ""
        if not host or host == "-":
            host = raw_host or arena or "?"
        tournament_id = str(c.get("tournament_id", "") or "")
        age_group = str(c.get("age_group", "") or "")
        date_val = str(c.get("date", "") or "")
        interval = str(c.get("interval", "") or "")
        if not interval and date_val:
            try:
                from datetime import date as _date
                interval = fmt_date(_date.fromisoformat(date_val)) or date_val
            except ValueError:
                interval = date_val
        entry_type = str(c.get("type", "") or "Arena-/tidskollisjon")
        conflict_id = str(c.get("conflicting_tournament_id", "") or "")
        conflict_ag = str(c.get("conflicting_age_group", "") or "")
        conflict_interval = str(c.get("conflicting_interval", "") or "")
        detail = str(c.get("message", "") or "")
        if not detail:
            detail = f"{interval} kolliderer med {conflict_interval}" if conflict_interval else interval
        conflict_cell = " ".join(part for part in (conflict_id, conflict_ag, conflict_interval) if part) or "-"
        rows.append(
            "<tr>"
            f"<td class=\"numeric-cell\">{idx}</td>"
            f"<td>{_html.escape(tournament_id)}</td>"
            f"<td>{_html.escape(date_val)}</td>"
            f"<td><strong>{_html.escape(age_group)}</strong></td>"
            f"<td>{_html.escape(host)}</td>"
            f"<td>{_html.escape(arena)}</td>"
            f"<td>{_html.escape(interval)}</td>"
            f"<td>{_html.escape(entry_type)}</td>"
            f"<td>{_html.escape(conflict_cell)}</td>"
            f"<td>{_html.escape(detail)}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan=\"10\" class=\"empty-cell\">Ingen turneringer trenger manuell planlegging.</td></tr>')

    rows_html = "".join(rows)

    def _nav_link(href: str, label: str, icon: str, active: bool = False) -> str:
        cls = "active" if active else ""
        return f'<a href="{_html.escape(href)}" class="{cls}"><span class="nav-icon">{icon}</span> {_html.escape(label)}</a>'

    calendar_nav = _nav_link(calendars_href, "Skrapede kalendere", ICON_CALENDAR) if calendars_href else ""
    season_plan_nav = (
        _nav_link(season_plan_href, "Sesongplan", ICON_CLIPBOARD) if season_plan_href else ""
    )
    report_nav = _nav_link(report_href, "Regler", ICON_BAR_CHART) if report_href else ""
    manual_nav = _nav_link(MANUAL_SCHEDULE_FILENAME, "Må planlegges manuelt", ICON_WARNING, active=True)
    input_nav = _nav_link(input_href, "Påmeldte lag", ICON_USERS) if input_href else ""

    scrape_meta_parts: list[str] = []
    if source_count:
        scrape_meta_parts.append(f"{source_count} kilder")
    if event_count:
        scrape_meta_parts.append(f"{event_count} hendelser")
    if scrape_age:
        scrape_meta_parts.append(f"Data: {scrape_age}")
    scrape_meta = " &middot; ".join(scrape_meta_parts)
    if scrape_meta:
        scrape_meta = f'<span class="meta-nav">{scrape_meta}</span>'

    subtitle = "RVV Hockey &mdash; manuelt behov"
    if season_label(plan):
        subtitle = f"{_html.escape(season_label(plan))} &mdash; manuelt behov"

    extra_note = ""
    if blocked:
        names = ", ".join(str(item) for item in blocked)
        extra_note = (
            '<div class="report-action report-action--warn"><strong>Datagrunnlag</strong>'
            f"<p>{_html.escape(names)} var utilgjengelig under skraping; husk å følge opp manuelt.</p></div>"
        )

    hidden_parts: list[str] = []
    if date_range:
        hidden_parts.append(f"Periode: {date_range}")
    if generated_at:
        hidden_parts.append(f"Generert: {generated_at}")
    if input_path:
        hidden_parts.append(f"Input: {input_path}")

    entry_count = str(len(entries))
    parts = {
        "$STYLES$": STYLES_CSS,
        "$CALENDAR_NAV$": calendar_nav,
        "$SEASON_PLAN_NAV$": season_plan_nav,
        "$REPORT_NAV$": report_nav,
        "$MANUAL_NAV$": manual_nav,
        "$INPUT_NAV$": input_nav,
        "$SCRAPE_META$": scrape_meta,
        "$ICON_WARNING$": ICON_WARNING,
        "$SUBTITLE$": subtitle,
        "$ENTRY_COUNT$": entry_count,
        "$EXTRA_NOTE$": extra_note,
        "$ROWS$": rows_html,
        "$HIDDEN_CONTEXT$": _html.escape(" · ".join(hidden_parts)),
    }
    html = MANUAL_SCHEDULE
    for token, value in parts.items():
        html = html.replace(token, value)
    return html
