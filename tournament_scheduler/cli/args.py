"""Argument parsing for the RVV Miniputt CLI."""

from __future__ import annotations

import argparse


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
        "--manual-bookup-login",
        action="store_true",
        help="Open BookUp in a visible browser and wait for manual Vipps/SMS MFA during Stage 2",
    )
    run.add_argument(
        "--manual-bookup-login-timeout",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Maximum seconds to wait for manual BookUp MFA/login (default: 300)",
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
    scrape.add_argument(
        "--manual-bookup-login",
        action="store_true",
        help="For BookUp sources, use a visible browser and wait for manual Vipps/SMS MFA",
    )
    scrape.add_argument(
        "--manual-bookup-login-timeout",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Maximum seconds to wait for manual BookUp MFA/login (default: 300)",
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
        help="Source name to patch (e.g. 'Sandefjord', 'Tønsberg')",
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

    return parser
