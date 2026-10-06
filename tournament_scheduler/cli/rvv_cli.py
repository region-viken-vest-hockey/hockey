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
from .cancel_command import _cmd_cancel
from .registered_teams_command import _cmd_registered_teams
from .activities_command import _cmd_activities
from .operator_command import _cmd_operator
from .operator_subcommands import _cmd_operator_questions, _cmd_operator_answer, _cmd_operator_promote, _cmd_operator_health
from .sources_registrations_command import _cmd_sources, _cmd_registrations
from .tournament_commands import _cmd_tournament, _cmd_tournament_list, _cmd_tournament_add, _cmd_tournament_remove
from .plan_adjustment_commands import _cmd_replan, _cmd_adjust
from .review_scrape_commands import _cmd_review, _cmd_scrape_llm
from .verdict_critic_auto_adjust import _cmd_verdict, _cmd_critic, _cmd_auto_adjust

_console = Console()

# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def _canonical_verification_problem(
    work_dir: str, season: str | None = None, root: str | None = None
) -> dict | None:
    """Best-effort full ``planning_problem`` for canonical approval/move gates.

    Reconstructs the same problem contract Stage 3 was given (including any
    canonical baseline locks and active operator waivers) so approving or
    moving a canonical tournament is checked against the real hard
    invariants, not only self-consistency.  When *season*/*root* are given,
    an unrelated ``--work-dir`` pipeline (a different season's config) is
    ignored rather than applied to this season.  A promoted canonical season
    owns the authoritative problem: once its schedule exists, any failure to
    load it or project its live overlays raises :class:`SeasonStateError`
    rather than silently degrading to self-consistency verification.  Only a
    genuinely absent canonical schedule (or one without a promoted problem)
    falls back to the pipeline reconstruction and may return ``None``.
    """
    from datetime import date as _date

    if season:
        from ..season_state import SeasonStateError, load_decisions, load_schedule, schedule_path

        season_root = root or "season"
        # Only a genuinely absent canonical schedule may fall through to the
        # pipeline reconstruction below. Once the schedule exists, a failure
        # to load it or project its live overlays must fail closed: verifying
        # against an incomplete or absent contract is worse than refusing the
        # mutation.
        if schedule_path(season, root=season_root).exists():
            schedule = load_schedule(season, root=season_root)
            context = schedule.get("verification_context") if isinstance(schedule, dict) else None
            problem = context.get("problem") if isinstance(context, dict) else None
            if isinstance(problem, dict) and problem:
                # The stored problem is frozen at the season's original
                # promotion; project the *current* canonical decisions into it
                # through the one shared overlay facade -- holiday exceptions,
                # banned dates, calendar-booking associations, ice-time
                # overrides and durable participation withdrawals -- mirroring
                # ``season findings``/``season export``, so approve/move/etc.
                # never refuse a mutation over a since-superseded fact or an
                # already-committed withdrawal, and never re-derive an
                # incomplete subset of the canonical overlays.
                from ..season_maintenance import project_canonical_overlays

                decisions = load_decisions(season, root=season_root)
                try:
                    return project_canonical_overlays(
                        problem,
                        decisions=decisions,
                        plan=schedule.get("plan") or {},
                    )
                except SeasonStateError:
                    raise
                except Exception as exc:
                    raise SeasonStateError(
                        f"Canonical season {season} verification overlay projection "
                        f"failed: {type(exc).__name__}: {exc}"
                    ) from exc

    from ..pipeline.stage1_config import load_effective_config
    from ..pipeline.stage4_export_verification import _build_export_verification_problem
    from ..pipeline.state import PipelineState

    state = PipelineState(work_dir)
    try:
        effective_config = load_effective_config(state)
    except Exception:
        effective_config = {}
    if not effective_config:
        return None
    if season:
        start_raw = effective_config.get("start_date")
        end_raw = effective_config.get("end_date")
        if not start_raw or not end_raw:
            return None
        try:
            from ..canonical_baseline import resolve_canonical_season

            resolved = resolve_canonical_season(
                effective_config,
                _date.fromisoformat(str(start_raw)),
                _date.fromisoformat(str(end_raw)),
                root=root,
            )
        except Exception:
            return None
        if resolved != season:
            return None
    return _build_export_verification_problem(effective_config, state)


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
