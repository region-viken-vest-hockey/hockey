"""Pure data‑computation functions for the season‑plan HTML exporter.

Extracted from ``HtmlExporter.export()`` and related static methods.
All functions are deterministic — given the same inputs they always
return the same values.
"""

from __future__ import annotations

from datetime import datetime as _dt
from zoneinfo import ZoneInfo
from pathlib import Path

from tournament_scheduler.club_distances import (
    compute_team_travel_distances as _compute_team_travel_distances,
)
from tournament_scheduler.club_registry import canonicalize_club_name as _canonicalize_club_name
from tournament_scheduler.models import team_key as _team_key
from tournament_scheduler.models import find_duplicate_labels as _find_duplicate_labels

# ---------------------------------------------------------------------------
# Inline SVG icons (14x14 or 16x16 viewBox, currentColor stroke, 1.5px)
# ---------------------------------------------------------------------------

ICON_CALENDAR = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="12" height="11" rx="2"/><line x1="2" y1="7" x2="14" y2="7"/><line x1="5" y1="1" x2="5" y2="5"/><line x1="11" y1="1" x2="11" y2="5"/></svg>'
ICON_CLIPBOARD = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 1.5h5a1 1 0 011 1v1h-7v-1a1 1 0 011-1z"/><rect x="3" y="3.5" width="10" height="11" rx="1.5"/><line x1="6" y1="7" x2="10" y2="7"/><line x1="6" y1="10" x2="10" y2="10"/></svg>'
ICON_USERS = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="4" r="2.5"/><path d="M1.5 14v-1.5a4 4 0 014-4h1a4 4 0 014 4V14"/><circle cx="12" cy="5" r="1.5"/><path d="M12 11.5a3 3 0 012.5 2.5"/></svg>'
ICON_TARGET = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="6"/><circle cx="8" cy="8" r="3"/><circle cx="8" cy="8" r="1" fill="currentColor"/></svg>'
ICON_TRAVEL = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="10" r="3"/><path d="M12 13.7C17.3 9 20 5 20 2a8 8 0 1 0-16 0c0 3 2.7 7 8 11.7z"/></svg>'
ICON_WARNING = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 1.5l-7 12h14l-7-12z"/><line x1="8" y1="6" x2="8" y2="9"/><circle cx="8" cy="11" r=".5" fill="currentColor"/></svg>'
ICON_DOWNLOAD = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 10v3a1 1 0 01-1 1H3a1 1 0 01-1-1v-3"/><polyline points="5 7 8 10 11 7"/><line x1="8" y1="10" x2="8" y2="2"/></svg>'
ICON_FILE_SPREADSHEET = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 2h6l4 4v8a1 1 0 01-1 1H3a1 1 0 01-1-1V3a1 1 0 011-1z"/><polyline points="9 2 9 6 13 6"/><line x1="5" y1="9" x2="11" y2="9"/><line x1="5" y1="12" x2="11" y2="12"/></svg>'
ICON_CLOCK = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="6"/><polyline points="8 4 8 8 11 10"/></svg>'
ICON_BAR_CHART = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="2" y1="14" x2="2" y2="6"/><line x1="6" y1="14" x2="6" y2="10"/><line x1="10" y1="14" x2="10" y2="4"/><line x1="14" y1="14" x2="14" y2="8"/></svg>'

_OSLO_TZ = ZoneInfo("Europe/Oslo")

# ---------------------------------------------------------------------------
# RVV club helpers
# ---------------------------------------------------------------------------

_RVV_CLUBS = (
    "Ringerike",
    "Tønsberg",
    "Frisk Asker",
    "Sandefjord Penguins",
    "Jar",
    "Holmen",
    "Skien",
    "Jutul",
    "Kongsberg",
)


def canonical_rvv_club_name(club_name: str) -> str:
    """Return the canonical RVV display name for a known club alias.

    Delegates to `club_registry.canonicalize_club_name` (issue #272) so
    reporting/export code shares one canonicalization source of truth with
    the rest of the pipeline instead of maintaining its own alias table.
    """
    return _canonicalize_club_name(club_name)


# ---------------------------------------------------------------------------
# Date / label helpers
# ---------------------------------------------------------------------------


def season_label(plan: object) -> str:
    """Build a short season label like ``"2025/2026"`` from the plan."""
    start = getattr(plan, "start_date", None)
    end = getattr(plan, "end_date", None)
    if start and end:
        sy = start.year
        ey = end.year
        if sy == ey:
            return f"{sy}/{ey + 1}"
        return f"{sy}-{ey}"
    return ""


def fmt_date(d: object) -> str:
    """Format a date to ``"dd.mm.yyyy"`` or return ``"?"``."""
    if d is None:
        return "?"
    if hasattr(d, "strftime"):
        return d.strftime("%d.%m.%Y")  # type: ignore[union-attr]
    return str(d)


def age_string(iso_str: str) -> str:
    """Human-friendly relative time string (``"3d siden"`` / ``"5t siden"``)."""
    if not iso_str:
        return ""
    try:
        dt = _dt.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = _dt.now(dt.tzinfo) if dt.tzinfo is not None else _dt.now()
        delta = now - dt
        if delta.total_seconds() < 60:
            return f"{int(delta.total_seconds())}s siden"
        if delta.total_seconds() < 3600:
            return f"{int(delta.total_seconds() // 60)}m siden"
        if delta.days < 1:
            return f"{int(delta.total_seconds() // 3600)}t siden"
        return f"{delta.days}d siden"
    except (ValueError, TypeError):
        return ""


def timestamp_string(iso_str: str) -> str:
    """Format an ISO timestamp as an absolute date-time string."""
    if not iso_str:
        return ""
    try:
        dt = _dt.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(_OSLO_TZ)
        else:
            dt = dt.replace(tzinfo=_OSLO_TZ)
        return dt.strftime("%Y-%m-%d %H:%M %Z")
    except (ValueError, TypeError):
        return iso_str.replace("T", " ")


# ---------------------------------------------------------------------------
# Data computation — team game counts
# ---------------------------------------------------------------------------


def compute_team_game_counts(plan: object) -> dict[str, int]:
    """Return a dict mapping team_key → total games played.

    Keys are disambiguated via team_key() so that teams with the same label
    but different clubs or age groups are counted separately.
    """
    # First pass: collect all team objects to find labels that are shared by
    # more than one distinct (club, age_group) combination.
    duplicate_labels = _find_duplicate_labels(
        team_obj
        for t in getattr(plan, "tournaments", [])
        for g in getattr(t, "games", [])
        for team_obj in (getattr(g, "home"), getattr(g, "away"))
    )

    # Second pass: count games per disambiguated team key.
    team_game_counts: dict[str, int] = {}
    for t in getattr(plan, "tournaments", []):
        for g in getattr(t, "games", []):
            for team_obj in (getattr(g, "home"), getattr(g, "away")):
                key = _team_key(team_obj, duplicate_labels)
                team_game_counts[key] = team_game_counts.get(key, 0) + 1
    return team_game_counts


# ---------------------------------------------------------------------------
# Data computation — travel info
# ---------------------------------------------------------------------------


def compute_team_travel_info(plan: object) -> tuple[dict[str, int], str, str, int, str]:
    """Compute travel distances and return a 5‑tuple.

    Returns
    -------
    team_travel : dict[str, int]
    most_travel_team : str
    most_travel_km : str
    total_travel_km : int
    travel_count_estimate_html : str
    """
    team_travel = _compute_team_travel_distances(plan)  # type: ignore[arg-type]
    most_travel_team = next(iter(team_travel.keys()), "-") if team_travel else "-"
    most_travel_km = str(max(team_travel.values())) if team_travel else "0"
    total_travel_km = int(sum(team_travel.values()))
    travel_count_estimate_html = ""
    if len(team_travel) < 3:
        travel_count_estimate_html = (
            '<div style="padding:8px 16px;font-size:12px;color:var(--text-muted);'
            'text-align:center;margin-bottom:12px">'
            '<span class="warning-icon">$ICON_WARNING$</span> '
            "F\u00e5 lag med reisedata &mdash; avstandene er grove anslag basert p\u00e5 "
            "kjente arenaer.</div>"
        )
    return team_travel, most_travel_team, most_travel_km, total_travel_km, travel_count_estimate_html


# ---------------------------------------------------------------------------
# Data computation — heatmap
# ---------------------------------------------------------------------------


def compute_heatmap_data(plan: object) -> tuple[dict[str, dict[str, list[str]]], list[str], list[str]]:
    """Build the heatmap dict from tournament data.

    Returns a 3‑tuple ``(heatmap, heatmap_weeks, heatmap_clubs)``.
    """
    heatmap: dict[str, dict[str, list[str]]] = {}
    all_host_clubs: set[str] = set()
    for t in getattr(plan, "tournaments", []):
        if getattr(t, "cancelled", False) or not getattr(t, "date", None):
            continue
        host = getattr(t, "host_club", None) or ""
        if not host:
            continue
        iso_year, iso_week, _ = getattr(t, "date").isocalendar()
        week_key = f"{iso_year}-W{iso_week:02d}"
        heatmap.setdefault(week_key, {}).setdefault(host, []).append(getattr(t, "age_group", ""))
        all_host_clubs.add(host)
    return heatmap, sorted(heatmap.keys()), sorted(all_host_clubs)


# ---------------------------------------------------------------------------
# Data computation — club stats
# ---------------------------------------------------------------------------


def compute_club_stats(plan: object, team_travel: dict[str, int]) -> tuple[dict[str, dict[str, object]], list[str]]:
    """Aggregate hosting, away, teams, and travel per club.

    Returns a 2‑tuple ``(club_stats, all_clubs_list)``.
    """
    club_hosted: dict[str, int] = {}
    club_away: dict[str, int] = {}
    club_teams: dict[str, list[str]] = {}
    club_travel: dict[str, int] = {}

    # Disambiguated team_key() -> club, so travel totals (keyed the same way
    # by compute_team_travel_distances()) can be attributed to the right
    # club without guessing from the label text.
    duplicate_labels = _find_duplicate_labels(
        team for t in getattr(plan, "tournaments", []) for team in getattr(t, "teams", [])
    )
    key_to_club: dict[str, str] = {}

    for t in getattr(plan, "tournaments", []):
        if getattr(t, "cancelled", False):
            continue
        host = getattr(t, "host_club", None) or ""
        if host:
            club_hosted[host] = club_hosted.get(host, 0) + 1
        seen_clubs: set[str] = set()
        for team in getattr(t, "teams", []):
            tc = team.club
            key = _team_key(team, duplicate_labels)
            club_teams.setdefault(tc, [])
            if key not in club_teams[tc]:
                club_teams[tc].append(key)
            key_to_club[key] = tc
            if host and tc != host and tc not in seen_clubs:
                seen_clubs.add(tc)
                club_away[tc] = club_away.get(tc, 0) + 1

    for key, km in team_travel.items():
        club_name = key_to_club.get(key)
        if club_name:
            club_travel[club_name] = club_travel.get(club_name, 0) + km

    club_stats: dict[str, dict[str, object]] = {}
    all_clubs_set: set[str] = set()
    for club in set(club_hosted) | set(club_away) | set(club_teams):
        all_clubs_set.add(club)
        club_stats[club] = {
            "hosted": club_hosted.get(club, 0),
            "away": club_away.get(club, 0),
            "teams": len(club_teams.get(club, [])),
            "travel_km": club_travel.get(club, 0),
            "team_list": club_teams.get(club, []),
        }
    return club_stats, sorted(all_clubs_set)


# ---------------------------------------------------------------------------
# Export links HTML
# ---------------------------------------------------------------------------


def build_export_links_html(output_files: dict[str, str] | None) -> str:
    """Build the header export-download link buttons HTML."""
    if not output_files:
        return ""

    links_parts = ['<div class="export-links">']
    link_defs = [
        ("excel", ICON_DOWNLOAD + " Last ned Excel (.xlsx)", "#38bdf8"),
        ("csv_overview", ICON_BAR_CHART + " Last ned CSV", "#34d399"),
        ("csv_games", ICON_FILE_SPREADSHEET + " Last ned CSV (kamper)", "#fbbf24"),
        ("ical", ICON_CALENDAR + " Last ned iCal (.ics)", "#f87171"),
    ]
    for key, label, color in link_defs:
        if key in output_files:
            filename = Path(output_files[key]).name
            links_parts.append(
                f'<a href="{filename}" class="export-link-btn" '
                f'style="--link-color:{color}" download>{label}</a>'
            )
    links_parts.append('</div>')
    return "".join(links_parts)


# ---------------------------------------------------------------------------
# Age group display helpers
# ---------------------------------------------------------------------------


def compute_display_age_groups(plan: object, age_groups: list[str] | None) -> list[str]:
    """Build the sorted, deduplicated age-group list for the page."""
    if age_groups:
        return list(dict.fromkeys(age_groups))
    return list(dict.fromkeys(sorted({getattr(t, "age_group", "") for t in getattr(plan, "tournaments", [])})))
