"""Placeholder export surface written when planning hasn't started yet."""

from __future__ import annotations

from pathlib import Path

from .not_started import render_not_started_html


def _write_not_started_exports(primary_export_path: Path, basename: str, message: str) -> dict[str, str]:
    """Write the normal export surface as small placeholder files."""
    import openpyxl

    primary_export_path.mkdir(parents=True, exist_ok=True)
    output_files: dict[str, str] = {}

    def _write_workbook(path: Path, title: str) -> None:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = title[:31]
        ws.append([message])
        wb.save(path)

    html = render_not_started_html(message)

    excel_path = primary_export_path / f"{basename}.xlsx"
    _write_workbook(excel_path, "Ikke begynt")
    output_files["excel"] = str(excel_path)

    ical_path = primary_export_path / f"{basename}.ics"
    ical_path.write_text(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//RVV Miniputt//Not Started//NO\r\n"
        "X-WR-CALNAME:Ikke begynt\r\nEND:VCALENDAR\r\n",
        encoding="utf-8",
    )
    output_files["ical"] = str(ical_path)

    csv_path = primary_export_path / f"{basename}.csv"
    csv_path.write_text(f"status\n{message}\n", encoding="utf-8")
    output_files["csv_games"] = str(csv_path)

    overview_path = primary_export_path / f"{basename}_overview.csv"
    overview_path.write_text(f"status\n{message}\n", encoding="utf-8")
    output_files["csv_overview"] = str(overview_path)

    for key, filename in (
        ("input_html", "input.html"),
        ("calendars_html", "calendars.html"),
        ("html", f"{basename}.html"),
        ("html_report", f"{basename}_report.html"),
    ):
        path = primary_export_path / filename
        path.write_text(html, encoding="utf-8")
        output_files[key] = str(path)

    spond_path = primary_export_path / f"{basename}_spond.xlsx"
    _write_workbook(spond_path, "Ikke begynt")
    output_files["spond"] = str(spond_path)

    spond_games_path = primary_export_path / f"{basename}_spond_games.xlsx"
    _write_workbook(spond_games_path, "Ikke begynt")
    output_files["spond_games"] = str(spond_games_path)

    review_dir = primary_export_path / "review_packets"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "README.txt").write_text(message + "\n", encoding="utf-8")
    output_files["review_packets"] = str(review_dir)

    return output_files
