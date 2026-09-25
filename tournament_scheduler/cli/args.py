"""Argument parsing for the RVV Miniputt CLI."""

from __future__ import annotations

import argparse
from .args_audit import add_operator_audit_subparsers as _add_operator_audit_subparsers
from ..operator_waivers import WAIVABLE_RULE_IDS


MANUAL_PLACEMENT_OPT_IN_HELP = (
    "Explicitly allow this action to introduce a known manual/fixed-busy placement "
    "or unresolved placement work (provisional placement); not the default"
)
HOST_CONFIRMATION_OPT_IN_HELP = (
    "Explicitly allow a new host-confirmation dependency (movable_busy allocation) "
    "for this action; not the default"
)


def _add_operational_opt_in_flags(parser: argparse.ArgumentParser) -> None:
    """Add the shared explicit opt-ins for provisional/manual canonical placements."""

    parser.add_argument(
        "--allow-manual-placement",
        action="store_true",
        help=MANUAL_PLACEMENT_OPT_IN_HELP,
    )
    parser.add_argument(
        "--allow-host-confirmation",
        action="store_true",
        help=HOST_CONFIRMATION_OPT_IN_HELP,
    )


def _add_regression_acceptance_flags(parser: argparse.ArgumentParser) -> None:
    """Add the explicit operator acceptance of named team-schedule regressions."""

    parser.add_argument(
        "--accept-team-regression",
        dest="accept_team_regressions",
        action="append",
        default=None,
        metavar="TEAM=CODE",
        help=(
            "Explicitly accept one named material regression for one affected team, "
            "e.g. 'Sandefjord=more_gaps_under_7_days' (repeatable); only when the "
            "operator has accepted that exact trade-off; not the default"
        ),
    )
    parser.add_argument(
        "--accept-regression-reason",
        default=None,
        help="Mandatory operator reason recorded with any accepted team-schedule regression",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rvv-miniputt",
        description="RVV Miniputt — tournament scheduler pipeline CLI",
    )
    sub = parser.add_subparsers(dest="command", title="commands")

    # status
    status = sub.add_parser("status", help="Show checkpoint/log status for the pipeline work directory")
    status.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    status.add_argument(
        "--json",
        action="store_true",
        help="Print the AI-operator run manifest as JSON instead of the human-readable summary",
    )

    # sources
    sources = sub.add_parser("sources", help="Calendar source health commands")
    sources_sub = sources.add_subparsers(dest="sources_command")
    sources_status = sources_sub.add_parser(
        "status",
        help="Show per-source health: reachability, event counts, cache age, and suggested recovery actions",
    )
    sources_status.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    sources_status.add_argument(
        "--json",
        action="store_true",
        help="Print source health as a JSON array of capability results instead of a human-readable summary",
    )

    # calendars
    cal = sub.add_parser("calendars", help="Calendar viewer commands")
    cal.add_argument(
        "--refresh",
        action="store_true",
        help="Force full re-scrape: clear all caches, re-scrape, regenerate HTML",
    )
    cal.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )

    # registered-teams — standalone public Påmeldte lag page from SharePoint CSV
    registered = sub.add_parser(
        "registered-teams",
        help="Regenerate the public Påmeldte lag page from a SharePoint CSV and optionally publish it",
    )
    registered.add_argument(
        "--csv",
        required=True,
        help="SharePoint CSV export with columns club,label,age_group",
    )
    registered.add_argument(
        "--export-dir",
        default=".pipeline/registered_teams_publish_export",
        help="Staging export directory (default: .pipeline/registered_teams_publish_export)",
    )
    registered.add_argument(
        "--config",
        default="input.json",
        help="Optional JSON config with age_groups for validation (default: input.json if present)",
    )
    registered.add_argument(
        "--generated-at",
        default=None,
        help="Override generation timestamp, primarily for deterministic tests/previews",
    )
    registered.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory used for publish manifest/questions (default: .pipeline)",
    )
    registered.add_argument(
        "--repo-dir",
        default=".",
        help="Git repository directory to publish from/read gh-pages from (default: current directory)",
    )
    registered.add_argument(
        "--branch",
        default="gh-pages",
        help="Pages branch to update/read from (default: gh-pages)",
    )
    registered.add_argument(
        "--remote",
        default="origin",
        help="Git remote to fetch/push (default: origin)",
    )
    registered.add_argument(
        "--no-base-latest",
        dest="base_latest",
        action="store_false",
        help="Do not copy the current /latest/ snapshot before writing registered-team artifacts",
    )
    registered.set_defaults(base_latest=True)
    registered.add_argument(
        "--publish",
        action="store_true",
        help="Publish the staged full snapshot to GitHub Pages after generation",
    )
    registered.add_argument(
        "--run-id",
        default=None,
        help="Override immutable /runs/<run-id>/ id (default: registered-teams-<UTC timestamp>)",
    )
    registered.add_argument(
        "--no-push",
        dest="push",
        action="store_false",
        help="Commit the Pages branch locally but do not push to the remote",
    )
    registered.set_defaults(push=True)
    registered.add_argument(
        "--confirm-public",
        action="store_true",
        help="Explicitly authorize public publishing now; required for --publish to push without an approval question",
    )
    registered.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview publish changes and raise/refresh the approval question; never publish",
    )
    registered.add_argument(
        "--no-verify",
        dest="verify",
        action="store_false",
        help="Skip polling the published URL after a successful push",
    )
    registered.set_defaults(verify=True)
    registered.add_argument(
        "--verify-max-attempts",
        type=int,
        default=None,
        metavar="N",
        help="Bounded retry count for post-publish verification",
    )
    registered.add_argument(
        "--verify-retry-delay",
        dest="verify_retry_delay_seconds",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Delay between post-publish verification attempts",
    )
    registered.add_argument(
        "--json",
        action="store_true",
        help="Print publish result as JSON when --publish is used",
    )

    # activities — standalone public activity calendar from the year-wheel workbook
    activities = sub.add_parser(
        "activities",
        help="Regenerate the public activity calendar and optionally publish a full Pages snapshot",
    )
    activities.add_argument(
        "--input",
        default="Årshjul for aktiviteter.xlsx",
        help="Activity/year-wheel workbook (default: Årshjul for aktiviteter.xlsx)",
    )
    activities.add_argument(
        "--export-dir",
        default=".pipeline/activity_publish_export",
        help="Staging export directory (default: .pipeline/activity_publish_export)",
    )
    activities.add_argument(
        "--year",
        type=int,
        default=None,
        help="Default year for rows that only contain day/month",
    )
    activities.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory used for publish manifest/questions (default: .pipeline)",
    )
    activities.add_argument(
        "--repo-dir",
        default=".",
        help="Git repository directory to publish from/read gh-pages from (default: current directory)",
    )
    activities.add_argument(
        "--branch",
        default="gh-pages",
        help="Pages branch to update/read from (default: gh-pages)",
    )
    activities.add_argument(
        "--remote",
        default="origin",
        help="Git remote to fetch/push (default: origin)",
    )
    activities.add_argument(
        "--no-base-latest",
        dest="base_latest",
        action="store_false",
        help="Do not copy the current /latest/ snapshot before writing activities (unsafe for publish unless intentional)",
    )
    activities.set_defaults(base_latest=True)
    activities.add_argument(
        "--publish",
        action="store_true",
        help="Publish the staged full snapshot to GitHub Pages after generation",
    )
    activities.add_argument(
        "--run-id",
        default=None,
        help="Override immutable /runs/<run-id>/ id (default: activities-<UTC timestamp>)",
    )
    activities.add_argument(
        "--no-push",
        dest="push",
        action="store_false",
        help="Commit the Pages branch locally but do not push to the remote",
    )
    activities.set_defaults(push=True)
    activities.add_argument(
        "--confirm-public",
        action="store_true",
        help="Explicitly authorize public publishing now; required for --publish to push without an approval question",
    )
    activities.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview publish changes and raise/refresh the approval question; never publish",
    )
    activities.add_argument(
        "--no-verify",
        dest="verify",
        action="store_false",
        help="Skip polling the published URL after a successful push",
    )
    activities.set_defaults(verify=True)
    activities.add_argument(
        "--verify-max-attempts",
        type=int,
        default=None,
        metavar="N",
        help="Bounded retry count for post-publish verification",
    )
    activities.add_argument(
        "--verify-retry-delay",
        dest="verify_retry_delay_seconds",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Delay between post-publish verification attempts",
    )
    activities.add_argument(
        "--json",
        action="store_true",
        help="Print publish result as JSON when --publish is used",
    )

    # run
    run = sub.add_parser("run", help="Run the full pipeline (stages 1→4 + HTML)")
    run.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    run.add_argument(
        "--input",
        default="input.xlsx",
        help="Path to pipeline input workbook (default: input.xlsx)",
    )
    run.add_argument(
        "--export-dir",
        default="export",
        help="Export output directory (default: export)",
    )
    run.add_argument(
        "--resume-from",
        default="1",
        help="Resume from stage number or alias (1-4, config, scraping, planning, export)",
    )
    run.add_argument(
        "--log-level",
        default="info",
        choices=["info", "verbose"],
        help="Console/log verbosity hint (default: info)",
    )
    run.add_argument(
        "--force-refresh",
        action="store_true",
        help="Force calendar cache refresh before Stage 2 when that stage runs",
    )
    run.add_argument(
        "--non-strict",
        action="store_true",
        help="Continue on blocked sources or warnings",
    )
    run.add_argument(
        "--allow-missing-sources",
        action="store_true",
        help="Treat blocked sources as an operator-approved skip and keep partial results",
    )
    run.add_argument(
        "--no-timestamped-export",
        dest="timestamped_export",
        action="store_false",
        help="Write exports flat into --export-dir instead of a timestamped subfolder",
    )
    run.set_defaults(timestamped_export=True)
    run.add_argument(
        "--iterations",
        type=int,
        default=1,
        metavar="N",
        help="Run Stage 3 planner N times with different random seeds and keep the best plan (default: 1)",
    )
    run.add_argument(
        "--mid-planning-critic-iterations",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Optionally run a Stage 3 checkpoint critic loop before Stage 4 export: "
            "inspect the plan, inject structured planner hints, and re-run Stage 3 up to N times "
            "(default: 0/off)"
        ),
    )
    # Headless / CI judge backend: set RVV_JUDGE_BACKEND=claude|openai|llm_bridge
    # plus the matching API key (ANTHROPIC_API_KEY / OPENAI_API_KEY) to enable
    # inter-stage LLM judgment when no harness session is present.
    # See docs/rvv-miniputt-pipeline.md §"Headless / CI usage" for details.
    run.add_argument(
        "--interactive",
        action="store_true",
        help="Run exactly one stage (starting at --resume-from), emit a DecisionContext for it "
        "as JSON, and exit — the canonical capability behind harness stage-by-stage checkpoint "
        "review (issue #260 Phase 5). See --decision-action to advance past a prior stage's "
        "checkpoint on the next invocation",
    )
    run.add_argument(
        "--new-full-run",
        dest="new_full_run",
        action="store_true",
        help="Explicitly start a new full pipeline run even though a reviewed, exported, "
        "unpromoted Stage 3/4 candidate exists. Without this, a plain run that would restart "
        "Stage 1 is refused and points to 'stage3 refine' instead of silently invalidating the "
        "reviewed candidate",
    )
    run.add_argument(
        "--decision-action",
        default=None,
        metavar="JSON",
        help="Inline JSON DecisionAction (e.g. '{\"action_id\": \"proceed\"}') deciding the "
        "outcome of the stage immediately before --resume-from. Validated against a freshly "
        "rebuilt DecisionContext for that stage before the run proceeds; only meaningful with "
        "--interactive and --resume-from > 1",
    )
    run.add_argument(
        "--decision-action-file",
        default=None,
        metavar="PATH",
        help="Path to a JSON file containing the DecisionAction, alternative to --decision-action",
    )

    # operator — the goal-oriented AI operator entry point (see docs/ai-operator-product-direction.md)
    operator = sub.add_parser(
        "operator",
        help="Goal-oriented AI operator commands (thin wrapper around the portable pipeline)",
    )
    operator_sub = operator.add_subparsers(dest="operator_command")

    op_run = operator_sub.add_parser(
        "run",
        help="Produce the best trustworthy season plan: inspects workspace state, "
        "resumes from the earliest stale/pending capability, and reports a "
        "structured summary",
    )
    op_run.add_argument(
        "--objective",
        default=None,
        help="Explicit objective for this run (default: produce the best trustworthy season plan)",
    )
    op_run.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    op_run.add_argument(
        "--input",
        default="input.xlsx",
        help="Path to pipeline input workbook (default: input.xlsx)",
    )
    op_run.add_argument(
        "--export-dir",
        default="export",
        help="Export output directory (default: export)",
    )
    op_run.add_argument(
        "--resume-from",
        default=None,
        help="Force resuming from a specific stage number or alias, overriding auto-detection "
        "(1-4, config, scraping, planning, export)",
    )
    op_run.add_argument(
        "--force",
        action="store_true",
        help="Run the full pipeline from stage 1 even if every stage already looks done and fresh",
    )
    op_run.add_argument(
        "--log-level",
        default="info",
        choices=["info", "verbose"],
        help="Console/log verbosity hint (default: info)",
    )
    op_run.add_argument(
        "--force-refresh",
        action="store_true",
        help="Force calendar cache refresh before Stage 2 when that stage runs",
    )
    op_run.add_argument(
        "--non-strict",
        action="store_true",
        help="Continue on blocked sources or warnings instead of escalating",
    )
    op_run.add_argument(
        "--allow-missing-sources",
        action="store_true",
        help="Treat blocked sources as an operator-approved skip and keep partial results",
    )
    op_run.add_argument(
        "--manual-bookup-login",
        action="store_true",
        help="Open BookUp in a visible browser and wait for manual Vipps/SMS MFA during Stage 2",
    )
    op_run.add_argument(
        "--manual-bookup-login-timeout",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Maximum seconds to wait for manual BookUp MFA/login (default: 300)",
    )
    op_run.add_argument(
        "--no-timestamped-export",
        dest="timestamped_export",
        action="store_false",
        help="Write exports flat into --export-dir instead of a timestamped subfolder",
    )
    op_run.set_defaults(timestamped_export=True)
    op_run.add_argument(
        "--iterations",
        type=int,
        default=1,
        metavar="N",
        help="Run Stage 3 planner N times with different random seeds and keep the best plan (default: 1)",
    )
    op_run.add_argument(
        "--mid-planning-critic-iterations",
        type=int,
        default=0,
        metavar="N",
        help="Optionally run a Stage 3 checkpoint critic loop before Stage 4 export (default: 0/off)",
    )
    op_run.add_argument(
        "--publish",
        action="store_true",
        help="After a successful run, publish the exported season plan to GitHub Pages (issue #17)",
    )
    # The following mirror "operator publish"'s own flags exactly (same names/help/defaults) so
    # "operator run --publish ..." behaves identically to running "operator run" followed by a
    # separate "operator publish ..." — the only difference is that this bundles both into one
    # invocation, and folds the publish outcome into the same per-run log (issue #32 follow-up).
    op_run.add_argument(
        "--repo-dir",
        default=".",
        help="Git repository directory to publish from (default: current directory)",
    )
    op_run.add_argument(
        "--branch",
        default="gh-pages",
        help="Pages branch to publish to (default: gh-pages)",
    )
    op_run.add_argument(
        "--remote",
        default="origin",
        help="Git remote to push to (default: origin)",
    )
    op_run.add_argument(
        "--no-push",
        dest="push",
        action="store_false",
        help="Commit the Pages branch locally but do not push to the remote",
    )
    op_run.set_defaults(push=True)
    op_run.add_argument(
        "--extra-public-file",
        dest="extra_public_files",
        action="append",
        default=[],
        metavar="FILENAME",
        help="Allow an additional filename into the sanitized public bundle (issue #18); repeatable",
    )
    op_run.add_argument(
        "--allow-finding",
        dest="allow_findings",
        action="append",
        default=[],
        metavar="TEXT",
        help="Acknowledge a specific flagged string as a false positive so it no longer blocks "
        "publication (issue #18); repeatable",
    )
    op_run.add_argument(
        "--confirm-public",
        action="store_true",
        help="Explicitly authorize publishing this exact bundle to this exact target right now "
        "(issue #19) — without this (or a prior durable 'godkjenn' answer for this exact bundle "
        "and target), --publish only previews and raises an approval question",
    )
    op_run.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show what would change under /latest/ and raise the approval question; "
        "never publish, even if an approval already exists for this bundle",
    )
    op_run.add_argument(
        "--no-verify",
        dest="verify",
        action="store_false",
        help="Skip polling the published URL for reachability after a successful publish push (issue #20)",
    )
    op_run.set_defaults(verify=True)
    op_run.add_argument(
        "--verify-max-attempts",
        type=int,
        default=None,
        metavar="N",
        help="Bounded retry count for post-publish verification (default: pages_verify's own default)",
    )
    op_run.add_argument(
        "--verify-retry-delay",
        dest="verify_retry_delay_seconds",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Delay between post-publish verification attempts (default: pages_verify's own default)",
    )

    op_questions = operator_sub.add_parser(
        "questions",
        help="List pending (unanswered) escalation questions raised by the last operator run",
    )
    op_questions.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    op_questions.add_argument(
        "--json",
        action="store_true",
        help="Print pending questions as JSON instead of a human-readable list",
    )
    op_questions.add_argument(
        "--all",
        action="store_true",
        help="Include answered and stale questions too, not just unanswered ones (issue #12)",
    )

    op_answer = operator_sub.add_parser(
        "answer",
        help="Record a durable human answer to a pending escalation question",
    )
    op_answer.add_argument("question_id", help="Question id, as shown by 'operator questions'")
    op_answer.add_argument("answer", help="The human's answer/decision")
    op_answer.add_argument(
        "--decided-by",
        default=None,
        help="Optional name/identifier of who made this decision, for the audit trail",
    )
    op_answer.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )

    op_promote = operator_sub.add_parser(
        "promote",
        help="Promote an answered decision to a broader scope so it is reused across runs/inputs/seasons (issue #12)",
    )
    op_promote.add_argument("question_id", help="Question id to promote, as shown by 'operator questions --all'")
    op_promote.add_argument(
        "scope",
        choices=["input_version", "season", "workspace"],
        help="Target scope — must be broader than the question's current scope",
    )
    op_promote.add_argument(
        "--scope-key",
        default="",
        help="Scope key for the target scope (required for 'season'; ignored for 'workspace')",
    )
    op_promote.add_argument(
        "--decided-by",
        default=None,
        help="Optional name/identifier of who made this promotion, for the audit trail",
    )
    op_promote.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )

    op_health = operator_sub.add_parser(
        "health",
        help="Check whether the run manifest is durably writable and free of unrecovered corruption (issue #14)",
    )
    op_health.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    op_health.add_argument(
        "--json",
        action="store_true",
        help="Print the health check result as JSON",
    )

    op_publish = operator_sub.add_parser(
        "publish",
        help="Publish the current exported season plan to GitHub Pages (issue #17)",
    )
    op_publish.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    op_publish.add_argument(
        "--export-dir",
        default=None,
        help="Override export bundle directory (default: resolved from the last Stage 4 export)",
    )
    op_publish.add_argument(
        "--run-id",
        default=None,
        help="Override the run id used for the immutable /runs/<run-id>/ path "
        "(default: the current run manifest's run_id)",
    )
    op_publish.add_argument(
        "--repo-dir",
        default=".",
        help="Git repository directory to publish from (default: current directory)",
    )
    op_publish.add_argument(
        "--branch",
        default="gh-pages",
        help="Pages branch to publish to (default: gh-pages)",
    )
    op_publish.add_argument(
        "--remote",
        default="origin",
        help="Git remote to push to (default: origin)",
    )
    op_publish.add_argument(
        "--no-push",
        dest="push",
        action="store_false",
        help="Commit the Pages branch locally but do not push to the remote",
    )
    op_publish.set_defaults(push=True)
    op_publish.add_argument(
        "--extra-public-file",
        dest="extra_public_files",
        action="append",
        default=[],
        metavar="FILENAME",
        help="Allow an additional filename into the sanitized public bundle (issue #18); repeatable",
    )
    op_publish.add_argument(
        "--allow-finding",
        dest="allow_findings",
        action="append",
        default=[],
        metavar="TEXT",
        help="Acknowledge a specific flagged string as a false positive so it no longer blocks "
        "publication (issue #18); repeatable",
    )
    op_publish.add_argument(
        "--confirm-public",
        action="store_true",
        help="Explicitly authorize publishing this exact bundle to this exact target right now "
        "(issue #19) — without this (or a prior durable 'godkjenn' answer for this exact bundle "
        "and target), publishing only previews and raises an approval question",
    )
    op_publish.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show what would change under /latest/ and raise the approval question; "
        "never publish, even if an approval already exists for this bundle",
    )
    op_publish.add_argument(
        "--no-verify",
        dest="verify",
        action="store_false",
        help="Skip polling the published URL for reachability after a successful push (issue #20)",
    )
    op_publish.set_defaults(verify=True)
    op_publish.add_argument(
        "--verify-max-attempts",
        type=int,
        default=None,
        metavar="N",
        help="Bounded retry count for post-publish verification (default: pages_verify's own default)",
    )
    op_publish.add_argument(
        "--verify-retry-delay",
        dest="verify_retry_delay_seconds",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Delay between post-publish verification attempts (default: pages_verify's own default)",
    )
    op_publish.add_argument(
        "--json",
        action="store_true",
        help="Print the publish result as JSON",
    )

    op_verify = operator_sub.add_parser(
        "verify",
        help="Re-check that the last published GitHub Pages content is reachable (issue #20)",
    )
    op_verify.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    op_verify.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        metavar="N",
        help="Bounded retry count (default: pages_verify's own default)",
    )
    op_verify.add_argument(
        "--retry-delay",
        dest="retry_delay_seconds",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Delay between attempts (default: pages_verify's own default)",
    )
    op_verify.add_argument(
        "--json",
        action="store_true",
        help="Print the verification result as JSON",
    )

    op_rollback = operator_sub.add_parser(
        "rollback",
        help="Roll '/latest/' back to a previously published run on GitHub Pages (issue #20)",
    )
    op_rollback.add_argument("run_id", help="A previously published run id, as shown by 'operator publish-history'")
    op_rollback.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    op_rollback.add_argument(
        "--repo-dir",
        default=".",
        help="Git repository directory (default: current directory)",
    )
    op_rollback.add_argument(
        "--branch",
        default="gh-pages",
        help="Pages branch (default: gh-pages)",
    )
    op_rollback.add_argument(
        "--remote",
        default="origin",
        help="Git remote to push to (default: origin)",
    )
    op_rollback.add_argument(
        "--no-push",
        dest="push",
        action="store_false",
        help="Commit the rollback locally but do not push to the remote",
    )
    op_rollback.set_defaults(push=True)
    op_rollback.add_argument(
        "--confirm-public",
        action="store_true",
        help="Explicitly authorize this rollback right now (issue #19/#20) — without this (or a "
        "prior durable 'godkjenn' answer for this exact rollback), it only raises an approval question",
    )
    op_rollback.add_argument(
        "--json",
        action="store_true",
        help="Print the rollback result as JSON",
    )

    op_publish_history = operator_sub.add_parser(
        "publish-history",
        help="List the publish/rollback history on the Pages branch (issue #20)",
    )
    op_publish_history.add_argument(
        "--repo-dir",
        default=".",
        help="Git repository directory (default: current directory)",
    )
    op_publish_history.add_argument(
        "--branch",
        default="gh-pages",
        help="Pages branch (default: gh-pages)",
    )
    op_publish_history.add_argument(
        "--json",
        action="store_true",
        help="Print the history as JSON",
    )

    _add_operator_audit_subparsers(operator_sub)  # issue #325, see args_audit.py
    # logs
    logs = sub.add_parser("logs", help="Show structured pipeline run logs")
    logs.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    logs_sub = logs.add_subparsers(dest="logs_command")

    logs_list = logs_sub.add_parser("list", help="List recent pipeline runs")
    logs_list.add_argument("--count", type=int, default=10, help="How many recent runs to show (default: 10)")
    logs_list.add_argument("--work-dir", default=".pipeline", help=argparse.SUPPRESS)

    logs_show = logs_sub.add_parser("show", help="Show details for one run")
    logs_show.add_argument("run_id", nargs="?", default="latest", help="Run id, or 'latest' (default)")
    logs_show.add_argument("--work-dir", default=".pipeline", help=argparse.SUPPRESS)

    logs_stats = logs_sub.add_parser("stats", help="Show aggregate run statistics")
    logs_stats.add_argument("--work-dir", default=".pipeline", help=argparse.SUPPRESS)

    # registrations — reviewed SharePoint List export -> controlled input.xlsx snapshot
    registrations = sub.add_parser(
        "registrations",
        help="Validate or export reviewed SharePoint registrations into the Lag sheet of input.xlsx",
    )
    registrations_sub = registrations.add_subparsers(dest="registrations_command")

    registrations_validate = registrations_sub.add_parser(
        "validate",
        help="Validate a reviewed SharePoint CSV/XLSX export without writing a workbook",
    )
    registrations_validate.add_argument("source", help="Reviewed SharePoint List export (.csv/.xlsx)")
    registrations_validate.add_argument(
        "--input",
        required=True,
        help="Controlled pipeline input workbook to validate against (input.xlsx)",
    )

    registrations_export = registrations_sub.add_parser(
        "export",
        help="Create an updated input workbook with only Lag replaced from approved registrations",
    )
    registrations_export.add_argument("source", help="Reviewed SharePoint List export (.csv/.xlsx)")
    registrations_export.add_argument(
        "--input",
        required=True,
        help="Controlled pipeline input workbook to copy and update",
    )
    registrations_export.add_argument(
        "--output",
        required=True,
        help="Output workbook path for the updated controlled input snapshot",
    )
    registrations_export.add_argument(
        "--dry-run",
        action="store_true",
        help="Show validation/diff summary without writing the output workbook or audit artifact",
    )

    # scrape — single-club troubleshooting
    scrape = sub.add_parser("scrape", help="Scrape a single club's calendar for troubleshooting")
    scrape.add_argument(
        "--club", required=True,
        help="Club/source name (e.g. 'Sandefjord Penguins', 'Jar', 'Jutul')",
    )
    scrape.add_argument(
        "--work-dir", default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )

    # scrape-llm — capability-gated LLM browser guidance for blocked sources
    scrape_llm = sub.add_parser(
        "scrape-llm",
        help="Show browser-tool requirements for LLM-guided single-club recovery (Pi or browser-enabled harness only)",
    )
    scrape_llm.add_argument(
        "--club", required=True,
        help="Club/source name (e.g. 'Holmen', 'Jar', 'Sandefjord')",
    )
    scrape_llm.add_argument(
        "--work-dir", default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    scrape_llm.add_argument(
        "--export-dir", default="export",
        help="Export directory for debug screenshots or recovered artifacts (default: export)",
    )
    scrape_llm.add_argument(
        "--endpoint", default="http://host.lima.internal:1234",
        help="LLM API endpoint used by the browser agent (default: http://host.lima.internal:1234)",
    )
    scrape_llm.add_argument(
        "--model", default="qwen2.5-32b-instruct",
        help="LLM model name used by the browser agent (default: qwen2.5-32b-instruct)",
    )
    scrape_llm.add_argument(
        "--max-iterations", type=int, default=20,
        help="Maximum browser interaction cycles to advertise to the browser agent (default: 20)",
    )
    scrape_llm.add_argument(
        "--cache-results", dest="cache_results", action="store_true",
        help="Advertise caching of recovered events (default: on)",
    )
    scrape_llm.add_argument(
        "--no-cache-results", dest="cache_results", action="store_false",
        help="Disable caching in the capability guidance",
    )
    scrape_llm.set_defaults(cache_results=True)
    scrape_llm.add_argument(
        "--debug-screenshots", action="store_true",
        help="Advertise saving browser debug screenshots",
    )

    recovery = sub.add_parser(
        "recovery-targets",
        help="List blocked or zero-event sources from the Stage 2 checkpoint as JSON",
    )
    recovery.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )

    # recovery-inject — inject recovered events into the unified cache from stdin
    recovery_inject = sub.add_parser(
        "recovery-inject",
        help="Inject a JSON event list from stdin into the cache for a given source",
    )
    recovery_inject.add_argument(
        "--source",
        required=True,
        help="Source name to patch (e.g. 'Tønsberg')",
    )
    recovery_inject.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )

    # scrape-merge — rebuild Stage 2 checkpoint from recovered cache data
    scrape_merge = sub.add_parser(
        "scrape-merge",
        help="Rebuild the Stage 2 checkpoint from recovered cache data",
    )
    scrape_merge.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )

    # season — canonical Git-backed promoted season state
    season = sub.add_parser(
        "season",
        help="Promote, inspect, replan and export canonical Git-backed season state",
    )
    season_sub = season.add_subparsers(dest="season_command")

    season_promote = season_sub.add_parser(
        "promote",
        help="Deliberately promote the current verified Stage 3 candidate into season/<season>/ state",
    )
    season_promote.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory (default: .pipeline)")
    season_promote.add_argument("--season", default=None, help="Season id, e.g. 2026-2027 (default: infer from plan)")
    season_promote.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_promote.add_argument("--actor", default=None, help="Operator identity for the promotion record")
    season_promote.add_argument("--force", action="store_true", help="Deliberately replace existing canonical state")
    season_promote.add_argument("--json", action="store_true", help="Print written state metadata as JSON")

    season_export = season_sub.add_parser(
        "export",
        help="Regenerate Stage 4 exports from canonical season state, without relying on Stage 3 checkpoints",
    )
    season_export.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_export.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_export.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for export checkpoint/logs")
    season_export.add_argument("--export-dir", default="export", help="Export directory (default: export)")
    season_export.add_argument("--flat", dest="timestamped_export", action="store_false", help="Write directly into --export-dir")
    season_export.set_defaults(timestamped_export=True)
    season_export.add_argument("--json", action="store_true", help="Print Stage 4 export checkpoint as JSON")

    season_status = season_sub.add_parser("status", help="Show canonical season state metadata")
    season_status.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_status.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_status.add_argument("--json", action="store_true", help="Print canonical state metadata as JSON")

    season_lifecycle = season_sub.add_parser(
        "lifecycle",
        help="Show the published/planning lifecycle state and published-baseline reconciliation",
    )
    season_lifecycle.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_lifecycle.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_lifecycle.add_argument("--json", action="store_true", help="Print the lifecycle report as JSON")

    season_seal = season_sub.add_parser(
        "seal-published",
        help=(
            "Backfill/seal an already published season from its authoritative "
            "publication history so it can no longer be globally regenerated"
        ),
    )
    season_seal.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_seal.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_seal.add_argument("--actor", default=None, help="Operator identity")
    season_seal.add_argument("--note", default="", help="Audit note")
    season_seal.add_argument(
        "--attest-materialization",
        dest="attest_materializations",
        action="append",
        default=[],
        metavar="TOURNAMENT_ID=PROVENANCE",
        help=(
            "Attest a tournament that was created after publication, with durable "
            "provenance evidence (repeatable). Only needed for legacy migration; "
            "an unattested added tournament fails closed"
        ),
    )
    season_seal.add_argument("--json", action="store_true", help="Print the seal report as JSON")

    season_reopen = season_sub.add_parser(
        "reopen-planning",
        help="Emergency, operator-only escape hatch from published_sealed back to planning",
    )
    season_reopen.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_reopen.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_reopen.add_argument("--reason", required=True, help="Operator reason for breaking the published baseline")
    season_reopen.add_argument(
        "--confirm-break-published-baseline",
        action="store_true",
        help="Required acknowledgement that this removes the sealed-season protection",
    )
    season_reopen.add_argument("--actor", default=None, help="Operator identity")
    season_reopen.add_argument("--json", action="store_true", help="Print the reopen report as JSON")

    season_normalize = season_sub.add_parser(
        "normalize-placements",
        help=(
            "Upgrade an already-generated canonical plan to the placed/provisional/unplaced "
            "state model without rerunning Stage 1-3"
        ),
    )
    season_normalize.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_normalize.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_normalize.add_argument("--actor", default=None, help="Operator identity")
    season_normalize.add_argument("--note", default="", help="Audit note")
    season_normalize.add_argument("--dry-run", action="store_true", help="Report what would change without writing canonical state")
    season_normalize.add_argument("--json", action="store_true", help="Print the normalization report as JSON")

    season_normalize_arenas = season_sub.add_parser(
        "normalize-arenas",
        help=(
            "Re-emit the canonical schedulable arena across a promoted season "
            "(legacy venue labels are rewritten in place without changing any placement)"
        ),
    )
    season_normalize_arenas.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_normalize_arenas.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_normalize_arenas.add_argument("--actor", default=None, help="Operator identity")
    season_normalize_arenas.add_argument("--note", default="", help="Audit note")
    season_normalize_arenas.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing canonical state",
    )
    season_normalize_arenas.add_argument("--json", action="store_true", help="Print the normalization report as JSON")

    season_approve = season_sub.add_parser("approve", help="Approve/lock one canonical tournament in decisions.json")
    season_approve.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_approve.add_argument("--tournament-id", required=True, help="Durable tournament id to approve")
    season_approve.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_approve.add_argument("--actor", default=None, help="Operator identity")
    season_approve.add_argument("--note", default="", help="Approval note")
    season_approve.add_argument("--no-placement-lock", dest="placement_locked", action="store_false", help="Approve without locking placement")
    season_approve.set_defaults(placement_locked=True)
    season_approve.add_argument("--participants-lock", action="store_true", help="Also lock participants")
    season_approve.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
    season_approve.add_argument("--json", action="store_true", help="Print updated decisions.json as JSON")

    season_unapprove = season_sub.add_parser(
        "unapprove", help="Revoke approval and locks for one canonical tournament, restoring editability"
    )
    season_unapprove.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_unapprove.add_argument("--tournament-id", required=True, help="Durable tournament id to unapprove")
    season_unapprove.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_unapprove.add_argument("--actor", default=None, help="Operator identity")
    season_unapprove.add_argument("--note", default="", help="Reason for revoking the approval")
    season_unapprove.add_argument("--json", action="store_true", help="Print updated decisions.json as JSON")

    season_approvals = season_sub.add_parser(
        "approvals", help="List per-tournament approval/lock status, including stale approvals"
    )
    season_approvals.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_approvals.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_approvals.add_argument("--json", action="store_true", help="Print the approval report as JSON")

    season_booking_candidates = season_sub.add_parser(
        "calendar-booking-candidates",
        help="Return deterministic tournament candidates for scraped calendar bookings",
    )
    season_booking_candidates.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_booking_candidates.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_booking_candidates.add_argument("--club", default=None, help="Optional host club filter")
    season_booking_candidates.add_argument("--json", action="store_true", help="Print structured JSON")

    season_confirm_booking = season_sub.add_parser(
        "confirm-calendar-booking",
        help="Bind one scraped calendar event to one canonical tournament and approve/lock the placement",
    )
    season_confirm_booking.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_confirm_booking.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_confirm_booking.add_argument("--event-fingerprint", required=True, help="Scraped calendar event fingerprint")
    season_confirm_booking.add_argument("--tournament-id", required=True, help="Canonical tournament id")
    season_confirm_booking.add_argument("--actor", default=None, help="Operator identity")
    season_confirm_booking.add_argument("--note", default="", help="Audited rationale for this semantic match")
    season_confirm_booking.add_argument("--dry-run", action="store_true", help="Validate without writing canonical state")
    season_confirm_booking.add_argument("--json", action="store_true", help="Print structured JSON")

    season_booking_findings = season_sub.add_parser(
        "calendar-booking-findings",
        help="Report stale scraped-event booking associations",
    )
    season_booking_findings.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_booking_findings.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_booking_findings.add_argument("--json", action="store_true", help="Print structured JSON")

    season_booking_status = season_sub.add_parser(
        "booking-status",
        help="Report per-tournament booking evidence status separately from approvals",
    )
    season_booking_status.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_booking_status.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_booking_status.add_argument("--json", action="store_true", help="Print structured JSON")

    season_reconcile_bookings = season_sub.add_parser(
        "reconcile-calendar-bookings",
        help="Record host-calendar booking evidence for one club (overlap is candidate evidence, never a confirmed booking)",
    )
    season_reconcile_bookings.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_reconcile_bookings.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_reconcile_bookings.add_argument("--club", required=True, help="Host club whose calendar was reviewed")
    season_reconcile_bookings.add_argument("--actor", default=None, help="Operator identity")
    season_reconcile_bookings.add_argument("--note", default="", help="Audit note for the calendar review")
    season_reconcile_bookings.add_argument("--dry-run", action="store_true", help="Classify without writing canonical state")
    season_reconcile_bookings.add_argument("--json", action="store_true", help="Print structured JSON")

    season_release_booking = season_sub.add_parser(
        "release-calendar-booking",
        help="Release an active scraped-event booking association before rebinding",
    )
    season_release_booking.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_release_booking.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_release_booking.add_argument("--event-fingerprint", required=True, help="Scraped calendar event fingerprint")
    season_release_booking.add_argument("--tournament-id", default=None, help="Optional associated tournament id to release")
    season_release_booking.add_argument("--actor", default=None, help="Operator identity")
    season_release_booking.add_argument("--note", default="", help="Audited reason for releasing this association")
    season_release_booking.add_argument("--dry-run", action="store_true", help="Validate without writing canonical state")
    season_release_booking.add_argument("--json", action="store_true", help="Print structured JSON")

    season_move = season_sub.add_parser("move", help="Apply a verified placement mutation to canonical schedule state")
    season_move.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_move.add_argument("--tournament-id", required=True, help="Durable tournament id to mutate")
    season_move.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_move.add_argument("--date", default=None, help="New date (YYYY-MM-DD)")
    season_move.add_argument("--arena", default=None, help="New arena")
    season_move.add_argument("--host-club", default=None, help="New physical host club")
    season_move.add_argument("--start-time", default=None, help="New start time/placement value")
    season_move.add_argument("--actor", default=None, help="Operator identity")
    season_move.add_argument("--note", default="", help="Move note/reason for decisions history")
    season_move.add_argument("--request-id", default=None, help="Stable source/request id recorded on automatic change protections")
    season_move.add_argument("--dry-run", action="store_true", help="Validate and preview the move without writing canonical state")
    _add_operational_opt_in_flags(season_move)
    season_move.add_argument(
        "--allow-cross-half",
        action="store_true",
        help="Allow a date move across the before/after-Christmas planning boundary",
    )
    season_move.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
    season_move.add_argument("--json", action="store_true", help="Print updated schedule.json as JSON")


    season_replace = season_sub.add_parser(
        "replace-participant",
        help="Replace one participant in one canonical tournament and verify the full season",
    )
    season_replace.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_replace.add_argument("--tournament-id", required=True, help="Durable tournament id")
    season_replace.add_argument("--remove-team", required=True, help="Existing participant label to remove")
    season_replace.add_argument("--add-team", required=True, help="Registered same-age participant label to add")
    season_replace.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_replace.add_argument("--actor", default=None, help="Operator identity")
    season_replace.add_argument("--note", default="", help="Replacement note/reason for decisions history")
    season_replace.add_argument("--request-id", default=None, help="Stable source/request id recorded on automatic change protections")
    season_replace.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and preview the replacement without writing canonical state",
    )
    season_replace.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
    season_replace.add_argument("--json", action="store_true", help="Print replacement result as JSON")

    season_swap = season_sub.add_parser(
        "swap-participants",
        help="Swap one participant between two same-age canonical tournaments and verify the full season",
    )
    season_swap.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_swap.add_argument("--tournament-a", required=True, help="First durable tournament id")
    season_swap.add_argument("--team-a", required=True, help="Participant label to remove from tournament A")
    season_swap.add_argument("--tournament-b", required=True, help="Second durable tournament id")
    season_swap.add_argument("--team-b", required=True, help="Participant label to remove from tournament B")
    season_swap.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_swap.add_argument("--actor", default=None, help="Operator identity")
    season_swap.add_argument("--note", default="", help="Swap note/reason for decisions history")
    season_swap.add_argument("--request-id", default=None, help="Stable source/request id recorded on automatic change protections")
    season_swap.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and preview the swap without writing canonical state",
    )
    _add_regression_acceptance_flags(season_swap)
    season_swap.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
    season_swap.add_argument("--json", action="store_true", help="Print swap result as JSON")

    season_rename = season_sub.add_parser(
        "rename-team",
        help="Rename one or more canonical team identities without replanning",
    )
    season_rename.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_rename.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_rename.add_argument("--club", action="append", default=None, help="Club for a rename mapping (repeatable with --age-group/--from/--to)")
    season_rename.add_argument("--age-group", action="append", default=None, help="Age group for a rename mapping")
    season_rename.add_argument("--from", dest="from_label", action="append", default=None, help="Existing team label")
    season_rename.add_argument("--to", dest="to_label", action="append", default=None, help="New team label")
    season_rename.add_argument(
        "--mapping",
        action="append",
        default=None,
        help="Mapping as club,age_group,from_label,to_label (repeatable); alternative to the repeated flags",
    )
    season_rename.add_argument("--actor", default=None, help="Operator identity")
    season_rename.add_argument("--note", default="", help="Rename note/reason for decisions history")
    season_rename.add_argument("--request-id", required=True, help="Stable request id recorded in history")
    season_rename.add_argument("--input", default="input.xlsx", help="Controlled input workbook to update on apply")
    season_rename.add_argument(
        "--no-input-update",
        action="store_true",
        help="Do not update the controlled input workbook (for tests or non-workbook snapshots)",
    )
    season_rename.add_argument("--dry-run", action="store_true", help="Validate and preview the rename without writing")
    season_rename.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
    season_rename.add_argument("--json", action="store_true", help="Print rename result as JSON")


    season_batch = season_sub.add_parser(
        "batch",
        help=(
            "Atomically compose several scoped canonical mutations in one candidate and commit once; "
            "use when several recorded request constraints can only be fixed together"
        ),
    )
    season_batch.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_batch.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_batch.add_argument(
        "--operations",
        required=True,
        help=(
            "Path to a JSON file with an array of operations, e.g. "
            "[{\"op\": \"move\", \"tournament_id\": \"rvv-0147\", \"date\": \"2027-02-27\"}, "
            "{\"op\": \"swap_participants\", ...}, {\"op\": \"cancel\", ...}]"
        ),
    )
    season_batch.add_argument(
        "--scope",
        action="append",
        default=None,
        help="Affected tournament id (repeatable, comma-separated accepted); all other tournaments are frozen",
    )
    season_batch.add_argument("--actor", default=None, help="Operator identity")
    season_batch.add_argument("--note", default="", help="Batch note/reason for decisions history")
    season_batch.add_argument(
        "--request-id",
        required=True,
        help="Stable request id recorded for the whole batch",
    )
    season_batch.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report every gate without writing canonical state",
    )
    _add_operational_opt_in_flags(season_batch)
    _add_regression_acceptance_flags(season_batch)
    season_batch.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
    season_batch.add_argument("--json", action="store_true", help="Print the batch report as JSON")

    season_protections = season_sub.add_parser(
        "protections",
        help="List team-specific accepted-change guards that later maintenance must preserve",
    )
    season_protections.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_protections.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_protections.add_argument("--all", action="store_true", help="Include released protections")
    season_protections.add_argument("--json", action="store_true", help="Print protection report as JSON")

    season_release_protection = season_sub.add_parser(
        "release-protection",
        help="Explicitly release accepted-change guards when a newer request supersedes them",
    )
    season_release_protection.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_release_protection.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_release_protection.add_argument(
        "--protection-id",
        dest="protection_ids",
        action="append",
        default=None,
        help="Protection id to release (repeatable)",
    )
    season_release_protection.add_argument(
        "--request-id",
        default=None,
        help="Release all active protections created by this earlier request id",
    )
    season_release_protection.add_argument("--actor", default=None, help="Operator identity")
    season_release_protection.add_argument("--note", default="", help="Why this protection is being superseded")
    season_release_protection.add_argument("--json", action="store_true", help="Print release result as JSON")

    season_constraints = season_sub.add_parser(
        "constraints",
        help="List typed request constraints and their derived current satisfaction",
    )
    season_constraints.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_constraints.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_constraints.add_argument("--all", action="store_true", help="Include released constraints")
    season_constraints.add_argument("--json", action="store_true", help="Print the constraint report as JSON")

    season_add_constraint = season_sub.add_parser(
        "add-constraint",
        help="Persist one validated typed request constraint (decision-only write)",
    )
    season_add_constraint.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_add_constraint.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_add_constraint.add_argument(
        "--type",
        required=True,
        choices=["team_unavailable", "minimum_gap", "opponent_avoidance"],
        help="Typed constraint kind",
    )
    season_add_constraint.add_argument(
        "--request-id",
        required=True,
        help="Stable source/request id; the constraint id is derived from it plus the semantic payload",
    )
    season_add_constraint.add_argument("--team-club", default="", help="Primary team club")
    season_add_constraint.add_argument("--team-label", default="", help="Primary team label")
    season_add_constraint.add_argument(
        "--team-age-group", default="", help="Primary team age group (required when club+label is ambiguous)"
    )
    season_add_constraint.add_argument("--team2-club", default="", help="Second team club (opponent_avoidance)")
    season_add_constraint.add_argument("--team2-label", default="", help="Second team label (opponent_avoidance)")
    season_add_constraint.add_argument(
        "--team2-age-group", default="", help="Second team age group (opponent_avoidance)"
    )
    season_add_constraint.add_argument(
        "--date-from", default=None, help="Inclusive start date YYYY-MM-DD (single-day when --date-to is omitted)"
    )
    season_add_constraint.add_argument("--date-to", default=None, help="Inclusive end date YYYY-MM-DD")
    season_add_constraint.add_argument("--min-days", type=int, default=None, help="Minimum gap in days (minimum_gap)")
    season_add_constraint.add_argument("--actor", default=None, help="Operator identity")
    season_add_constraint.add_argument("--note", default="", help="Source note/reason for decisions history")
    season_add_constraint.add_argument("--json", action="store_true", help="Print the recorded constraint as JSON")

    season_release_constraint = season_sub.add_parser(
        "release-constraint",
        help="Explicitly release/supersede request constraints when a newer request replaces them",
    )
    season_release_constraint.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_release_constraint.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_release_constraint.add_argument(
        "--constraint-id",
        dest="constraint_ids",
        action="append",
        default=None,
        help="Constraint id to release (repeatable)",
    )
    season_release_constraint.add_argument(
        "--request-id",
        default=None,
        help="Release all active constraints created by this earlier request id",
    )
    season_release_constraint.add_argument("--actor", default=None, help="Operator identity")
    season_release_constraint.add_argument("--note", default="", help="Why this constraint is being superseded")
    season_release_constraint.add_argument("--json", action="store_true", help="Print release result as JSON")

    season_ban_date = season_sub.add_parser(
        "ban-date",
        help="Record a global operator date ban (policy/decision write; schedule repair is separate)",
    )
    season_ban_date.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_ban_date.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_ban_date.add_argument("--date", required=True, help="Date to ban globally (YYYY-MM-DD)")
    season_ban_date.add_argument(
        "--request-id",
        required=True,
        help="Stable operator/request id recorded as provenance",
    )
    season_ban_date.add_argument("--actor", default=None, help="Operator identity")
    season_ban_date.add_argument("--note", default="", help="Reason/source note for decisions history")
    season_ban_date.add_argument("--json", action="store_true", help="Print the banned date report as JSON")

    season_unban_date = season_sub.add_parser(
        "unban-date",
        help="Remove one or more active operator banned dates with audit history",
    )
    season_unban_date.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_unban_date.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_unban_date.add_argument(
        "--date",
        dest="dates",
        action="append",
        default=None,
        help="Banned date to remove (repeatable)",
    )
    season_unban_date.add_argument(
        "--ban-id",
        dest="date_ids",
        action="append",
        default=None,
        help="Banned-date record id to remove (repeatable)",
    )
    season_unban_date.add_argument(
        "--request-id",
        default=None,
        help="Remove all active bans created by this earlier request id",
    )
    season_unban_date.add_argument("--actor", default=None, help="Operator identity")
    season_unban_date.add_argument("--note", default="", help="Why the ban is being removed")
    season_unban_date.add_argument("--json", action="store_true", help="Print release result as JSON")

    season_banned_dates = season_sub.add_parser(
        "banned-dates",
        help="List active operator banned dates and the tournaments that currently violate them",
    )
    season_banned_dates.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_banned_dates.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_banned_dates.add_argument("--all", action="store_true", help="Include released bans")
    season_banned_dates.add_argument("--json", action="store_true", help="Print the banned date report as JSON")

    season_allow_holiday_date = season_sub.add_parser(
        "allow-holiday-date",
        help="Allow one date that is excluded only by the derived holiday/date policy",
    )
    season_allow_holiday_date.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_allow_holiday_date.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_allow_holiday_date.add_argument("--date", required=True, help="Date to allow (YYYY-MM-DD)")
    season_allow_holiday_date.add_argument("--reason", required=True, help="Audited reason for allowing this date")
    season_allow_holiday_date.add_argument("--actor", default=None, help="Operator identity")
    season_allow_holiday_date.add_argument("--json", action="store_true", help="Print result as JSON")

    season_disallow_holiday_date = season_sub.add_parser(
        "disallow-holiday-date",
        help="Remove one or more active holiday-date exceptions",
    )
    season_disallow_holiday_date.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_disallow_holiday_date.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_disallow_holiday_date.add_argument(
        "--date",
        dest="dates",
        action="append",
        default=None,
        help="Allowed holiday date to remove (repeatable)",
    )
    season_disallow_holiday_date.add_argument(
        "--exception-id",
        dest="exception_ids",
        action="append",
        default=None,
        help="Holiday-date exception id to remove (repeatable)",
    )
    season_disallow_holiday_date.add_argument("--actor", default=None, help="Operator identity")
    season_disallow_holiday_date.add_argument("--note", default="", help="Why the exception is being removed")
    season_disallow_holiday_date.add_argument("--json", action="store_true", help="Print result as JSON")

    season_holiday_date_exceptions = season_sub.add_parser(
        "holiday-date-exceptions",
        help="List active holiday-policy date exceptions",
    )
    season_holiday_date_exceptions.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_holiday_date_exceptions.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_holiday_date_exceptions.add_argument("--all", action="store_true", help="Include released exceptions")
    season_holiday_date_exceptions.add_argument("--json", action="store_true", help="Print report as JSON")

    season_guest_report = season_sub.add_parser(
        "guest-report",
        help="Show reserved guest places and their open/filled/released lifecycle status",
    )
    season_guest_report.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_guest_report.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_guest_report.add_argument("--json", action="store_true", help="Print the guest-slot report as JSON")

    season_guest_candidates = season_sub.add_parser(
        "guest-candidates",
        help="List deterministic legal candidates for reserving guest places",
    )
    season_guest_candidates.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_guest_candidates.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_guest_candidates.add_argument(
        "--age-groups",
        default=None,
        help="Comma-separated age groups (default: JU10,JU12)",
    )
    season_guest_candidates.add_argument(
        "--max-per-tournament",
        type=int,
        default=1,
        help="Maximum reservations per tournament when ranking candidates (default: 1)",
    )
    season_guest_candidates.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
    season_guest_candidates.add_argument("--json", action="store_true", help="Print candidates as JSON")

    season_guest_reserve = season_sub.add_parser(
        "guest-reserve",
        help="Reserve one or more guest places on one canonical tournament",
    )
    season_guest_reserve.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_guest_reserve.add_argument("--tournament-id", required=True, help="Durable tournament id to reserve on")
    season_guest_reserve.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_guest_reserve.add_argument("--count", type=int, default=1, help="Number of places to reserve (default: 1)")
    season_guest_reserve.add_argument(
        "--displaced-team",
        dest="displaced_teams",
        action="append",
        default=None,
        help="Participant label to displace when the tournament is full (repeatable)",
    )
    season_guest_reserve.add_argument("--actor", default=None, help="Operator identity")
    season_guest_reserve.add_argument("--note", default="", help="Reservation note/reason for decisions history")
    season_guest_reserve.add_argument("--dry-run", action="store_true", help="Validate and preview without writing canonical state")
    season_guest_reserve.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
    season_guest_reserve.add_argument("--json", action="store_true", help="Print updated schedule.json as JSON")

    season_guest_fill = season_sub.add_parser(
        "guest-fill",
        help="Accept an external team into a reserved guest place and regenerate games",
    )
    season_guest_fill.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_guest_fill.add_argument("--tournament-id", required=True, help="Durable tournament id holding the reservation")
    season_guest_fill.add_argument("--slot-id", default=None, help="Reservation id (default: first open reservation)")
    season_guest_fill.add_argument("--external-club", default="", help="External guest team's club")
    season_guest_fill.add_argument("--external-label", required=True, help="External guest team's label")
    season_guest_fill.add_argument("--external-age-group", default=None, help="External guest team's age group (default: tournament age group)")
    season_guest_fill.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_guest_fill.add_argument("--actor", default=None, help="Operator identity")
    season_guest_fill.add_argument("--note", default="", help="Fill note for decisions history")
    season_guest_fill.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
    season_guest_fill.add_argument("--json", action="store_true", help="Print updated schedule.json as JSON")

    season_guest_release = season_sub.add_parser(
        "guest-release",
        help="Release a reserved guest place, optionally filling it with a real RVV team",
    )
    season_guest_release.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_guest_release.add_argument("--tournament-id", required=True, help="Durable tournament id holding the reservation")
    season_guest_release.add_argument("--slot-id", default=None, help="Reservation id (default: first active reservation)")
    season_guest_release.add_argument("--replacement-club", default=None, help="RVV club to fill the released place")
    season_guest_release.add_argument("--replacement-label", default=None, help="RVV team label to fill the released place")
    season_guest_release.add_argument("--replacement-age-group", default=None, help="Replacement team's age group (default: tournament age group)")
    season_guest_release.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_guest_release.add_argument("--actor", default=None, help="Operator identity")
    season_guest_release.add_argument("--note", default="", help="Release reason for decisions history")
    season_guest_release.add_argument("--dry-run", action="store_true", help="Validate and preview without writing canonical state")
    season_guest_release.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
    season_guest_release.add_argument("--json", action="store_true", help="Print updated schedule.json as JSON")

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
    _add_operational_opt_in_flags(season_apply)

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
    _add_operational_opt_in_flags(season_replan)

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

    season_repair = season_sub.add_parser(
        "repair-options",
        help="Enumerate deterministic repair options for one selected finding",
    )
    season_repair.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_repair.add_argument("--finding", required=True, help="Finding id from 'season findings'")
    season_repair.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_repair.add_argument(
        "--search",
        dest="allow_search",
        action="store_true",
        help="Also run the bounded finding-directed search",
    )
    season_repair.add_argument("--json", action="store_true", help="Print options as JSON")
    _add_operational_opt_in_flags(season_repair)

    season_search = season_sub.add_parser(
        "search",
        help="Run a bounded search for one finding and return verified non-dominated options",
    )
    season_search.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_search.add_argument("--finding", required=True, help="Finding id from 'season findings'")
    season_search.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_search.add_argument(
        "--dimensions",
        default="participants,host",
        help="Comma-separated search dimensions (default: participants,host)",
    )
    season_search.add_argument("--json", action="store_true", help="Print search result as JSON")
    _add_operational_opt_in_flags(season_search)

    season_apply_repair = season_sub.add_parser(
        "apply-repair",
        help="Apply one verified repair option to canonical state atomically",
    )
    season_apply_repair.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_apply_repair.add_argument("--option-id", required=True, help="Option id from repair-options/search")
    season_apply_repair.add_argument(
        "--expected-revision",
        required=True,
        help="Canonical revision the option was derived from (stale revisions are rejected)",
    )
    season_apply_repair.add_argument("--finding", default=None, help="Finding id the option belongs to")
    season_apply_repair.add_argument(
        "--dimensions",
        default="participants,host",
        help=(
            "Comma-separated search dimensions fallback for non-search options "
            "(a search option recovers its own dimensions from its option id)"
        ),
    )
    season_apply_repair.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_apply_repair.add_argument("--actor", default=None, help="Operator identity for the apply record")
    season_apply_repair.add_argument(
        "--dry-run", action="store_true", help="Validate and preview the repair without writing canonical state"
    )
    season_apply_repair.add_argument("--json", action="store_true", help="Print the apply result/delta as JSON")
    _add_operational_opt_in_flags(season_apply_repair)

    season_accept_deviation = season_sub.add_parser(
        "accept-deviation",
        help="Persist explicit operator acceptance of one participation strong-goal deviation",
    )
    season_accept_deviation.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_accept_deviation.add_argument(
        "--finding", required=True, help="Participation finding id from 'season findings'"
    )
    season_accept_deviation.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_accept_deviation.add_argument("--actor", default=None, help="Operator identity for the acceptance record")
    season_accept_deviation.add_argument("--note", default="", help="Why this deviation is accepted")
    season_accept_deviation.add_argument("--json", action="store_true", help="Print the acceptance result as JSON")

    season_revoke_acceptance = season_sub.add_parser(
        "revoke-acceptance",
        help="Revoke an active operator participation acceptance",
    )
    season_revoke_acceptance.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_revoke_acceptance.add_argument(
        "--finding", required=True, help="Participation finding id from 'season findings'"
    )
    season_revoke_acceptance.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_revoke_acceptance.add_argument("--actor", default=None, help="Operator identity for the revoke record")
    season_revoke_acceptance.add_argument("--note", default="", help="Why the acceptance is revoked")
    season_revoke_acceptance.add_argument("--json", action="store_true", help="Print the revoke result as JSON")

    # stage3
    stage3 = sub.add_parser(
        "stage3",
        help="Inspect the explicit interactive Stage 3 session/state machine",
    )
    stage3_sub = stage3.add_subparsers(dest="stage3_command", title="stage3 commands")
    stage3_session = stage3_sub.add_parser(
        "session",
        help="Show one compact view of the interactive Stage 3 session for the active run",
    )
    stage3_session.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory (default: .pipeline)")
    stage3_session.add_argument("--json", action="store_true", help="Print the session/status view as JSON")
    stage3_status = stage3_sub.add_parser(
        "status",
        help="Alias for 'stage3 session' (compact interactive Stage 3 status)",
    )
    stage3_status.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory (default: .pipeline)")
    stage3_status.add_argument("--json", action="store_true", help="Print the session/status view as JSON")
    stage3_refine = stage3_sub.add_parser(
        "refine",
        help=(
            "Refine a finalized unpromoted candidate without promotion, reset "
            "or a Stage 1/2 rerun"
        ),
    )
    stage3_refine.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory (default: .pipeline)")
    stage3_refine.add_argument("--input", default=None, help="Input workbook path (defaults to the run's)")
    stage3_refine.add_argument("--finding", default=None, help="Finding id to repair (omit to list findings)")
    stage3_refine.add_argument("--option-id", default=None, help="Verified option id to apply")
    stage3_refine.add_argument("--search", action="store_true", help="Allow the bounded search dimensions")
    stage3_refine.add_argument("--dry-run", action="store_true", help="Preview the verified delta without mutating")
    stage3_refine.add_argument("--no-export", action="store_true", help="Mutate the candidate without re-running Stage 4")
    stage3_refine.add_argument("--export-dir", default=None, help="Export root (defaults to the reviewed export's root)")
    stage3_refine.add_argument("--flat-export", action="store_true", help="Write the export flat instead of timestamped")
    stage3_refine.add_argument("--non-strict", action="store_true", help="Continue on non-fatal export errors")
    stage3_refine.add_argument("--rationale", default="", help="Concise operational rationale")
    stage3_refine.add_argument("--json", action="store_true", help="Print the result as JSON")
    stage3_converge = stage3_sub.add_parser(
        "converge",
        help=(
            "Autonomously refine a REVIEW_REQUIRED unpromoted candidate toward a "
            "bounded Pareto-stable frontier instead of escalating immediately"
        ),
    )
    stage3_converge.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory (default: .pipeline)")
    stage3_converge.add_argument("--input", default=None, help="Input workbook path (defaults to the run's)")
    stage3_converge.add_argument("--finding", default=None, help="Force the first epoch to this finding id")
    stage3_converge.add_argument(
        "--option-id",
        default=None,
        help="Prefer this verified option as the next exploration baseline when it is non-dominated",
    )
    stage3_converge.add_argument("--max-epochs", type=int, default=6, help="Bounded controller epoch budget (default: 6)")
    stage3_converge.add_argument(
        "--max-no-improvement",
        type=int,
        default=2,
        help="Consecutive epochs without a new non-dominated candidate before plateau (default: 2)",
    )
    stage3_converge.add_argument("--frontier-limit", type=int, default=6, help="Bounded frontier size (default: 6)")
    stage3_converge.add_argument("--no-search", action="store_true", help="Only use cheap repair options, not bounded search")
    stage3_converge.add_argument("--no-export", action="store_true", help="Mutate candidates without re-running Stage 4")
    stage3_converge.add_argument("--export-dir", default=None, help="Export root for the batch review exports (defaults to the reviewed export's root)")
    stage3_converge.add_argument("--flat-export", action="store_true", help="Write the batch export flat instead of timestamped")
    stage3_converge.add_argument("--non-strict", action="store_true", help="Continue on non-fatal export errors")
    stage3_converge.add_argument(
        "--review-candidate",
        default=None,
        help=(
            "Retained frontier candidate ref to adopt as the review handoff "
            "before the batch export (default: record the recommended candidate)"
        ),
    )
    stage3_converge.add_argument("--dry-run", action="store_true", help="Preview the next epoch without mutating")
    stage3_converge.add_argument(
        "--ignore-audit",
        action="store_true",
        help="Do not read the persisted semantic-audit verdict for this export",
    )
    stage3_converge.add_argument("--json", action="store_true", help="Print the convergence report as JSON")
    stage3_adopt = stage3_sub.add_parser(
        "adopt",
        help="Adopt one still-valid retained frontier candidate as the current candidate",
    )
    stage3_adopt.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory (default: .pipeline)")
    stage3_adopt.add_argument("--input", default=None, help="Input workbook path (defaults to the run's)")
    stage3_adopt.add_argument("--candidate-ref", required=True, help="Retained frontier/attempt candidate ref")
    stage3_adopt.add_argument("--no-export", action="store_true", help="Adopt without re-running Stage 4")
    stage3_adopt.add_argument("--export-dir", default=None, help="Export root (defaults to the reviewed export's root)")
    stage3_adopt.add_argument("--flat-export", action="store_true", help="Write the export flat instead of timestamped")
    stage3_adopt.add_argument("--non-strict", action="store_true", help="Continue on non-fatal export errors")
    stage3_adopt.add_argument("--rationale", default="", help="Concise operational rationale")
    stage3_adopt.add_argument("--json", action="store_true", help="Print the result as JSON")

    # cancel
    cancel = sub.add_parser("cancel", help="Cancel a tournament and suggest/reschedule makeup dates")
    cancel.add_argument(
        "--tournament-id",
        default=None,
        help="ID of the tournament to cancel (omit to list available tournaments)",
    )
    cancel.add_argument(
        "--reason",
        default=None,
        help="Cancellation reason, e.g. 'Ishall stengt — vannlekkasje'",
    )
    cancel.add_argument(
        "--makeup-date",
        default=None,
        help="Apply a makeup date immediately (YYYY-MM-DD). If omitted, suggestions are shown.",
    )
    cancel.add_argument(
        "--no-export",
        action="store_true",
        help="Skip re-export after cancellation/makeup",
    )
    cancel.add_argument(
        "--force",
        action="store_true",
        help="Force the date move even when conflicts are detected",
    )
    cancel.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    cancel.add_argument(
        "--export-dir",
        default="export",
        help="Export output directory (default: export)",
    )

    # replan — one-shot cancel + move + re-export
    replan = sub.add_parser("replan", help="One-shot replan: move a tournament to a new date and re-export")
    replan.add_argument("--tournament-id", required=True, help="ID of the tournament to replan")
    replan.add_argument(
        "--new-date", default=None,
        help="New date for the tournament (YYYY-MM-DD). Required unless --suggest.",
    )
    replan.add_argument(
        "--suggest", action="store_true",
        help="Show suggested makeup dates instead of applying a move",
    )
    replan.add_argument("--reason", default=None, help="Reason for the replan (e.g. 'Ishall stengt')")
    replan.add_argument("--force", action="store_true", help="Force the move even when conflicts are detected")
    replan.add_argument(
        "--work-dir", default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    replan.add_argument(
        "--export-dir", default="export",
        help="Export output directory (default: export)",
    )
    replan.add_argument(
        "--no-timestamped-export",
        dest="timestamped_export",
        action="store_false",
        help="Write exports flat into --export-dir instead of a timestamped subfolder",
    )
    replan.set_defaults(timestamped_export=True)

    # adjust — manual organizer loop for the final plan
    adjust = sub.add_parser(
        "adjust",
        help="Apply manual organizer adjustments (lock/ban/pin/host rules) and re-export",
    )
    adjust.add_argument(
        "--lock-date",
        action="append",
        default=[],
        help="Lock a tournament date (repeatable, YYYY-MM-DD)",
    )
    adjust.add_argument(
        "--ban-date",
        action="append",
        default=[],
        help="Ban a tournament date from future planning (repeatable, YYYY-MM-DD)",
    )
    adjust.add_argument(
        "--pin-tournament",
        action="append",
        default=[],
        help="Pin a tournament ID so it is preserved during adjustments",
    )
    adjust.add_argument(
        "--force-host-club",
        action="append",
        default=[],
        help="Prefer this club as host when reapplying host rules (repeatable)",
    )
    adjust.add_argument(
        "--exclude-host-club",
        action="append",
        default=[],
        help="Exclude this club from host selection (repeatable)",
    )
    adjust.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    adjust.add_argument(
        "--export-dir",
        default="export",
        help="Export output directory (default: export)",
    )
    adjust.add_argument(
        "--no-timestamped-export",
        dest="timestamped_export",
        action="store_false",
        help="Write exports flat into --export-dir instead of a timestamped subfolder",
    )
    adjust.set_defaults(timestamped_export=False)

    # review — apply club responses from review packets
    review = sub.add_parser(
        "review",
        help="Apply club review responses (accept/change-request) and re-export",
    )
    review.add_argument(
        "--response",
        action="append",
        required=True,
        help="Response file or packet directory with response_template.json (repeatable)",
    )
    review.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    review.add_argument(
        "--export-dir",
        default="export",
        help="Export output directory (default: export)",
    )
    review.add_argument(
        "--no-timestamped-export",
        dest="timestamped_export",
        action="store_false",
        help="Write exports flat into --export-dir instead of a timestamped subfolder",
    )
    review.set_defaults(timestamped_export=False)

    # tournament — add/remove/list/cancel tournaments
    t_sub = sub.add_parser("tournament", help="Manage tournaments: list, add, remove, cancel")
    t_cmds = t_sub.add_subparsers(dest="t_command", title="tournament commands")

    t_list = t_cmds.add_parser("list", help="List all tournaments in the season plan")
    t_list.add_argument(
        "--work-dir", default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )

    t_add = t_cmds.add_parser("add", help="Add a new tournament to the season plan")
    t_add.add_argument("--age-group", required=True, help="Age group (e.g. U10, JU12)")
    t_add.add_argument("--teams", required=True, help="Comma-separated team labels (e.g. 'Jar 1,Kongsberg 1')")
    t_add.add_argument("--date", required=True, help="Tournament date (YYYY-MM-DD)")
    t_add.add_argument("--arena", required=True, help="Host arena (e.g. Kongsberghallen)")
    t_add.add_argument("--host-club", default=None, help="Host club (inferred from teams if omitted)")
    t_add.add_argument("--force", action="store_true", help="Skip conflict checking")
    t_add.add_argument(
        "--work-dir", default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    t_add.add_argument(
        "--export-dir", default="export",
        help="Export output directory (default: export)",
    )

    t_remove = t_cmds.add_parser("remove", help="Remove a tournament entirely from the season plan")
    t_remove.add_argument("--tournament-id", required=True, help="ID of the tournament to remove")
    t_remove.add_argument(
        "--work-dir", default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    t_remove.add_argument(
        "--export-dir", default="export",
        help="Export output directory (default: export)",
    )

    t_cancel = t_cmds.add_parser("cancel", help="Cancel a tournament and suggest/reschedule makeup dates")
    t_cancel.add_argument("--tournament-id", default=None, help="ID to cancel (omit to list)")
    t_cancel.add_argument("--reason", default=None, help="Cancellation reason")
    t_cancel.add_argument("--makeup-date", default=None, help="Makeup date (YYYY-MM-DD)")
    t_cancel.add_argument("--no-export", action="store_true", help="Skip re-export")
    t_cancel.add_argument("--force", action="store_true", help="Force date move")
    t_cancel.add_argument(
        "--work-dir", default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    t_cancel.add_argument(
        "--export-dir", default="export",
        help="Export output directory (default: export)",
    )

    # critic
    critic = sub.add_parser(
        "critic",
        help="Run the plan critic on an existing Stage 3 checkpoint and print issues",
    )
    critic.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )

    # auto-adjust
    auto_adjust = sub.add_parser(
        "auto-adjust",
        help=(
            "Automatically apply auto-fixable critic issues (arena-day collisions, "
            "hosting clumps) in a loop until resolved or max iterations reached"
        ),
    )
    auto_adjust.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    auto_adjust.add_argument(
        "--export-dir",
        default="export",
        help="Export output directory (default: export)",
    )
    auto_adjust.add_argument(
        "--max-iterations",
        type=int,
        default=3,
        help="Maximum number of adjustment iterations (default: 3)",
    )
    auto_adjust.add_argument(
        "--no-timestamped-export",
        dest="timestamped_export",
        action="store_false",
        help="Write exports flat into --export-dir instead of a timestamped subfolder",
    )
    auto_adjust.set_defaults(timestamped_export=True)

    # verdict
    verdict = sub.add_parser(
        "verdict",
        help="Read the Stage 3 checkpoint and print the tone (strong/mixed/rough) and key scores",
    )
    verdict.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )

    # candidates
    candidates = sub.add_parser(
        "candidates",
        help="Compare Stage 3 plan candidates (from --iterations > 1): reproducibility "
        "metadata, ranking, and the most consequential trade-offs vs. the runner-up",
    )
    candidates.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    candidates.add_argument(
        "--json",
        action="store_true",
        help="Print candidates as JSON instead of a human-readable comparison",
    )

    # plan — harness-neutral candidate verification/scoring (issue #257)
    plan = sub.add_parser(
        "plan",
        help="Deterministically verify/score a Stage 3 candidate plan, independent of SeasonPlanner",
    )
    plan_sub = plan.add_subparsers(dest="plan_command", title="plan commands")

    plan_verify = plan_sub.add_parser(
        "verify",
        help="Check a candidate plan against hard planning requirements",
    )
    plan_verify.add_argument(
        "candidate",
        help="Path to a candidate.json, or a Stage 3 checkpoint file containing a 'plan' key",
    )
    plan_verify.add_argument(
        "--problem",
        default=None,
        help="Path to a planning_problem.json for full hard-constraint checks "
        "(registered teams, calendar validity, participation targets, manual restrictions). "
        "Without it, only self-consistency checks run.",
    )
    plan_verify.add_argument(
        "--json",
        action="store_true",
        help="Print the verification result as JSON instead of a human-readable report",
    )

    plan_score = plan_sub.add_parser(
        "score",
        help="Compute deterministic quality metrics for a candidate plan",
    )
    plan_score.add_argument(
        "candidate",
        help="Path to a candidate.json, or a Stage 3 checkpoint file containing a 'plan' key",
    )
    plan_score.add_argument(
        "--problem",
        default=None,
        help="Path to a planning_problem.json to also report club x age-group hosting "
        "coverage (issue #266). Without it, hosting metrics only include the "
        "club-level counts/spread.",
    )
    plan_score.add_argument(
        "--json",
        action="store_true",
        help="Print the score report as JSON instead of a human-readable summary",
    )

    plan_optimize = plan_sub.add_parser(
        "optimize",
        help="Generic local-search repair pass over a candidate's existing tournament "
        "skeleton (issue #257 Stage 3 v2 optimizer, explicit opt-in — never run implicitly)",
    )
    plan_optimize.add_argument(
        "candidate",
        help="Path to a candidate.json, or a Stage 3 checkpoint file containing a 'plan' key",
    )
    plan_optimize.add_argument(
        "--problem",
        default=None,
        help="Path to a planning_problem.json (used to size tournaments per age group; "
        "falls back to inferring capacity from the candidate's own games)",
    )
    plan_optimize.add_argument(
        "--iterations",
        type=int,
        default=4000,
        help="Simulated-annealing swap attempts (default: 4000)",
    )
    plan_optimize.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Random seed for reproducible output (default: 0)",
    )
    plan_optimize.add_argument(
        "--output",
        "-o",
        default=None,
        help="Write the optimized candidate.json to this path instead of stdout",
    )
    plan_optimize.add_argument(
        "--weight",
        action="append",
        dest="weights",
        default=None,
        metavar="NAME=VALUE",
        help="Override one objective weight (e.g. --weight gap_under_7=8.0), or just one "
        "age group's weight (e.g. --weight JU12:same_club_pairing=1.5); repeatable. "
        "See stage3_optimizer.DEFAULT_WEIGHTS for the tunable names",
    )
    plan_optimize.add_argument(
        "--move-dates",
        action="store_true",
        help="Also let the search swap two same-age-group tournaments' dates (arena/host/"
        "teams stay put), not just teams between tournaments. Off by default — the "
        "skeleton (dates/arenas/hosts) is otherwise taken as given",
    )
    plan_optimize.add_argument(
        "--engine",
        choices=["local-search", "cp-sat"],
        default="local-search",
        help="Search engine to use (issue #276 Phase 1): 'local-search' (default, existing "
        "simulated-annealing repair pass) or 'cp-sat' (fixed-skeleton participant-only "
        "shadow engine — requires OR-Tools; --iterations/--weight/--move-dates are ignored)",
    )
    plan_optimize.add_argument(
        "--solve-budget-seconds",
        type=float,
        default=30.0,
        help="For --engine cp-sat: bounded wall-clock solve budget in seconds (default: 30.0)",
    )

    plan_ab = plan_sub.add_parser(
        "ab",
        help="Full-season old-vs-new benchmark: baseline SeasonPlanner (Stage 3 checkpoint) vs. "
        "the Stage 3 v2 optimizer, from the same normalized planning problem (issue #257)",
    )
    plan_ab.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory containing Stage 1-3 checkpoints (default: .pipeline)",
    )
    plan_ab.add_argument(
        "--start-date",
        default=None,
        help="Season start date (YYYY-MM-DD); defaults to the Stage 3 checkpoint's plan window",
    )
    plan_ab.add_argument(
        "--end-date",
        default=None,
        help="Season end date (YYYY-MM-DD); defaults to the Stage 3 checkpoint's plan window",
    )
    plan_ab.add_argument(
        "--iterations",
        type=int,
        default=4000,
        help="Simulated-annealing swap attempts for the new candidate (default: 4000)",
    )
    plan_ab.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Random seed for the new candidate (default: 0)",
    )
    plan_ab.add_argument(
        "--output-dir",
        default=None,
        help="Write old_candidate.json/new_candidate.json/ab_report.json to this directory",
    )
    plan_ab.add_argument(
        "--json",
        action="store_true",
        help="Print the A/B report as JSON instead of a human-readable summary",
    )
    plan_ab.add_argument(
        "--weight",
        action="append",
        dest="weights",
        default=None,
        metavar="NAME=VALUE",
        help="Override one optimizer objective weight for the new candidate "
        "(e.g. --weight gap_under_7=8.0), or just one age group's weight "
        "(e.g. --weight JU12:same_club_pairing=1.5); repeatable. See "
        "stage3_optimizer.DEFAULT_WEIGHTS for the tunable names",
    )
    plan_ab.add_argument(
        "--move-dates",
        action="store_true",
        help="Also let the new candidate's search swap two same-age-group tournaments' "
        "dates, not just teams between tournaments. Off by default",
    )
    plan_ab.add_argument(
        "--engine",
        choices=["local-search", "cp-sat"],
        default="local-search",
        help="Search engine to use for the new candidate (issue #276 Phase 1): 'local-search' "
        "(default) or 'cp-sat' (fixed-skeleton participant-only shadow engine — requires "
        "OR-Tools; --iterations/--weight/--move-dates are ignored)",
    )
    plan_ab.add_argument(
        "--solve-budget-seconds",
        type=float,
        default=30.0,
        help="For --engine cp-sat: bounded wall-clock solve budget in seconds (default: 30.0)",
    )

    plan_ab_participants = plan_sub.add_parser(
        "ab-participants",
        help="Full-season baseline-bounded participant-optimization benchmark: for each age "
        "group independently, keep the Stage 3 checkpoint's assignment unless a seeded "
        "search finds a strict, non-regressing improvement (issue #257 Task 2-4)",
    )
    plan_ab_participants.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory containing Stage 1-3 checkpoints (default: .pipeline)",
    )
    plan_ab_participants.add_argument(
        "--start-date",
        default=None,
        help="Season start date (YYYY-MM-DD); defaults to the Stage 3 checkpoint's plan window",
    )
    plan_ab_participants.add_argument(
        "--end-date",
        default=None,
        help="Season end date (YYYY-MM-DD); defaults to the Stage 3 checkpoint's plan window",
    )
    plan_ab_participants.add_argument(
        "--seeds",
        default="1,2,3,4,5",
        help="Comma-separated list of seeds/restarts to try per age group (default: 1,2,3,4,5)",
    )
    plan_ab_participants.add_argument(
        "--iterations",
        type=int,
        default=4000,
        help="Simulated-annealing swap attempts per seed per age group (default: 4000)",
    )
    plan_ab_participants.add_argument(
        "--output-dir",
        default=None,
        help="Write problem.json/old_candidate.json/new_candidate.json/ab_report.json to this directory",
    )
    plan_ab_participants.add_argument(
        "--json",
        action="store_true",
        help="Print the A/B report as JSON instead of a human-readable summary",
    )
    plan_ab_participants.add_argument(
        "--repair-schedule",
        action="store_true",
        default=True,
        help="Also run the baseline-bounded date-swap-only schedule-conflict repair pass "
        "after participant optimization, to fix hard violations like arena_interval_conflict "
        "that a participant-only optimizer cannot (default: on)",
    )
    plan_ab_participants.add_argument(
        "--no-repair-schedule",
        action="store_false",
        dest="repair_schedule",
        help="Skip the schedule-conflict repair pass; only optimize team assignments",
    )

    # plan decision-context / plan decide — Stage 3 optimizer as an
    # LLM-directed decision (issue #260 Phase 4). Turns a "plan ab"/
    # "plan ab-participants" report into a DecisionContext, and validates +
    # records + (when accepted) executes the LLM's chosen DecisionAction,
    # instead of a Python quality heuristic deciding automatically.
    plan_decision_context = plan_sub.add_parser(
        "decision-context",
        help="Build the DecisionContext for an old-vs-new Stage 3 A/B report, for an "
        "LLM/agent controller to choose apply_candidate/keep_baseline/optimize_plan/"
        "request_operator (issue #260 Phase 4)",
    )
    plan_decision_context.add_argument(
        "ab_report",
        help="Path to an ab_report.json written by 'plan ab'/'plan ab-participants' "
        "(--output-dir)",
    )
    plan_decision_context.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory, used to default --run-id from the active run "
        "manifest (default: .pipeline)",
    )
    plan_decision_context.add_argument(
        "--run-id",
        default=None,
        help="Run id to stamp on the DecisionContext; defaults to the active run "
        "manifest's run_id",
    )
    plan_decision_context.add_argument(
        "--baseline-ref",
        default=None,
        help="Reference (e.g. path) to the baseline candidate, carried through unchanged",
    )
    plan_decision_context.add_argument(
        "--candidate-ref",
        default=None,
        help="Reference (e.g. path) to the new candidate, carried through unchanged",
    )
    plan_decision_context.add_argument(
        "--objective",
        default=None,
        help="Override the default decision objective text",
    )
    plan_decision_context.add_argument(
        "--output",
        "-o",
        default=None,
        help="Write the DecisionContext JSON to this path instead of stdout",
    )

    plan_decide = plan_sub.add_parser(
        "decide",
        help="Validate, record, and (when accepted) execute a DecisionAction against an "
        "old-vs-new Stage 3 A/B report (issue #260 Phase 4)",
    )
    plan_decide.add_argument(
        "ab_report",
        help="Path to an ab_report.json written by 'plan ab'/'plan ab-participants' "
        "(--output-dir)",
    )
    plan_decide.add_argument(
        "--action",
        required=False,
        default=None,
        choices=["apply_candidate", "keep_baseline", "optimize_plan", "request_operator"],
        help="The DecisionAction to validate and execute. Required unless --decision-action/"
        "--decision-action-file is given instead",
    )
    plan_decide.add_argument(
        "--decision-action",
        default=None,
        metavar="JSON",
        help="Inline JSON DecisionAction (e.g. '{\"action_id\": \"optimize_plan\", \"arguments\": "
        "{\"iterations\": 8000, \"weights\": {\"gap_under_7\": 8.0}}}'), an alternative to "
        "--action/--target/--candidate/--question that lets the LLM/agent pass genuinely "
        "parameterized optimize_plan search settings in one structured payload instead of "
        "CLI flags choosing them (issue #260 P1). Overrides --action and friends when given",
    )
    plan_decide.add_argument(
        "--decision-action-file",
        default=None,
        metavar="PATH",
        help="Path to a JSON file containing the DecisionAction, alternative to --decision-action",
    )
    plan_decide.add_argument(
        "--rationale",
        default=None,
        help="Concise rationale to record for this decision (never chain-of-thought)",
    )
    plan_decide.add_argument(
        "--target",
        default=None,
        help="Optional DecisionAction target (e.g. age group), recorded for audit",
    )
    plan_decide.add_argument(
        "--candidate",
        default=None,
        help="Path to the new candidate.json; required for --action apply_candidate, which "
        "writes it into the Stage 3 checkpoint's plan. For --action optimize_plan, an "
        "optional starting point to re-optimize from (defaults to the Stage 3 "
        "checkpoint's baseline)",
    )
    plan_decide.add_argument(
        "--question",
        default=None,
        help="Question to record for --action request_operator",
    )
    plan_decide.add_argument(
        "--output-dir",
        default=None,
        help="For --action optimize_plan: write old_candidate.json/new_candidate.json/"
        "ab_report.json from the re-run to this directory, so the result can be fed "
        "back into 'plan decision-context' (required for optimize_plan)",
    )
    plan_decide.add_argument(
        "--problem",
        default=None,
        help="For --action optimize_plan: path to a planning_problem.json (falls back to "
        "inferring capacity from the candidate's own games)",
    )
    plan_decide.add_argument(
        "--iterations",
        type=int,
        default=4000,
        help="For --action optimize_plan: simulated-annealing swap attempts (default: 4000)",
    )
    plan_decide.add_argument(
        "--seed",
        type=int,
        default=0,
        help="For --action optimize_plan: random seed for reproducible output (default: 0)",
    )
    plan_decide.add_argument(
        "--weight",
        action="append",
        dest="weights",
        default=None,
        metavar="NAME=VALUE",
        help="For --action optimize_plan: override one objective weight (e.g. "
        "--weight gap_under_7=8.0), or just one age group's weight (e.g. "
        "--weight JU12:same_club_pairing=1.5); repeatable",
    )
    plan_decide.add_argument(
        "--move-dates",
        action="store_true",
        help="For --action optimize_plan: also let the search swap two same-age-group "
        "tournaments' dates, not just teams between tournaments. Off by default",
    )
    plan_decide.add_argument(
        "--engine",
        choices=["local-search", "cp-sat"],
        default="local-search",
        help="For --action optimize_plan: search engine to re-run (issue #276 Phase 1). "
        "'local-search' (default) or 'cp-sat' (requires OR-Tools)",
    )
    plan_decide.add_argument(
        "--solve-budget-seconds",
        type=float,
        default=30.0,
        help="For --action optimize_plan with --engine cp-sat: bounded wall-clock solve "
        "budget in seconds (default: 30.0)",
    )
    plan_decide.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory whose Stage 3 checkpoint/run manifest this decision "
        "applies to (default: .pipeline)",
    )
    plan_decide.add_argument(
        "--run-id",
        default=None,
        help="Run id to stamp on the DecisionContext; defaults to the active run "
        "manifest's run_id",
    )
    plan_decide.add_argument(
        "--baseline-ref",
        default=None,
        help="Reference (e.g. path) to the baseline candidate, carried through unchanged",
    )
    plan_decide.add_argument(
        "--candidate-ref",
        default=None,
        help="Reference (e.g. path) to the new candidate, carried through unchanged "
        "(defaults to --candidate for apply_candidate)",
    )
    plan_decide.add_argument(
        "--objective",
        default=None,
        help="Override the default decision objective text",
    )
    plan_decide.add_argument(
        "--json",
        action="store_true",
        help="Also print the DecisionResult as JSON",
    )

    plan_problem = plan_sub.add_parser(
        "problem",
        help="Emit a normalized planning_problem.json from the Stage 1/2 checkpoints",
    )
    plan_problem.add_argument(
        "--work-dir",
        default=".pipeline",
        help="Pipeline work directory (default: .pipeline)",
    )
    plan_problem.add_argument(
        "--start-date",
        default=None,
        help="Season start date (YYYY-MM-DD); defaults to the Stage 3 checkpoint's plan window",
    )
    plan_problem.add_argument(
        "--end-date",
        default=None,
        help="Season end date (YYYY-MM-DD); defaults to the Stage 3 checkpoint's plan window",
    )
    plan_problem.add_argument(
        "--output",
        default=None,
        help="Write the planning_problem.json to this path instead of stdout",
    )

    # waiver — explicit operator authorization of hard-rule exceptions
    waiver = sub.add_parser(
        "waiver",
        help="Create/list/revoke narrow, audited operator waivers for hard planning rules",
    )
    waiver_sub = waiver.add_subparsers(dest="waiver_command")

    waiver_list = waiver_sub.add_parser("list", help="List operator waivers for a run")
    waiver_list.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory (default: .pipeline)")
    waiver_list.add_argument("--all", action="store_true", help="Include revoked waivers")
    waiver_list.add_argument("--json", action="store_true", help="Print records as JSON")

    waiver_create = waiver_sub.add_parser(
        "create",
        help="Authorize one narrow operator exception (operator-only; agents may never create one)",
    )
    waiver_create.add_argument(
        "--rule",
        required=True,
        choices=sorted(WAIVABLE_RULE_IDS),
        help="Hard planning rule to waive",
    )
    waiver_create.add_argument("--club", default=None, help="Team's club (disambiguates the team identity)")
    waiver_create.add_argument("--team", required=True, help="Team label, e.g. 'Frisk Asker 4'")
    waiver_create.add_argument("--age-group", default=None, help="Team age group, e.g. U11")
    waiver_create.add_argument(
        "--tournament",
        default=None,
        help="Tournament id the authorized participation lands in (required for participation waivers)",
    )
    waiver_create.add_argument(
        "--half",
        default=None,
        choices=["before_christmas", "after_christmas"],
        help="Season half the exception applies to",
    )
    waiver_create.add_argument(
        "--configured-value",
        type=int,
        default=None,
        help="Configured target being exceeded; validated against the run's configured value",
    )
    waiver_create.add_argument(
        "--allowed-value",
        type=int,
        required=True,
        help="Exact actual count the operator authorizes (e.g. 6 for a 6/5 exception)",
    )
    waiver_create.add_argument("--reason", required=True, help="Operator reason (recorded verbatim in the audit)")
    waiver_create.add_argument("--actor", default=None, help="Operator identity (defaults from RVV_OPERATOR/USER)")
    waiver_create.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory (default: .pipeline)")
    waiver_create.add_argument("--json", action="store_true", help="Print the created record as JSON")

    waiver_revoke = waiver_sub.add_parser("revoke", help="Revoke an active waiver and restore hard verification")
    waiver_revoke.add_argument("waiver_id", help="Waiver id to revoke")
    waiver_revoke.add_argument("--reason", default=None, help="Why the exception is withdrawn")
    waiver_revoke.add_argument("--actor", default=None, help="Operator identity (defaults from RVV_OPERATOR/USER)")
    waiver_revoke.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory (default: .pipeline)")
    waiver_revoke.add_argument("--json", action="store_true", help="Print the revoked record as JSON")

    return parser
