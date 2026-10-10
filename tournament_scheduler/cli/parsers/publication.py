"""Publication commands: registered-teams and activities pages."""

from __future__ import annotations

import argparse


def add_publication_parsers(sub: argparse._SubParsersAction) -> None:
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
