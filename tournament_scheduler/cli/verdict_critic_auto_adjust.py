"""
Verdict, critic, and auto-adjust command implementations for the RVV Miniputt CLI.

This module contains the implementations of the `rvv-miniputt verdict`,
`rvv-miniputt critic`, and `rvv-miniputt auto-adjust` commands.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from rich.console import Console

# Module-level console that can be overridden for testing.
# The rvv_cli module will inject its console instance at import time.
_console: Console | None = None


def _get_console() -> Console:
    """Get the console instance, creating a default if needed.
    
    Prefers the console from rvv_cli if available (for test patching),
    otherwise falls back to module-level console or creates a new one.
    """
    global _console
    # Try to get console from rvv_cli (which tests patch)
    try:
        from .rvv_cli import _console as rvv_console
        if rvv_console is not None:
            return rvv_console
    except ImportError:
        pass
    if _console is None:
        _console = Console()
    return _console


def _cmd_verdict(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt verdict`` — analyze plan and emit structured verdict."""
    from ..pipeline.state import PipelineState, StageName

    work_dir = getattr(args, "work_dir", ".pipeline")
    state = PipelineState(work_dir)

    # Load the plan from the pipeline state
    checkpoint = state.read_stage(StageName.PLANNING)
    if not isinstance(checkpoint, dict) or not checkpoint:
        _get_console().print("[red]✗[/red] No plan checkpoint found in pipeline state")
        raise SystemExit(1)

    plan_obj = checkpoint.get("plan")
    if plan_obj is None:
        _get_console().print("[red]✗[/red] Plan checkpoint missing 'plan' key")
        raise SystemExit(1)

    # Handle both dict and SeasonPlan objects
    if hasattr(plan_obj, "pairwise_matchup_score"):
        # It's a SeasonPlan object
        plan = plan_obj
    else:
        # It's a dict, convert to SeasonPlan
        from ..serialization.season_plan import season_plan_from_dict
        plan = season_plan_from_dict(plan_obj)

    # Compute tone based on plan scores (matching html.renderers.judgment._score_tone)
    pairwise = getattr(plan, "pairwise_matchup_score", 1.0)
    diversity = getattr(plan, "diversity_score", 1.0)
    month_balance = getattr(plan, "month_balance_score", 1.0)
    fairness_gate = getattr(plan, "fairness_gate", {"status": "pass", "score": 100})
    gate_status = str(fairness_gate.get("status", "pass")).lower()
    gate_score = int(fairness_gate.get("score", 0) or 0)
    # Note: spread and missing_hosts not available in verdict CLI, default to 0/[]
    spread = 0
    missing_hosts: list[str] = []

    # Determine tone (matching _score_tone logic)
    if gate_status == "fail" or gate_score < 70 or pairwise < 0.75 or spread >= 5:
        tone = "rough"
    elif gate_status == "warn" or missing_hosts or pairwise < 0.9 or diversity < 0.9 or month_balance < 0.9 or spread >= 3:
        tone = "mixed"
    else:
        tone = "strong"

    # Tone label
    tone_labels = {
        "strong": "SOLID",
        "mixed": "BLANDET",
        "rough": "IKKE KLAR",
    }
    tone_label = tone_labels.get(tone, "Unknown")

    # Verdict text
    if fairness_gate.get("status") == "pass":
        verdict_text = "Plan passes fairness gate"
    else:
        verdict_text = "Plan fails fairness gate"

    # Action text
    if tone == "strong":
        action_text = "Proceed to export"
    elif tone == "mixed":
        action_text = "Review and consider adjustments before export"
    else:
        action_text = "Significant rework recommended before export"

    # Output structured verdict
    _get_console().print(f"tone={tone}")
    _get_console().print(f"tone_label={tone_label}")
    _get_console().print(f"pairwise_matchup_score={pairwise:.4f}")
    _get_console().print(f"verdict={verdict_text}")
    _get_console().print(f"action_text={action_text}")

    return 0


def _cmd_critic(args: argparse.Namespace) -> int:
    """Handle ``rvv-miniputt critic`` — run critic analysis."""
    from ..pipeline.state import PipelineState
    from ..plan_critic import main as run_plan_critic

    state = PipelineState(args.work_dir)
    result = run_plan_critic(
        season=args.season,
        root=args.root,
        out_dir=args.out_dir,
    )
    if args.json:
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if result.get("success"):
            _get_console().print("[green]✓[/green] Critic analysis completed")
            if result.get("issues_found"):
                _get_console().print(f"  issues found: {result['issues_found']}")
                if result.get("critical_issues"):
                    _get_console().print(f"  critical issues: {result['critical_issues']}")
        else:
            _get_console().print(f"[red]✗[/red] Critic analysis failed: {result.get('error')}")
    return 0


