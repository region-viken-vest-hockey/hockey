"""
``rvv-miniputt`` — unified CLI for the RVV Miniputt tournament scheduler pipeline.

Provides the commands referenced by the HTML calendar viewer, scraper tools,
and pipeline logs::

    rvv-miniputt status                 Show checkpoint/log status
    rvv-miniputt calendars              Regenerate calendar HTML from cache
    rvv-miniputt calendars --refresh    Full re-scrape: clear caches, scrape, regenerate
    rvv-miniputt run                    Full pipeline: stages 1→4 + HTML views
    rvv-miniputt logs                   Show structured pipeline run logs
    rvv-miniputt cancel                 Cancel a tournament and suggest/reschedule makeup dates
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from ..pipeline.state import PipelineState

from rich.console import Console

from ..application.operator_state import (
    check_operator_health,
    list_operator_questions,
    promote_operator_question,
    record_operator_answer,
)
from ..application.canonical_season_service import CanonicalSeasonService
from .args import build_parser as _build_parser
from .pipeline_orchestrator import (
    _cmd_calendars, _cmd_operator_audit_context, _cmd_operator_audit_evidence,
    _cmd_operator_audit_run, _cmd_operator_audit_submit,
    _cmd_operator_publish,
    _cmd_operator_publish_history,
    _cmd_operator_rollback,
    _cmd_operator_run,
    _cmd_operator_verify,
    _execute_operator_publish,
    _print_pages_result,
    _cmd_run,
    _cmd_scrape,
)
from .plan_command import _cmd_plan
from .recovery_cli import _cmd_recovery_inject, _cmd_recovery_targets, _cmd_scrape_merge
from .waiver_command import _cmd_waiver
from .reporting import _cmd_candidates, _cmd_logs, _cmd_sources_status, _cmd_status
from .season_command import _cmd_season
from .verification_problem import _canonical_verification_problem  # noqa: F401 (re-exported for CLI callers)
from .cancel_command import _cmd_cancel
from .registered_teams_command import _cmd_registered_teams
from .activities_command import _cmd_activities
from .operator_subcommands import _cmd_operator_questions, _cmd_operator_answer, _cmd_operator_promote, _cmd_operator_health
from .sources_registrations_command import _cmd_sources, _cmd_registrations
from .tournament_commands import _cmd_tournament, _cmd_tournament_list, _cmd_tournament_add, _cmd_tournament_remove
from .plan_adjustment_commands import _cmd_replan, _cmd_adjust, _cmd_auto_adjust
from .review_scrape_commands import _cmd_review, _cmd_scrape_llm
from .verdict_critic_auto_adjust import _cmd_verdict, _cmd_critic
from . import verdict_critic_auto_adjust as _vca

_console = Console()

# Inject console into verdict_critic_auto_adjust for shared output capture in tests
_vca._console = _console

# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def _format_delta(delta: dict | None) -> str:
    """Compact before/after summary for a season maintenance action."""
    if not delta:
        return ""
    parts = [
        f"endrede turneringer: {delta.get('changed_tournament_count', 0)}",
        f"hard-feil: {delta.get('hard_violations_before', 0)} -> {delta.get('hard_violations_after', 0)}",
        (
            "uoppfylte hostingkrav: "
            f"{delta.get('unresolved_hosting_obligations_before', 0)} -> "
            f"{delta.get('unresolved_hosting_obligations_after', 0)}"
        ),
        (
            "hostingbalanse-avvik: "
            f"{delta.get('hosting_balance_imbalances_before', 0)} -> "
            f"{delta.get('hosting_balance_imbalances_after', 0)}"
        ),
        (
            "deltakelsesavvik: "
            f"{delta.get('participation_deviations_before', 0)} -> "
            f"{delta.get('participation_deviations_after', 0)}"
        ),
        (
            "reise (km): "
            f"{delta.get('total_travel_km_before', 0):.0f} -> "
            f"{delta.get('total_travel_km_after', 0):.0f} "
            f"({delta.get('total_travel_km_delta', 0):+.0f})"
        ),
        (
            "kvalitetsregresjoner: "
            f"{len(delta.get('quality_regressions') or [])}"
        ),
    ]
    return "; ".join(parts)


def _cmd_operator(args: argparse.Namespace) -> int:
    """Dispatch ``rvv-miniputt operator <subcommand>`` to its handler."""
    subcommand = getattr(args, "operator_command", None)
    handlers = {
        "run": _cmd_operator_run,
        "questions": _cmd_operator_questions,
        "answer": _cmd_operator_answer,
        "promote": _cmd_operator_promote,
        "health": _cmd_operator_health,
        "publish": _cmd_operator_publish,
        "verify": _cmd_operator_verify,
        "rollback": _cmd_operator_rollback,
        "publish-history": _cmd_operator_publish_history,
        "audit-context": _cmd_operator_audit_context,
        "audit-evidence": _cmd_operator_audit_evidence,
        "audit-submit": _cmd_operator_audit_submit,
        "audit-run": _cmd_operator_audit_run,
    }
    handler = handlers.get(subcommand)
    if handler is None:
        _console.print("[red]✗[/red] Mangler operator-underkommando (kjør 'rvv-miniputt operator --help')")
        return 1
    return handler(args)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``rvv-miniputt`` console script."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "status":
        return _cmd_status(args)
    elif args.command == "calendars":
        return _cmd_calendars(args)
    elif args.command == "activities":
        return _cmd_activities(args)
    elif args.command == "registered-teams":
        return _cmd_registered_teams(args)
    elif args.command == "run":
        return _cmd_run(args)
    elif args.command == "operator":
        return _cmd_operator(args)
    elif args.command == "sources":
        return _cmd_sources(args)
    elif args.command == "registrations":
        return _cmd_registrations(args)
    elif args.command == "logs":
        return _cmd_logs(args)
    elif args.command == "cancel":
        return _cmd_cancel(args)
    elif args.command == "replan":
        return _cmd_replan(args)
    elif args.command == "adjust":
        return _cmd_adjust(args)
    elif args.command == "review":
        return _cmd_review(args)
    elif args.command == "tournament":
        return _cmd_tournament(args)
    elif args.command == "scrape":
        return _cmd_scrape(args)
    elif args.command == "scrape-llm":
        return _cmd_scrape_llm(args)
    elif args.command == "recovery-targets":
        return _cmd_recovery_targets(args)
    elif args.command == "recovery-inject":
        return _cmd_recovery_inject(args)
    elif args.command == "scrape-merge":
        return _cmd_scrape_merge(args)
    elif args.command == "critic":
        return _cmd_critic(args)
    elif args.command == "auto-adjust":
        return _cmd_auto_adjust(args)
    elif args.command == "verdict":
        return _cmd_verdict(args)
    elif args.command == "candidates":
        return _cmd_candidates(args)
    elif args.command == "plan":
        return _cmd_plan(args)
    elif args.command == "season":
        return _cmd_season(args)
    elif args.command == "stage3":
        from .pipeline_orchestrator.stage3_session_command import _cmd_stage3_session

        if getattr(args, "stage3_command", None) == "refine":
            from .pipeline_orchestrator.stage3_refine_command import _cmd_stage3_refine

            return _cmd_stage3_refine(args)
        if getattr(args, "stage3_command", None) == "converge":
            from .pipeline_orchestrator.stage3_converge_command import _cmd_stage3_converge

            return _cmd_stage3_converge(args)
        if getattr(args, "stage3_command", None) == "adopt":
            from .pipeline_orchestrator.stage3_converge_command import _cmd_stage3_adopt

            return _cmd_stage3_adopt(args)
        return _cmd_stage3_session(args)
    elif args.command == "waiver":
        return _cmd_waiver(args)
    elif args.command == "export-parity":
        from .export_parity_command import _cmd_export_parity

        return _cmd_export_parity(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
