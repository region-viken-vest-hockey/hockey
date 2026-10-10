"""Season replan, refresh, diagnostics, blockers, audit and infeasibility commands."""

from __future__ import annotations

import argparse

from .common import add_operational_opt_in_flags


def add_season_review_parsers(season_sub: argparse._SubParsersAction) -> None:
    season_apply = season_sub.add_parser(
        "apply",
        help="Apply a verified baseline-aware replan candidate to canonical season state",
    )
    season_apply.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_apply.add_argument(
        "--candidate",
        required=True,
        help="Path to a candidate/Stage 3 checkpoint JSON to apply",
    )
    season_apply.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_apply.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
    season_apply.add_argument("--actor", default=None, help="Operator identity for the apply record")
    season_apply.add_argument("--json", action="store_true", help="Print schedule/decisions/change-cost as JSON")
    add_operational_opt_in_flags(season_apply)

    season_diff = season_sub.add_parser(
        "diff",
        help="Show the weighted change cost between canonical season state and a candidate",
    )
    season_diff.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_diff.add_argument(
        "--candidate",
        required=True,
        help="Path to a candidate/Stage 3 checkpoint JSON to compare",
    )
    season_diff.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_diff.add_argument("--json", action="store_true", help="Print change cost as JSON")

    season_replan = season_sub.add_parser(
        "replan",
        help="Bounded replan around promoted canonical state, preserving approved/locked tournaments",
    )
    season_replan.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_replan.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_replan.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for config/checkpoint")
    season_replan.add_argument("--engine", default="local_search", help="Planner engine (local_search or cp_sat)")
    season_replan.add_argument("--iterations", type=int, default=4000, help="Search iterations (local_search)")
    season_replan.add_argument("--seed", type=int, default=0, help="Search seed")
    season_replan.add_argument("--move-dates", action="store_true", help="Allow swapping tournament dates")
    season_replan.add_argument("--move-hosts", action="store_true", help="Allow reassigning host clubs")
    season_replan.add_argument("--move-slots", action="store_true", help="Allow reassigning start times")
    season_replan.add_argument("--apply", action="store_true", help="Apply the verified result to canonical state")
    season_replan.add_argument("--json", action="store_true", help="Print the replan result as JSON")
    add_operational_opt_in_flags(season_replan)

    season_refresh = season_sub.add_parser(
        "refresh-calendars",
        help="Refresh promoted-season calendar evidence from a fresh Stage 2 scrape without moving tournaments",
    )
    season_refresh.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_refresh.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_refresh.add_argument("--input", default="input.xlsx", help="Canonical input workbook with configured sources")
    season_refresh.add_argument("--work-dir", default=None, help="Optional work directory for the scrape checkpoint/cache")
    season_refresh.add_argument("--actor", default=None, help="Operator identity for provenance")
    season_refresh.add_argument("--note", default="", help="Refresh note for decisions history")
    season_refresh.add_argument("--dry-run", action="store_true", help="Preview the evidence refresh without writing canonical state")
    season_refresh.add_argument(
        "--allow-missing-sources",
        action="store_true",
        help="Record partial evidence even if some sources are blocked (default: fail safely)",
    )
    season_refresh.add_argument(
        "--accept-source-policy-change",
        action="store_true",
        dest="accept_source_policy_change",
        help=(
            "Explicitly advance the promoted source policy when the configured input.xlsx "
            "sources (URL, parser kind, trust/classification, coverage) changed since promotion"
        ),
    )
    season_refresh.add_argument("--json", action="store_true", help="Print refresh result as JSON")

    season_reconcile_config = season_sub.add_parser(
        "reconcile-config",
        help="Reconcile promoted-season config facts after a semantic contract change",
    )
    season_reconcile_config.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_reconcile_config.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_reconcile_config.add_argument("--input", default="input.xlsx", help="Canonical input workbook with the current config contract")
    season_reconcile_config.add_argument("--actor", default=None, help="Operator identity for provenance")
    season_reconcile_config.add_argument("--note", default="", help="Reconciliation note for decisions history")
    season_reconcile_config.add_argument("--dry-run", action="store_true", help="Preview the reconciliation without writing canonical state")
    season_reconcile_config.add_argument("--json", action="store_true", help="Print reconciliation result as JSON")

    season_blockers = season_sub.add_parser(
        "blockers",
        help="Report genuine publication blockers separately from accepted and diagnostic debt",
    )
    season_blockers.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_blockers.add_argument(
        "--root", default="season", help="Canonical season-state root (default: season)"
    )
    season_blockers.add_argument("--json", action="store_true", help="Print the stable blocker report as JSON")

    season_findings = season_sub.add_parser(
        "findings",
        help="List fresh, revision-bound actionable findings over canonical season state",
    )
    season_findings.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_findings.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_findings.add_argument("--json", action="store_true", help="Print findings as JSON")
    season_findings.add_argument(
        "--all",
        action="store_true",
        help="Show all known/accepted findings when a baseline exists (default emphasizes NEW/REGRESSED)",
    )

    season_audit = season_sub.add_parser(
        "audit",
        help="Run the catalog-driven season-wide completion gate (exhaustive coverage report)",
    )
    season_audit.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_audit.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_audit.add_argument("--json", action="store_true", help="Print the audit report as JSON")

    season_record_infeasibility = season_sub.add_parser(
        "record-infeasibility",
        help=(
            "Run the canonical bounded placement search and persist durable "
            "proven-infeasibility evidence for unplaced obligations"
        ),
    )
    season_record_infeasibility.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_record_infeasibility.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_record_infeasibility.add_argument(
        "--finding",
        action="append",
        default=[],
        help="Restrict to one obligation/finding id (repeatable); default all unplaced obligations",
    )
    season_record_infeasibility.add_argument(
        "--expected-revision",
        default=None,
        help="Refuse the write when the canonical revision no longer matches",
    )
    season_record_infeasibility.add_argument("--actor", default=None, help="Operator identity for provenance")
    season_record_infeasibility.add_argument("--note", default="", help="Why this proof is recorded")
    season_record_infeasibility.add_argument(
        "--dry-run", action="store_true", help="Run the search and report the proofs without writing"
    )
    season_record_infeasibility.add_argument("--json", action="store_true", help="Print the result as JSON")

    season_release_infeasibility = season_sub.add_parser(
        "release-infeasibility",
        help="Supersede recorded placement-infeasibility proofs so the obligations reopen",
    )
    season_release_infeasibility.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_release_infeasibility.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_release_infeasibility.add_argument(
        "--finding",
        action="append",
        default=[],
        help="Restrict to one obligation/finding id (repeatable); default all unplaced obligations",
    )
    season_release_infeasibility.add_argument("--actor", default=None, help="Operator identity for provenance")
    season_release_infeasibility.add_argument("--note", default="", help="Why the proofs are released")
    season_release_infeasibility.add_argument("--json", action="store_true", help="Print the result as JSON")

    season_infeasibility_report = season_sub.add_parser(
        "infeasibility-report",
        help="Show recorded placement-infeasibility proofs and whether they are still current",
    )
    season_infeasibility_report.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_infeasibility_report.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_infeasibility_report.add_argument(
        "--include-superseded", action="store_true", help="Include superseded proofs"
    )
    season_infeasibility_report.add_argument("--json", action="store_true", help="Print the report as JSON")

    season_baseline = season_sub.add_parser(
        "baseline",
        help="Create, advance or inspect the accepted season-quality baseline",
    )
    season_baseline_sub = season_baseline.add_subparsers(dest="baseline_command", title="baseline commands")
    season_baseline_create = season_baseline_sub.add_parser(
        "create", help="Accept the current non-hard finding set as the season baseline"
    )
    season_baseline_create.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_baseline_create.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_baseline_create.add_argument("--actor", default=None, help="Operator identity for baseline provenance")
    season_baseline_create.add_argument("--note", default="", help="Why this baseline is accepted")
    season_baseline_create.add_argument("--json", action="store_true", help="Print baseline result as JSON")
    season_baseline_show = season_baseline_sub.add_parser("show", help="Show baseline and current comparison")
    season_baseline_show.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_baseline_show.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_baseline_show.add_argument("--json", action="store_true", help="Print baseline result as JSON")
    season_baseline_advance = season_baseline_sub.add_parser(
        "advance", help="Tighten the baseline to the current equal-or-better state"
    )
    season_baseline_advance.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_baseline_advance.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_baseline_advance.add_argument("--actor", default=None, help="Operator identity for baseline provenance")
    season_baseline_advance.add_argument("--note", default="", help="Optional note for baseline advancement")
    season_baseline_advance.add_argument("--json", action="store_true", help="Print baseline result as JSON")
    season_baseline_replace = season_baseline_sub.add_parser(
        "replace",
        help=(
            "Explicitly rebaseline to the current state even when it is worse, recording the "
            "prior baseline as audit history"
        ),
    )
    season_baseline_replace.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_baseline_replace.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_baseline_replace.add_argument("--actor", default=None, help="Operator identity for baseline provenance")
    season_baseline_replace.add_argument("--note", default="", help="Why the worse state is deliberately accepted")
    season_baseline_replace.add_argument("--json", action="store_true", help="Print baseline result as JSON")
