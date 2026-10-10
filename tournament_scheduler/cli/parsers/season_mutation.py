"""Season placement mutation commands: move, replace, remove, withdrawals, swap, rename, batch and protections."""

from __future__ import annotations

import argparse

from .common import add_operational_opt_in_flags, add_regression_acceptance_flags, add_reviewed_consequence_flag


def add_season_mutation_parsers(season_sub: argparse._SubParsersAction) -> None:
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
    add_operational_opt_in_flags(season_move)
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
    season_replace.add_argument(
        "--fail-on-blocked",
        action="store_true",
        help="With --dry-run, return exit code 3 when the domain verdict is blocked",
    )
    season_replace.add_argument("--json", action="store_true", help="Print replacement result as JSON")

    season_remove = season_sub.add_parser(
        "remove-participant",
        help=(
            "Remove one participant from one or more canonical tournaments without a replacement; "
            "pass --reconcile-withdrawal for a genuine season/age-group withdrawal"
        ),
    )
    season_remove.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_remove.add_argument(
        "--tournament-id",
        dest="tournament_ids",
        action="append",
        required=True,
        help="Durable tournament id to remove the participant from (repeatable, comma-separated accepted)",
    )
    season_remove.add_argument("--remove-team", required=True, help="Existing participant label to remove")
    season_remove.add_argument(
        "--reconcile-withdrawal",
        action="store_true",
        help=(
            "Record a revision-bound season/age-group withdrawal so the eligible shape pool is "
            "reduced for exactly these tournaments; omit for a one-event absence that fails "
            "closed if the smaller shape is not independently legal"
        ),
    )
    season_remove.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_remove.add_argument("--actor", default=None, help="Operator identity")
    season_remove.add_argument("--note", default="", help="Removal/withdrawal note or reason for decisions history")
    season_remove.add_argument(
        "--request-id",
        required=True,
        help="Stable source/request id recorded on automatic change protections and withdrawal records",
    )
    season_remove.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and preview the removal without writing canonical state",
    )
    add_regression_acceptance_flags(season_remove)
    season_remove.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
    season_remove.add_argument("--json", action="store_true", help="Print removal result as JSON")

    season_withdrawals = season_sub.add_parser(
        "withdrawals",
        help="List canonical participation-withdrawal records and their eligibility status",
    )
    season_withdrawals.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_withdrawals.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_withdrawals.add_argument(
        "--all",
        action="store_true",
        help="Include released withdrawal records (provenance is retained either way)",
    )
    season_withdrawals.add_argument("--json", action="store_true", help="Print withdrawal ledger as JSON")

    season_release_withdrawal = season_sub.add_parser(
        "release-withdrawal",
        help="Release a withdrawal record after a participant is restored or registration is reconciled",
    )
    season_release_withdrawal.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_release_withdrawal.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_release_withdrawal.add_argument(
        "--withdrawal-id",
        dest="withdrawal_ids",
        action="append",
        default=None,
        help="Withdrawal record id to release (repeatable)",
    )
    season_release_withdrawal.add_argument(
        "--request-id",
        default=None,
        help="Release every active withdrawal recorded by this request id",
    )
    season_release_withdrawal.add_argument(
        "--restore-participant",
        action="store_true",
        help=(
            "Atomically add the withdrawn team(s) back to the recorded tournaments, regenerate "
            "their games and release the record in one verified commit (the authorized reversal)"
        ),
    )
    season_release_withdrawal.add_argument("--actor", default=None, help="Operator identity")
    season_release_withdrawal.add_argument("--note", default="", help="Why the withdrawal is being released (audit reason)")
    season_release_withdrawal.add_argument("--json", action="store_true", help="Print release result as JSON")

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
    add_regression_acceptance_flags(season_swap)
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
    add_operational_opt_in_flags(season_batch)
    add_regression_acceptance_flags(season_batch)
    add_reviewed_consequence_flag(season_batch)
    season_batch.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
    season_batch.add_argument(
        "--fail-on-blocked",
        action="store_true",
        help="With --dry-run, return exit code 3 when the domain verdict is blocked",
    )
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
