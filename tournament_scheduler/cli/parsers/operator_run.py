"""Operator run command and its publish-bundling flags."""

from __future__ import annotations

import argparse


def add_operator_run_parsers(operator_sub: argparse._SubParsersAction) -> None:
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
