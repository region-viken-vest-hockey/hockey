"""Per-run log file and stage-resume helpers."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

from ...pipeline.run_log_paths import resolve_active_run_log_dir
from ._shared import _console

def _resolve_run_log_dir(args: argparse.Namespace, state: "Any", start_time: datetime) -> Path:
    """Return the export folder where the current run log should live."""
    export_dir = Path(getattr(args, "export_dir", "export"))
    if not export_dir.is_absolute():
        export_dir = Path.cwd() / export_dir
    if getattr(args, "timestamped_export", True):
        export_dir = export_dir / start_time.strftime("%Y-%m-%dT%H%M")
    return resolve_active_run_log_dir(state, preferred_export_dir=export_dir)


def _archive_structured_run_log(work_dir: str, export_log_dir: Path) -> None:
    """Move the JSONL run log into the export folder when it exists."""
    try:
        from ...pipeline.run_manifest import RunManifest

        run_id = (RunManifest(work_dir).read().get("run_id") or "").strip()
        if not run_id:
            return
        source_dir = Path(work_dir) / "logs"
        source = source_dir / f"{run_id}.jsonl"
        if not source.exists():
            return
        export_log_dir.mkdir(parents=True, exist_ok=True)
        target = export_log_dir / source.name
        if target.exists():
            target.unlink()
        source.replace(target)
    except Exception:
        pass


def _write_run_log(
    args: argparse.Namespace,
    state: "Any",
    start_time: datetime,
    lines: list[str],
    *,
    success: bool,
) -> None:
    """Write a per-run log file into the export folder."""
    log_dir = _resolve_run_log_dir(args, state, start_time)
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = start_time.strftime("%Y%m%d_%H%M%S")
    status = "OK" if success else "FAILED"
    filename = f"pipeline_run_{timestamp}_{status}.log"
    log_path = log_dir / filename

    content = "# Pipeline run log\n"
    content += f"# Started: {start_time.isoformat()}\n"
    content += f"# Status: {'SUCCESS' if success else 'FAILED'}\n\n"
    for line in lines:
        content += line + "\n"

    log_path.write_text(content, encoding="utf-8")
    _archive_structured_run_log(args.work_dir, log_dir)
    _console.print(f"[dim]Run log saved: {log_path}[/dim]")


def _resolve_resume_stage(value: str | int | None) -> int:
    mapping = {
        "1": 1, "config": 1, "stage1": 1,
        "2": 2, "scraping": 2, "stage2": 2,
        "3": 3, "planning": 3, "plan": 3, "stage3": 3,
        "4": 4, "export": 4, "stage4": 4,
    }
    if value is None:
        return 1
    return mapping.get(str(value).lower(), 1)


def _force_refresh_stage2_inputs(work_dir: str) -> None:
    from ...pipeline.cache_manager import ScrapedDataCache
    from ...utils.calendar_cache import CalendarCache

    CalendarCache(work_dir=work_dir).clear()
    ScrapedDataCache(work_dir=work_dir).force_refresh()
