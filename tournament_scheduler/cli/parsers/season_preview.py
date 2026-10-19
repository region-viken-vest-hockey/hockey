"""Parser for the read-only ``season preview`` consequence command."""

from __future__ import annotations

import argparse

from .common import add_operational_opt_in_flags


def add_season_preview_parser(season_sub: argparse._SubParsersAction) -> None:
    season_preview = season_sub.add_parser(
        "preview",
        help="Read-only consequence preview for one scoped canonical operation (replace, swap, move or remove)",
    )
    season_preview.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_preview.add_argument(
        "--operation",
        required=True,
        choices=["replace", "swap", "move", "remove", "batch"],
        help="Scoped canonical operation to preview",
    )
    season_preview.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_preview.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
    season_preview.add_argument(
        "--expected-revision",
        default=None,
        help="Canonical revision the preview was reviewed against; a different current revision is reported as stale",
    )
    season_preview.add_argument("--tournament-id", default=None, help="Durable tournament id (replace, move)")
    season_preview.add_argument("--tournament-ids", nargs="+", default=None, help="Durable tournament ids (remove)")
    season_preview.add_argument("--remove-team", default=None, help="Participant label to remove (replace, remove)")
    season_preview.add_argument("--add-team", default=None, help="Participant label to add (replace)")
    season_preview.add_argument("--tournament-a-id", default=None, help="First tournament id (swap)")
    season_preview.add_argument("--team-a", default=None, help="Participant label in the first tournament (swap)")
    season_preview.add_argument("--tournament-b-id", default=None, help="Second tournament id (swap)")
    season_preview.add_argument("--team-b", default=None, help="Participant label in the second tournament (swap)")
    season_preview.add_argument("--date", default=None, help="Candidate date (move)")
    season_preview.add_argument("--arena", default=None, help="Candidate arena (move)")
    season_preview.add_argument("--host-club", default=None, help="Candidate host club (move)")
    season_preview.add_argument("--start-time", default=None, help="Candidate start time (move)")
    season_preview.add_argument("--allow-cross-half", action="store_true", help="Allow a move across the Christmas boundary (move)")
    add_operational_opt_in_flags(season_preview)
    season_preview.add_argument(
        "--reconcile-withdrawal",
        action="store_true",
        help="Reconcile the withdrawal for a genuine season/age-group withdrawal (remove)",
    )
    season_preview.add_argument(
        "--operations-file",
        default=None,
        help="Scenario JSON file for a batch preview (same format as season batch --operations)",
    )
    season_preview.add_argument(
        "--scope",
        action="append",
        default=None,
        help="Affected tournament id for a batch preview (repeatable, comma-separated accepted)",
    )
    season_preview.add_argument("--actor", default=None, help="Operator identity")
    season_preview.add_argument("--note", default="", help="Reason recorded with the previewed operation")
    season_preview.add_argument("--request-id", default=None, help="Stable source/request id for the previewed operation")
    season_preview.add_argument(
        "--fail-on-blocked",
        action="store_true",
        help="Return exit code 3 unless the preview verdict is safe_to_apply",
    )
    season_preview.add_argument("--json", action="store_true", help="Print the normalized preview as JSON")
