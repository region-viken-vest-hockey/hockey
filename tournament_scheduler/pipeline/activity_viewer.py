"""Standalone public activity calendar page generation (issues #33/#38/#40/#91)."""

from __future__ import annotations

import json
from pathlib import Path

from tournament_scheduler.html.templates import ACTIVITY_VIEWER

from .activity_export import build_activities_payload


def generate_activity_artifacts(
    *,
    input_path: str,
    export_dir: str = "export",
    default_year: int | None = None,
    generated_at: str | None = None,
) -> dict[str, str] | None:
    """Write ``activities.json`` and ``activities/index.html`` for *input_path*.

    Returns ``None`` when the workbook has no supported activity table.
    """
    payload = build_activities_payload(input_path, default_year=default_year, generated_at=generated_at)
    if payload is None:
        return None

    export_path = Path(export_dir)
    export_path.mkdir(parents=True, exist_ok=True)
    json_path = export_path / "activities.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    html_path = Path(generate_html(export_dir=str(export_path)))
    return {"activities_json": str(json_path), "activities_html": str(html_path)}


def generate_html(*, export_dir: str = "export") -> str:
    """Write the standalone ``activities/index.html`` shell and return its path."""
    activities_dir = Path(export_dir) / "activities"
    activities_dir.mkdir(parents=True, exist_ok=True)
    html_path = activities_dir / "index.html"
    html_path.write_text(ACTIVITY_VIEWER, encoding="utf-8")
    return str(html_path)
