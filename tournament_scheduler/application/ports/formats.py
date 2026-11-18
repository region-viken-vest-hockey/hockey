from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .common import PortCapabilities, PortError, PortErrorCode


class Format(str, Enum):
    XLSX = "xlsx"
    CSV = "csv"
    ICS = "ics"
    Spond = "spond"
    HTML = "html"


@dataclass(frozen=True)
class FormatterResult:
    format: Format
    content: str
    mime_type: str
    file_name: str


FORMATTER_FEATURES = frozenset({"format_xlsx", "format_csv", "format_ics", "format_spond", "format_html"})


def formatter_capabilities(adapter_id: str, *, features: frozenset[str] | None = None) -> PortCapabilities:
    unknown = (features or FORMATTER_FEATURES) - FORMATTER_FEATURES
    if unknown:
        raise ValueError(f"unknown formatter port features: {sorted(unknown)}")
    return PortCapabilities(adapter_id=adapter_id, features=frozenset(features or FORMATTER_FEATURES))


def validate_format(format: str) -> None:
    if Format(format) not in Format:
        raise PortError(PortErrorCode.NOT_FOUND, f"invalid format: {format!r}")


#
# Formatter adapter functions that adapt the canonical SeasonPlan projection
# to each output format. These satisfy the common formatter port protocol.
#


def format_xlsx_season_plan(plan: Any) -> FormatterResult:
    """Adapt a SeasonPlan projection to XLSX format.

    In a full implementation this would use XLSX libraries to write an
    Excel workbook with tournament schedule, team assignments, and
    ice-time allocations. For now returns a placeholder content string.
    """
    # Placeholder: in full implementation, use openpyxl/xlswriter to write
    # the plan to an XLSX workbook
    content = f"XLSX export of season plan with {len(plan.tournaments) if hasattr(plan, 'tournaments') else 0} tournaments"
    return FormatterResult(
        format=Format.XLSX,
        content=content,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_name="season_schedule.xlsx",
    )


def format_csv_season_plan(plan: Any) -> FormatterResult:
    """Adapt a SeasonPlan projection to CSV format.

    Writes games CSV and overview CSV similar to the existing CsvExporter.
    """
    # Build games rows
    games_rows = []
    if hasattr(plan, 'tournaments'):
        for t in plan.tournaments:
            for team in getattr(t, 'teams', []):
                games_rows.append({
                    'date': str(getattr(t, 'date', '')),
                    'arena': getattr(t, 'arena', ''),
                    'age_group': getattr(t, 'age_group', ''),
                    'home': '',
                    'away': '',
                    'parallel_slot': '',
                })

    # Build overview rows
    overview_rows = []
    if hasattr(plan, 'tournaments'):
        tournament_counts = {}
        for t in plan.tournaments:
            key = getattr(t, 'name', 'unknown')
            tournament_counts[key] = tournament_counts.get(key, 0) + 1

    content = ""
    if games_rows:
        headers = "date,arena,age_group,home,away,parallel_slot\n"
        # Simplified CSV generation
        lines = [headers]
        for row in games_rows:
            line = ",".join(str(row.get(h, "")) for h in ["date", "arena", "age_group", "home", "away", "parallel_slot"])
            lines.append(line)
        content += "\n".join(lines)

    if overview_rows:
        content += "\n---\n"
        for key, count in tournament_counts.items():
            content += f"{key},{count}\n"

    return FormatterResult(
        format=Format.CSV,
        content=content,
        mime_type="text/csv",
        file_name="season_schedule.csv",
    )


def format_ics_season_plan(plan: Any) -> FormatterResult:
    """Adapt a SeasonPlan projection to ICS format (iCalendar)."""
    # Generate iCalendar events from the season plan
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Hockey Season//EN"]
    if hasattr(plan, 'tournaments'):
        for t in plan.tournaments:
            date_str = str(getattr(t, 'date', ''))
            lines.append("BEGIN:VEVENT")
            lines.append(f"DTSTART;VALUE=DATE:{date_str.replace('-', '')}")
            tname = getattr(t, 'name', 'Unnamed Tournament')
            lines.append(f"SUMMARY:{tname}")
            lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    content = "\n".join(lines)
    return FormatterResult(
        format=Format.ICS,
        content=content,
        mime_type="text/calendar",
        file_name="season_schedule.ics",
    )


def format_spond_season_plan(plan: Any) -> FormatterResult:
    """Adapt a SeasonPlan projection to Spond format."""
    # Spond is a Norwegian sports app format; generate simplified event listing
    events = []
    if hasattr(plan, 'tournaments'):
        for t in plan.tournaments:
            date_str = str(getattr(t, 'date', ''))
            tname = getattr(t, 'name', 'Unnamed')
            events.append(f"{date_str} | {tname}")
    content = "\n".join(events) if events else "No tournaments scheduled"
    return FormatterResult(
        format=Format.Spond,
        content=content,
        mime_type="application/text",
        file_name="season_schedule.spond",
    )


def format_html_season_plan(plan: Any) -> FormatterResult:
    """Adapt a SeasonPlan projection to HTML format."""
    # Generate a simple HTML report
    tournament_count = len(plan.tournaments) if hasattr(plan, 'tournaments') else 0
    content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Hockey Season Schedule</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        h1 {{ color: #333; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #f2f2f2; }}
    </style>
</head>
<body>
    <h1>Hockey Season Schedule</h1>
    <p>Total tournaments: {tournament_count}</p>
    <table>
        <tr>
            <th>Date</th>
            <th>Tournament</th>
            <th>Arena</th>
            <th>Age Group</th>
        </tr>
"""
    if hasattr(plan, 'tournaments'):
        for t in plan.tournaments:
            date_str = str(getattr(t, 'date', ''))
            tname = getattr(t, 'name', 'Unnamed')
            arena = getattr(t, 'arena', '')
            age_group = getattr(t, 'age_group', '')
            content += f"        <tr><td>{date_str}</td><td>{tname}</td><td>{arena}</td><td>{age_group}</td></tr>\n"
    content += """
    </table>
    <p>Generated from canonical season projection.</p>
</body>
</html>"""
    return FormatterResult(
        format=Format.HTML,
        content=content,
        mime_type="text/html",
        file_name="season_schedule.html",
    )
