"""Archive the latest structured run log into the export folder when needed."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Sequence

from .args import build_parser
from ..pipeline.run_log_paths import resolve_active_run_log_dir
from ..pipeline.state import PipelineState


def _latest_jsonl(log_dir: Path) -> Path | None:
    candidates = sorted(log_dir.glob("run-*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _latest_export_run_dir(export_dir: Path) -> Path | None:
    if not export_dir.exists():
        return None

    candidates = sorted(
        (path.parent for path in export_dir.rglob("season_plan.xlsx") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0]

    child_dirs = sorted((path for path in export_dir.iterdir() if path.is_dir()), key=lambda path: path.stat().st_mtime, reverse=True)
    return child_dirs[0] if child_dirs else export_dir


def _latest_export_run_log(export_dir: Path) -> Path | None:
    if not export_dir.exists():
        return None
    candidates = sorted(
        export_dir.rglob("run-*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _copy_run_log(run_log: Path, targets: Sequence[Path]) -> list[Path]:
    copied: list[Path] = []
    for target_dir in targets:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / run_log.name
        shutil.copy2(run_log, target)
        copied.append(target)
    return copied


def archive_latest_run_log(argv: Sequence[str] | None = None) -> list[Path]:
    """Copy the newest structured run log into the export folder for run commands."""
    args = build_parser().parse_args(list(argv or []))

    is_run = getattr(args, "command", None) == "run"
    is_operator_run = getattr(args, "command", None) == "operator" and getattr(args, "operator_command", None) == "run"
    if not (is_run or is_operator_run):
        return []

    work_dir = Path(getattr(args, "work_dir", ".pipeline"))
    log_dir = work_dir / "logs"
    run_log = _latest_jsonl(log_dir)
    if not run_log:
        return []

    export_dir = Path(getattr(args, "export_dir", "export"))
    export_root = export_dir if export_dir.is_absolute() else Path.cwd() / export_dir
    latest_export_log = _latest_export_run_log(export_root)
    if latest_export_log and latest_export_log.stat().st_mtime >= run_log.stat().st_mtime:
        return []

    target_dir = resolve_active_run_log_dir(
        PipelineState(work_dir),
        preferred_export_dir=_latest_export_run_dir(export_root) or export_root,
    )
    targets = [target_dir]
    if target_dir != export_root:
        targets.append(export_root)

    return _copy_run_log(run_log, targets)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        copied = archive_latest_run_log(argv if argv is not None else sys.argv[1:])
        if copied:
            print(f"Archived run log: {', '.join(str(path) for path in copied)}")
        return 0
    except Exception as exc:  # pragma: no cover - best-effort posthook
        print(f"[warn] Could not archive run log: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
