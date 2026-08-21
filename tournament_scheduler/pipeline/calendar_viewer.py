"""
Calendar viewer — self-contained HTML with month-grid, club filters, source links.

Reads from the unified scraped data cache (``.pipeline/cache/scraped_data.json``)
and generates ``.pipeline/calendars.html`` — a standalone HTML file that renders
a month-by-month calendar grid with:

  - Colour-coded events per club
  - Checkbox filters to toggle clubs on/off
  - Source links on each event
  - Scrape timestamp and data-age indicator
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from tournament_scheduler.html.data_computation import timestamp_string
from tournament_scheduler.html.templates import CALENDAR_VIEWER

from .cache_manager import ScrapedDataCache
from .not_started import NOT_STARTED_MESSAGE, render_not_started_html


# Colour palette for clubs (distinct, accessible)
CLUB_COLORS: list[dict[str, str]] = [
    {"bg": "#E3F2FD", "border": "#1E88E5", "text": "#0D47A1"},  # blue
    {"bg": "#E8F5E9", "border": "#43A047", "text": "#1B5E20"},  # green
    {"bg": "#FFF3E0", "border": "#FB8C00", "text": "#E65100"},  # orange
    {"bg": "#F3E5F5", "border": "#8E24AA", "text": "#4A148C"},  # purple
    {"bg": "#FFEBEE", "border": "#E53935", "text": "#B71C1C"},  # red
    {"bg": "#E0F7FA", "border": "#00ACC1", "text": "#006064"},  # cyan
    {"bg": "#FFF8E1", "border": "#FDD835", "text": "#F57F17"},  # yellow
    {"bg": "#F1F8E9", "border": "#7CB342", "text": "#33691E"},  # lime
    {"bg": "#FBE9E7", "border": "#D84315", "text": "#BF360C"},  # deep orange
]


def _age_string(iso_str: str) -> str:
    """Return human-readable age from ISO timestamp."""
    if not iso_str:
        return "aldri"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo) if dt.tzinfo is not None else datetime.now()
        delta = now - dt
        if delta.total_seconds() < 60:
            return f"{int(delta.total_seconds())}s siden"
        if delta.total_seconds() < 3600:
            return f"{int(delta.total_seconds() // 60)}m siden"
        if delta.days < 1:
            return f"{int(delta.total_seconds() // 3600)}t siden"
        return f"{delta.days}d siden"
    except (ValueError, TypeError):
        return "ukjent"


def _timestamp_string(iso_str: str) -> str:
    """Return an absolute timestamp string for display."""
    if not iso_str:
        return "ukjent"
    return timestamp_string(iso_str)


# Inline SVG icons (16x16 viewBox, currentColor stroke, 1.5px stroke-width)
_ICON_CALENDAR = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="12" height="11" rx="2"/><line x1="2" y1="7" x2="14" y2="7"/><line x1="5" y1="1" x2="5" y2="5"/><line x1="11" y1="1" x2="11" y2="5"/></svg>'
_ICON_CLIPBOARD = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 1.5h5a1 1 0 011 1v1h-7v-1a1 1 0 011-1z"/><rect x="3" y="3.5" width="10" height="11" rx="1.5"/><line x1="6" y1="7" x2="10" y2="7"/><line x1="6" y1="10" x2="10" y2="10"/></svg>'
_ICON_USERS = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="4" r="2.5"/><path d="M1.5 14v-1.5a4 4 0 014-4h1a4 4 0 014 4V14"/><circle cx="12" cy="5" r="1.5"/><path d="M12 11.5a3 3 0 012.5 2.5"/></svg>'
_ICON_ARROW_UP = '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="14" x2="8" y2="2"/><polyline points="3 7 8 2 13 7"/></svg>'
_ICON_EXTERNAL = '<svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2H3a1 1 0 00-1 1v10a1 1 0 001 1h10a1 1 0 001-1v-3"/><polyline points="10 2 14 2 14 6"/><line x1="7" y1="9" x2="14" y2="2"/></svg>'
_ICON_REFRESH = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 1 4 1"/><path d="M1 8a7 7 0 017-7 6.8 6.8 0 015.6 3"/><polyline points="15 12 15 15 12 15"/><path d="M15 8a7 7 0 01-7 7 6.8 6.8 0 01-5.6-3"/></svg>'
_ICON_SEARCH = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="6.5" cy="6.5" r="4.5"/><line x1="10" y1="10" x2="14.5" y2="14.5"/></svg>'
_ICON_CLOCK = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="6"/><polyline points="8 4 8 8 11 10"/></svg>'
_ICON_TERMINAL = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 6 6.5 8.5 4 11"/><line x1="8" y1="11" x2="12" y2="11"/><rect x="1" y="2" width="14" height="12" rx="2"/></svg>'
_ICON_BAR_CHART = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="2" y1="14" x2="2" y2="6"/><line x1="6" y1="14" x2="6" y2="10"/><line x1="10" y1="14" x2="10" y2="4"/><line x1="14" y1="14" x2="14" y2="8"/></svg>'

def _cache_status(entry: dict[str, Any], ttl_hours: float = 6.0) -> str:
    """Return a freshness badge label for a cache entry.

    Returns one of: Blokkert, Cachet, Fersk, Utdatert, Ukjent.
    """
    blocked = entry.get("blocked", False)
    note = entry.get("note", "")
    ts = entry.get("scrape_timestamp", "")

    if blocked:
        return "Blokkert"

    if note and ("tidligere cache" in note.lower() or "bruker" in note.lower()):
        return "Cachet"

    if not ts:
        return "Ukjent"

    try:
        scraped_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now = datetime.now(scraped_at.tzinfo) if scraped_at.tzinfo is not None else datetime.now()
        age = now - scraped_at
        if age.total_seconds() <= ttl_hours * 3600:
            return "Fersk"
        return "Utdatert"
    except (ValueError, TypeError):
        return "Ukjent"


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _month_name(m: int, locale: str = "nb") -> str:
    nb = ["", "Januar", "Februar", "Mars", "April", "Mai", "Juni",
          "Juli", "August", "September", "Oktober", "November", "Desember"]
    return nb[m] if 1 <= m <= 12 else f"Måned {m}"


def _write_not_started_html(output_path: Path, message: str) -> str:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_not_started_html(message), encoding="utf-8")
    return str(output_path)


def _stage4_not_started_message(work_dir: str) -> str | None:
    try:
        stage4 = json.loads((Path(work_dir) / "stage4_export.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    data = stage4.get("data", {}) if isinstance(stage4, dict) else {}
    if isinstance(data, dict) and data.get("not_started"):
        return str(data.get("message") or NOT_STARTED_MESSAGE)
    return None


def generate_html(work_dir: str = ".pipeline", export_dir: str = "export") -> str:
    """Generate the calendar viewer HTML and return its file path.

    Writes to ``<export_dir>/calendars.html`` by default.
    """
    out_path = Path(export_dir) / "calendars.html"
    not_started_message = _stage4_not_started_message(work_dir)
    if not_started_message:
        return _write_not_started_html(out_path, not_started_message)

    cache = ScrapedDataCache(work_dir)
    data = cache.read()
    sources: dict[str, Any] = data.get("sources", {})
    meta: dict[str, Any] = data.get("_meta", {})
    all_events = cache.get_all_events()

    # Assign colours per source
    source_names = sorted(sources.keys())
    color_map: dict[str, dict[str, str]] = {}
    for i, name in enumerate(source_names):
        color_map[name] = CLUB_COLORS[i % len(CLUB_COLORS)]

    # Build event lookup: date -> [events]
    from collections import defaultdict
    events_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    min_date: datetime | None = None
    max_date: datetime | None = None
    for ev in all_events:
        date_str = ev.get("date", "")
        if not date_str:
            continue
        try:
            dt = datetime.strptime(date_str, "%d.%m.%Y")
        except ValueError:
            continue
        events_by_date[date_str].append(ev)
        if min_date is None or dt < min_date:
            min_date = dt
        if max_date is None or dt > max_date:
            max_date = dt

    if min_date is None or max_date is None:
        min_date = datetime.now()
        max_date = datetime.now()

    updated_at = meta.get("updated_at", "")
    start_date_str = meta.get("start_date", "")
    end_date_str = meta.get("end_date", "")

    # Collect all months in range for month filter
    all_months: list[tuple[int, int]] = []
    y, m = min_date.year, min_date.month
    end_y, end_m = max_date.year, max_date.month
    while (y < end_y) or (y == end_y and m <= end_m):
        all_months.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1

    def _format_time(ev: dict[str, Any]) -> str:
        """Extract start and end time string from event."""
        dt_str = ev.get("datetime", "")
        dur = ev.get("duration_hours", 0)
        if not dt_str:
            return ""
        try:
            dt = datetime.fromisoformat(dt_str)
            start = dt.strftime("%H:%M")
            if dur and dur > 0:
                from datetime import timedelta
                end_dt = dt + timedelta(hours=dur)
                end = end_dt.strftime("%H:%M")
                return f"{start}-{end}"
            return start
        except (ValueError, TypeError):
            return ""

    # Generate month-by-month calendar
    def _month_html(year: int, month: int) -> str:
        import calendar
        cal = calendar.Calendar()
        month_days = cal.monthdayscalendar(year, month)
        month_name = _month_name(month)
        now = datetime.now()

        lines = [
            f'<div class="month" id="m{year}{month:02d}" data-year="{year}" data-month="{month:02d}">',
            f'  <h3 class="month-title">{month_name} {year}</h3>',
            '  <table class="cal">',
            '    <thead><tr class="day-names">',
            '      <th>Man</th><th>Tir</th><th>Ons</th><th>Tor</th><th>Fre</th><th>Lør</th><th>Søn</th>',
            '    </tr></thead>',
            '    <tbody>',
        ]

        for week in month_days:
            lines.append('      <tr>')
            for day_num in week:
                if day_num == 0:
                    lines.append('        <td class="empty"></td>')
                    continue
                date_str = f"{day_num:02d}.{month:02d}.{year}"
                day_events = events_by_date.get(date_str, [])
                is_today = (year == now.year and month == now.month and day_num == now.day)
                has_events = len(day_events) > 0

                cls = "day"
                if is_today:
                    cls += " today"
                if has_events:
                    cls += " has-events"

                lines.append(f'        <td class="{cls}">')
                lines.append(f'          <div class="day-num">{day_num}</div>')

                if day_events:
                    lines.append('          <div class="events">')
                    for ev in day_events:
                        src = ev.get("_source", "?")
                        src_url = ev.get("_source_url", "")
                        color = color_map.get(src, CLUB_COLORS[-1])
                        name = _escape_html(ev.get("name", "?"))
                        time_str = _format_time(ev)
                        link = f'<a class="ev-ext-link" href="{_escape_html(src_url)}" target="_blank" title="Åpne {_escape_html(src)} sin kalender">{_ICON_EXTERNAL}</a>' if src_url else ""
                        lines.append(
                            f'<div class="event" data-source="{_escape_html(src)}" style="background:{color["bg"]};border-left:3px solid {color["border"]};color:{color["text"]}" title="{_escape_html(src)} — {name}">'
                            + (f'<span class="ev-time">{time_str}</span> ' if time_str else '')
                            + f'<span class="ev-name">{name}</span> '
                            + f'<span class="ev-meta">{_escape_html(src)} {link}</span>'
                            + f'</div>'
                        )
                    lines.append('          </div>')

                lines.append('        </td>')
            lines.append('      </tr>')

        lines.extend(['    </tbody>', '  </table>', '</div>'])
        return "\n".join(lines)

    # Build month list
    months_html: list[str] = []
    for y, m in all_months:
        months_html.append(_month_html(y, m))

    # Build club filter controls
    club_filter_lines: list[str] = []
    for name in source_names:
        color = color_map[name]
        entry = sources.get(name, {})
        cnt = entry.get("event_count", 0)
        ts = entry.get("scrape_timestamp", "")
        age = _age_string(ts)
        freshness = _cache_status(entry)
        source_url = str(entry.get("url", "") or "").strip()
        source_link = (
            f'<a class="source-link" href="{_escape_html(source_url)}" target="_blank" '
            f'rel="noopener noreferrer" onclick="event.stopPropagation();" '
            f'title="Åpne {_escape_html(name)} sin kalender">'
            f'{_ICON_EXTERNAL}<span>Kilde</span></a>'
            if source_url else ""
        )
        club_filter_lines.append(
            f'<label class="filter-item" style="--cbg:{color["bg"]};--cborder:{color["border"]}">'
            f'<input type="checkbox" class="club-filter" data-club="{_escape_html(name)}" checked>'
            f'<span class="club-label">{_escape_html(name)}</span> '
            f'<span class="club-stats">({cnt} hendelser, {age})</span> '
            f'<span class="club-freshness">{_escape_html(freshness)}</span>'
            f'{source_link}'
            f'</label>'
        )
    club_filter_html = "\n".join(club_filter_lines)

    # Build month filter controls
    month_filter_lines: list[str] = []
    for y, m in all_months:
        mn = _month_name(m)
        mid = f"m{y}{m:02d}"
        month_filter_lines.append(
            f'<label class="month-filter-item">'
            f'<input type="checkbox" class="month-filter" data-month="{mid}" checked>'
            f'<span>{mn} {y}</span>'
            f'</label>'
        )
    month_filter_html = "\n".join(month_filter_lines)

    total_events = data.get("total_events", 0)
    source_count = data.get("source_count", 0)
    age_all = _timestamp_string(updated_at)

    # Check if season plan / input overview exist alongside
    season_plan_path = Path(export_dir) / "season_plan.html"
    has_season_plan = season_plan_path.exists()
    input_html_path = Path(export_dir) / "input.html"
    input_nav = (
        f'<a href="input.html"><span class="nav-icon">{_ICON_USERS}</span> Påmeldte lag</a>'
        if input_html_path.exists()
        else ""
    )

    # Read scraping confidence assessment from Stage 2 checkpoint (if available)
    confidence_html = ""
    try:
        from .state import PipelineState, StageName
        _state = PipelineState(work_dir)
        _scraping_cp = _state.read_stage(StageName.SCRAPING)
        _conf = _scraping_cp.get("confidence") if _scraping_cp else None
        if _conf:
            _verdict = _conf.get("verdict", "OK")
            _assessment = _escape_html(_conf.get("overall_assessment", ""))
            _suspicious = _conf.get("suspicious_sources", [])
            _gaps = _conf.get("gaps", [])
            if _verdict == "WARN":
                _susp_html = (
                    f'<div class="conf-row"><span class="conf-label">Mistenkelige kilder:</span> '
                    + _escape_html(", ".join(_suspicious))
                    + "</div>"
                    if _suspicious else ""
                )
                _gaps_html = "".join(
                    f'<div class="conf-row conf-gap">→ {_escape_html(g)}</div>'
                    for g in _gaps
                )
                confidence_html = (
                    f'<div class="confidence-banner warn">'
                    f'<span class="conf-icon">⚠</span>'
                    f'<div class="conf-body">'
                    f'<strong>Skrapekvalitet: WARN</strong> — {_assessment}'
                    f'{_susp_html}{_gaps_html}'
                    f'</div></div>'
                )
            else:
                confidence_html = (
                    f'<div class="confidence-banner ok">'
                    f'<span class="conf-icon">✓</span>'
                    f'<div class="conf-body">'
                    f'<strong>Skrapekvalitet: OK</strong>'
                    + (f' — {_assessment}' if _assessment else '')
                    + f'</div></div>'
                )
    except Exception:
        pass  # Confidence section is optional — never block report generation

    html = CALENDAR_VIEWER
    replacements = {
        "@@ICON_CALENDAR@@": _ICON_CALENDAR,
        "@@ICON_CLIPBOARD@@": _ICON_CLIPBOARD,
        "@@ICON_BAR_CHART@@": _ICON_BAR_CHART,
        "@@ICON_ARROW_UP@@": _ICON_ARROW_UP,
        "@@INPUT_NAV@@": input_nav,
        "@@SEASON_PLAN_ACTIVE@@": "active" if not has_season_plan else "",
        "@@ICON_USERS@@": _ICON_USERS,
        "@@ICON_SEARCH@@": _ICON_SEARCH,
        "@@ICON_CLOCK@@": _ICON_CLOCK,
        "@@CLUB_FILTER_HTML@@": club_filter_html,
        "@@MONTH_FILTER_HTML@@": month_filter_html,
        "@@SOURCE_COUNT@@": str(source_count),
        "@@TOTAL_EVENTS@@": str(total_events),
        "@@AGE_ALL@@": age_all,
        "@@START_DATE@@": start_date_str,
        "@@END_DATE@@": end_date_str,
        "@@CONFIDENCE_HTML@@": confidence_html,
        "@@MONTHS_HTML@@": "".join(months_html),
    }
    for marker, value in replacements.items():
        html = html.replace(marker, value)

    out_path = Path(export_dir) / "calendars.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return str(out_path.resolve())


if __name__ == "__main__":  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description="Generate calendar viewer HTML from cache")
    parser.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory")
    parser.add_argument("--export-dir", default="export", help="Export directory for HTML output")
    parser.add_argument("--refresh", action="store_true", help="Force re-scrape (marks cache stale)")
    args = parser.parse_args()

    if args.refresh:
        from .cache_manager import ScrapedDataCache
        c = ScrapedDataCache(work_dir=args.work_dir)
        c.force_refresh()
        print("Cache markert som utdatert — kjør rvv-miniputt run for å re-skrape.")
    else:
        path = generate_html(work_dir=args.work_dir, export_dir=args.export_dir)
        print(f"Kalendervisning generert: {path}")
