"""Season promotion, export, sealing, normalization and approval commands."""

from __future__ import annotations

import argparse


def add_season_lifecycle_parsers(season_sub: argparse._SubParsersAction) -> None:
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

    season_publication_evidence = season_sub.add_parser(
        "publication-evidence",
        help="Show the retained publication evidence and the published-to-canonical republish delta",
    )
    season_publication_evidence.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_publication_evidence.add_argument(
        "--root", default="season", help="Canonical season-state root (default: season)"
    )
    season_publication_evidence.add_argument(
        "--json", action="store_true", help="Print the publication evidence report as JSON"
    )

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
    season_unapprove.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory for verification context")
    season_unapprove.add_argument("--json", action="store_true", help="Print updated decisions.json as JSON")

    season_approvals = season_sub.add_parser(
        "approvals", help="List per-tournament approval/lock status, including stale approvals"
    )
    season_approvals.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_approvals.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_approvals.add_argument("--json", action="store_true", help="Print the approval report as JSON")
