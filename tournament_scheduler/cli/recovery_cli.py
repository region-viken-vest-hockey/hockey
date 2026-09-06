"""CLI commands for inspecting and recovering Stage 2 blocked/zero-event sources."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _cmd_recovery_targets(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt recovery-targets``.

    Reads the Stage 2 scraping checkpoint and prints a JSON array of sources
    that are either blocked or returned zero events.  Each entry has the shape::

        {"name": "Tønsberg", "url": "https://...", "reason": "blocked|zero_events",
         "block_reason": "..." | null, "llm_fallback": true|false}

    This output is intended for consumption by the harness agent when deciding
    whether to trigger LLM-assisted scraping for specific sources.
    """
    work_dir = Path(args.work_dir)
    checkpoint_path = work_dir / "stage2_scraping.json"

    if not checkpoint_path.exists():
        print(
            json.dumps({"error": f"Stage 2 checkpoint not found: {checkpoint_path}"}),
            file=sys.stderr,
        )
        return 1

    with checkpoint_path.open() as f:
        raw = json.load(f)

    # Checkpoint may wrap data under a "data" key (PipelineState format)
    data = raw.get("data", raw)
    sources: list[dict] = data.get("sources", [])

    targets = []
    for source in sources:
        name = source.get("name", "")
        url = source.get("url", "")
        blocked = source.get("blocked", False)
        event_count = source.get("event_count", 0)
        block_reason = source.get("block_reason") or None
        llm_fallback = source.get("llm_fallback", False)
        skipped = source.get("skipped", False)

        if skipped:
            continue

        if blocked:
            targets.append({
                "name": name,
                "url": url,
                "reason": "blocked",
                "block_reason": block_reason,
                "llm_fallback": llm_fallback,
            })
        elif event_count == 0:
            targets.append({
                "name": name,
                "url": url,
                "reason": "zero_events",
                "block_reason": None,
                "llm_fallback": llm_fallback,
            })

    print(json.dumps(targets, ensure_ascii=False, indent=2))
    return 0


def _cmd_scrape_merge(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt scrape-merge``.

    Rebuilds the Stage 2 checkpoint from the unified cache after recovery
    injection so the checkpoint reflects the recovered event counts, blocked
    state, and date range again.
    """
    from ..pipeline.recovery_injector import normalize_stage2_checkpoint

    try:
        summary = normalize_stage2_checkpoint(args.work_dir)
    except FileNotFoundError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": f"Failed to normalize Stage 2 checkpoint: {exc}"}), file=sys.stderr)
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _cmd_recovery_inject(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt recovery-inject``.

    Reads a JSON event list from stdin and injects it into the unified cache
    for the named source, so that subsequent Stage 2 re-runs or Stage 3
    invocations pick up the recovered data without re-scraping.

    Example::

        echo '[{"title": "...", "start": "2025-01-04"}]' | \\
            rvv-miniputt recovery-inject --source "Tønsberg"
    """
    from ..pipeline.recovery_injector import inject_recovered_events

    try:
        raw = sys.stdin.read()
        events = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(
            json.dumps({"error": f"Invalid JSON on stdin: {exc}"}),
            file=sys.stderr,
        )
        return 1

    if not isinstance(events, list):
        print(
            json.dumps({"error": "stdin must be a JSON array of event objects"}),
            file=sys.stderr,
        )
        return 1

    inject_recovered_events(
        source_name=args.source,
        events=events,
        work_dir=args.work_dir,
    )

    print(
        json.dumps(
            {"injected": len(events), "source": args.source, "work_dir": args.work_dir},
            ensure_ascii=False,
        )
    )
    return 0
