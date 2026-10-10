"""Season constraint, banned-date and holiday-date commands."""

from __future__ import annotations

import argparse


def add_season_constraint_parsers(season_sub: argparse._SubParsersAction) -> None:
    season_constraints = season_sub.add_parser(
        "constraints",
        help="List typed request constraints and their derived current satisfaction",
    )
    season_constraints.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_constraints.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_constraints.add_argument("--all", action="store_true", help="Include released constraints")
    season_constraints.add_argument("--json", action="store_true", help="Print the constraint report as JSON")

    season_changes = season_sub.add_parser(
        "changes",
        help="Project canonical decisions into a request-grouped change ledger",
    )
    season_changes.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_changes.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_changes.add_argument("--json", action="store_true", help="Print the stable ledger schema as JSON")
    season_changes.add_argument("--markdown", action="store_true", help="Print the generated Markdown ledger")
    season_changes.add_argument(
        "--write",
        action="store_true",
        help="Write season/<season>/change-log.md from the projection",
    )

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
    season_ban_date.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
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
    season_unban_date.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
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
    season_banned_dates.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
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
