"""
checkpoint_printer — pretty-print a pipeline stage checkpoint in compact human-readable form.

Usage:
    python3 -m tournament_scheduler.cli.checkpoint_printer <stage> [--work-dir .pipeline]

Arguments:
    stage       Stage name or number: stage1, stage2, stage3, stage4, 1, 2, 3, 4,
                config, scraping, planning, export

Options:
    --work-dir  Pipeline work directory (default: .pipeline)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_STAGE_ALIASES: dict[str, str] = {
    "1": "stage1_config",
    "2": "stage2_scraping",
    "3": "stage3_planning",
    "4": "stage4_export",
    "stage1": "stage1_config",
    "stage2": "stage2_scraping",
    "stage3": "stage3_planning",
    "stage4": "stage4_export",
    "config": "stage1_config",
    "scraping": "stage2_scraping",
    "planning": "stage3_planning",
    "export": "stage4_export",
}

_STAGE_LABELS = {
    "stage1_config": "Stage 1 — Config",
    "stage2_scraping": "Stage 2 — Scraping",
    "stage3_planning": "Stage 3 — Planning",
    "stage4_export": "Stage 4 — Export",
}

_STAGE_SUMMARY_KEYS: dict[str, list[str]] = {
    "stage1_config": ["input_path", "teams", "round_length_minutes", "participation_targets_by_age_group"],
    "stage2_scraping": ["sources", "blocked", "empty_sources", "cached", "event_expectation_warnings"],
    "stage3_planning": ["plan", "rules_report"],
    "stage4_export": ["generated_at", "input_path", "output_files", "errors"],
}


def _resolve_stage(alias: str) -> str:
    key = alias.lower().strip()
    if key not in _STAGE_ALIASES:
        print(
            f"Unknown stage '{alias}'. Valid values: 1-4, stage1-stage4, config, scraping, planning, export",
            file=sys.stderr,
        )
        sys.exit(1)
    return _STAGE_ALIASES[key]


def _compact_value(value: object, max_items: int = 5) -> str:
    """Return a compact representation of a value."""
    if isinstance(value, list):
        length = len(value)
        if length == 0:
            return "[] (empty)"
        if length <= max_items:
            # Try to show names if items are dicts with a 'name' key
            names = []
            for item in value:
                if isinstance(item, dict):
                    label = item.get("name") or item.get("club") or item.get("id") or item.get("date")
                    if label:
                        names.append(str(label))
                elif isinstance(item, str):
                    names.append(item)
            if names:
                return f"[{length}] {', '.join(names)}"
        sample_names: list[str] = []
        for item in value[:max_items]:
            if isinstance(item, dict):
                label = item.get("name") or item.get("club") or item.get("id") or item.get("date")
                if label:
                    sample_names.append(str(label))
            elif isinstance(item, str):
                sample_names.append(item)
        suffix = f", ... (+{length - max_items} more)" if length > max_items else ""
        if sample_names:
            return f"[{length}] {', '.join(sample_names)}{suffix}"
        return f"[{length} items]"
    if isinstance(value, dict):
        return f"{{...}} ({len(value)} keys: {', '.join(list(value.keys())[:5])})"
    return str(value)


def print_checkpoint(stage_file: str, work_dir: Path) -> None:
    checkpoint_path = work_dir / f"{stage_file}.json"
    label = _STAGE_LABELS.get(stage_file, stage_file)

    if not checkpoint_path.exists():
        print(f"{label}: checkpoint not found at {checkpoint_path}", file=sys.stderr)
        sys.exit(1)

    try:
        raw = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Failed to parse {checkpoint_path}: {exc}", file=sys.stderr)
        sys.exit(1)

    stage_val = raw.get("stage", "?")
    status = raw.get("status", "?")
    updated_at = raw.get("updated_at", "?")
    data = raw.get("data") or {}

    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    print(f"  stage      : {stage_val}")
    print(f"  status     : {status}")
    print(f"  updated_at : {updated_at}")

    summary_keys = _STAGE_SUMMARY_KEYS.get(stage_file, list(data.keys())[:8])
    if data:
        print()
        print("  --- data summary ---")
        all_keys = list(data.keys())
        for key in summary_keys:
            if key in data:
                print(f"  {key:<28}: {_compact_value(data[key])}")
        extra_keys = [k for k in all_keys if k not in summary_keys]
        if extra_keys:
            print(f"  {'(other keys)':<28}: {', '.join(extra_keys)}")
    else:
        print("  (no data)")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pretty-print a pipeline stage checkpoint in compact human-readable form."
    )
    parser.add_argument(
        "stage",
        help="Stage name or number: 1-4, stage1-stage4, config, scraping, planning, export",
    )
    parser.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    args = parser.parse_args()

    stage_file = _resolve_stage(args.stage)
    work_dir = Path(args.work_dir)
    print_checkpoint(stage_file, work_dir)


if __name__ == "__main__":
    main()
