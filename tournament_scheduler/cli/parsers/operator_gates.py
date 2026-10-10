"""Operator review, publication, verification and rollback commands."""

from __future__ import annotations

import argparse


def add_operator_gate_parsers(operator_sub: argparse._SubParsersAction) -> None:
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
