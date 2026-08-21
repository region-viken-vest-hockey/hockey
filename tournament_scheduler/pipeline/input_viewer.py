"""Input viewer — public, read-only overview of registered clubs/teams.

Reads only the whitelisted ``Lag`` worksheet of the input workbook (see
``input_workbook.PUBLIC_SHEET_WHITELIST``) and generates a standalone
``input.html`` page: teams grouped by age group, with club/age-group filters,
free-text search, and club/team totals. Internal configuration sheets
(``Aldersgrupper``, ``Innstillinger``, ``Kilder``, ``Datopreferanser``) and
the workbook file itself are never read or referenced by this module.
"""

from __future__ import annotations

import html as _html
from pathlib import Path
from typing import Any

from tournament_scheduler.html.templates import INPUT_VIEWER

from .input_workbook import read_public_teams

# Inline SVG icons (14x14 viewBox, currentColor stroke, 1.5px stroke-width) —
# kept consistent with calendar_viewer.py's icon set.
_ICON_CALENDAR = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="12" height="11" rx="2"/><line x1="2" y1="7" x2="14" y2="7"/><line x1="5" y1="1" x2="5" y2="5"/><line x1="11" y1="1" x2="11" y2="5"/></svg>'
_ICON_CLIPBOARD = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 1.5h5a1 1 0 011 1v1h-7v-1a1 1 0 011-1z"/><rect x="3" y="3.5" width="10" height="11" rx="1.5"/><line x1="6" y1="7" x2="10" y2="7"/><line x1="6" y1="10" x2="10" y2="10"/></svg>'
_ICON_USERS = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="4" r="2.5"/><path d="M1.5 14v-1.5a4 4 0 014-4h1a4 4 0 014 4V14"/><circle cx="12" cy="5" r="1.5"/><path d="M12 11.5a3 3 0 012.5 2.5"/></svg>'
_ICON_BAR_CHART = '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="2" y1="14" x2="2" y2="6"/><line x1="6" y1="14" x2="6" y2="10"/><line x1="10" y1="14" x2="10" y2="4"/><line x1="14" y1="14" x2="14" y2="8"/></svg>'


def _age_group_sort_key(age_group: str) -> tuple[int, str]:
    """Sort U-groups numerically (U7, U8, U10, U12, ...) before anything else."""
    digits = "".join(ch for ch in age_group if ch.isdigit())
    return (int(digits) if digits else 999, age_group)


def generate_html(
    *,
    input_path: str,
    export_dir: str = "export",
    calendars_path: str | None = None,
    season_label: str = "",
) -> str:
    """Generate ``input.html`` and return its path.

    Reads only the whitelisted ``Lag`` worksheet via
    :func:`input_workbook.read_public_teams`. Returns the path even when the
    sheet has no rows (an empty, valid page is written rather than nothing).
    """
    teams = read_public_teams(input_path)

    # Deduplicate identical (club, label, age_group) rows and sort for display.
    seen: set[tuple[str, str, str]] = set()
    rows: list[dict[str, Any]] = []
    for team in teams:
        club = str(team.get("club") or "").strip()
        label = str(team.get("label") or "").strip()
        age_group = str(team.get("age_group") or "").strip()
        if not club or not label:
            continue
        key = (club, label, age_group)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"club": club, "label": label, "age_group": age_group})
    rows.sort(key=lambda r: (_age_group_sort_key(r["age_group"]), r["club"], r["label"]))

    all_clubs = sorted({r["club"] for r in rows})
    all_age_groups = sorted({r["age_group"] for r in rows}, key=_age_group_sort_key)
    total_teams = len(rows)
    total_clubs = len(all_clubs)

    club_options = "".join(
        f'<option value="{_html.escape(club)}">{_html.escape(club)}</option>' for club in all_clubs
    )
    age_options = "".join(
        f'<option value="{_html.escape(ag)}">{_html.escape(ag)}</option>' for ag in all_age_groups
    )

    groups_html: list[str] = []
    for age_group in all_age_groups:
        group_rows = [r for r in rows if r["age_group"] == age_group]
        group_clubs = len({r["club"] for r in group_rows})
        row_html = "".join(
            '<tr class="team-row" '
            f'data-club="{_html.escape(r["club"])}" '
            f'data-age="{_html.escape(age_group)}" '
            f'data-search="{_html.escape((r["club"] + " " + r["label"]).lower())}">'
            f'<td class="club-summary-name">{_html.escape(r["club"])}</td>'
            f'<td>{_html.escape(r["label"])}</td>'
            "</tr>"
            for r in group_rows
        )
        groups_html.append(
            '<section class="age-group-section">'
            f'<h2>{_html.escape(age_group)} '
            f'<span class="age-group-count">{len(group_rows)} lag &middot; {group_clubs} klubber</span></h2>'
            '<div class="table-wrap"><table class="club-summary-table">'
            '<thead><tr><th>Klubb</th><th>Lag</th></tr></thead>'
            f'<tbody>{row_html}</tbody></table></div>'
            "</section>"
        )
    if not groups_html:
        groups_html.append(
            '<div class="no-results" id="emptyState"><p>Ingen lag er registrert i input-arket ennå.</p></div>'
        )
    groups_section_html = "".join(groups_html)

    calendars_href = "calendars.html" if calendars_path and Path(calendars_path).exists() else ""
    calendars_nav = (
        f'<a href="{calendars_href}"><span class="nav-icon">{_ICON_CALENDAR}</span> Skrapede kalendere</a>'
        if calendars_href
        else ""
    )

    template = INPUT_VIEWER
    replacements = {
        "@@CALENDARS_NAV@@": calendars_nav,
        "@@ICON_CLIPBOARD@@": _ICON_CLIPBOARD,
        "@@ICON_BAR_CHART@@": _ICON_BAR_CHART,
        "@@ICON_USERS@@": _ICON_USERS,
        "@@SEASON_LABEL@@": f" &mdash; {_html.escape(season_label)}" if season_label else "",
        "@@TOTAL_CLUBS@@": str(total_clubs),
        "@@TOTAL_TEAMS@@": str(total_teams),
        "@@AGE_GROUP_COUNT@@": str(len(all_age_groups)),
        "@@AGE_OPTIONS@@": age_options,
        "@@CLUB_OPTIONS@@": club_options,
        "@@GROUPS_SECTION@@": groups_section_html,
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    html = template

    out_path = Path(export_dir) / "input.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return str(out_path.resolve())


if __name__ == "__main__":  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description="Generer input.html fra input-arbeidsboken (Lag-arket)")
    parser.add_argument("--input-path", default="input.xlsx", help="Sti til input.xlsx")
    parser.add_argument("--export-dir", default="export", help="Eksportmappe for HTML-output")
    args = parser.parse_args()

    path = generate_html(input_path=args.input_path, export_dir=args.export_dir)
    print(f"Input-visning generert: {path}")
