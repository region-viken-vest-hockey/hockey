"""Tournament schedule-change commands: cancel, replan, adjust, review and tournament."""

from __future__ import annotations

import argparse


def add_schedule_change_parsers(sub: argparse._SubParsersAction) -> None:
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
