"""Season inspection, repair search, deviation acceptance and team retirement commands."""

from __future__ import annotations

import argparse

from .common import add_operational_opt_in_flags


def add_season_repair_parsers(season_sub: argparse._SubParsersAction) -> None:
    season_compact = season_sub.add_parser(
        "compact-history",
        help="Migrate oversized inline move-verification evidence into the durable archive",
    )
    season_compact.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_compact.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_compact.add_argument("--actor", default=None, help="Operator identity for the compaction record")
    season_compact.add_argument("--note", default="", help="Optional note for the compaction")
    season_compact.add_argument(
        "--dry-run", action="store_true", help="Report what compaction would change without writing"
    )
    season_compact.add_argument(
        "--apply", action="store_true", help="Apply the compaction (backup + verify + atomic swap)"
    )
    season_compact.add_argument("--json", action="store_true", help="Print the compaction report as JSON")

    season_inventory = season_sub.add_parser(
        "inventory",
        help="Read-only size/shape inventory of season, export and pipeline artifacts",
    )
    season_inventory.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_inventory.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_inventory.add_argument("--export-root", default="export", help="Export root directory (default: export)")
    season_inventory.add_argument("--pipeline-root", default=".pipeline", help="Pipeline work directory (default: .pipeline)")
    season_inventory.add_argument("--json", action="store_true", help="Print the inventory as JSON")

    season_inspect = season_sub.add_parser(
        "inspect",
        help="Read-only domain inspection of one tournament/roster/status, filtered constraints or replacement candidates",
    )
    season_inspect_sub = season_inspect.add_subparsers(dest="inspect_command", title="inspect commands")

    season_inspect_tournament = season_inspect_sub.add_parser(
        "tournament",
        help="Inspect one canonical tournament: placement, roster, approval/booking state and relevant constraints",
    )
    season_inspect_tournament.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_inspect_tournament.add_argument("--tournament-id", required=True, help="Durable tournament id to inspect")
    season_inspect_tournament.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_inspect_tournament.add_argument("--all", action="store_true", help="Include released constraints")
    season_inspect_tournament.add_argument("--json", action="store_true", help="Print the inspection as JSON")

    season_inspect_constraints = season_inspect_sub.add_parser(
        "constraints",
        help="Inspect request constraints filtered by team, tournament or date",
    )
    season_inspect_constraints.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_inspect_constraints.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_inspect_constraints.add_argument("--team", default=None, help="Filter by team club or label")
    season_inspect_constraints.add_argument("--tournament-id", default=None, help="Only constraints relevant to this tournament")
    season_inspect_constraints.add_argument("--date", default=None, help="Only constraints whose date window contains YYYY-MM-DD")
    season_inspect_constraints.add_argument("--all", action="store_true", help="Include released constraints")
    season_inspect_constraints.add_argument("--json", action="store_true", help="Print the constraint report as JSON")

    season_inspect_candidates = season_inspect_sub.add_parser(
        "candidates",
        help="List registered same-age replacement candidates for one tournament",
    )
    season_inspect_candidates.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_inspect_candidates.add_argument("--tournament-id", required=True, help="Durable tournament id to find candidates for")
    season_inspect_candidates.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_inspect_candidates.add_argument(
        "--replace",
        dest="replace_team",
        default=None,
        help=(
            "Participant label being replaced; validates each candidate read-only "
            "through the replacement gates and returns an explicit verdict"
        ),
    )
    season_inspect_candidates.add_argument(
        "--legal-only",
        action="store_true",
        help="Only include candidates the read-only validation marks safe to apply",
    )
    season_inspect_candidates.add_argument("--limit", type=int, default=None, help="Maximum number of candidates to return")
    season_inspect_candidates.add_argument("--json", action="store_true", help="Print the candidates as JSON")

    season_repair = season_sub.add_parser(
        "repair-options",
        help="Enumerate deterministic repair options for one selected finding",
    )
    season_repair.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_repair.add_argument("--finding", required=True, help="Finding id from 'season findings'")
    season_repair.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_repair.add_argument(
        "--age-group",
        default=None,
        help="Age-group selector for legacy participation finding ids (for example U9)",
    )
    season_repair.add_argument(
        "--search",
        dest="allow_search",
        action="store_true",
        help="Also run the bounded finding-directed search",
    )
    season_repair.add_argument("--json", action="store_true", help="Print options as JSON")
    add_operational_opt_in_flags(season_repair)

    season_search = season_sub.add_parser(
        "search",
        help="Run a bounded search for one finding and return verified non-dominated options",
    )
    season_search.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_search.add_argument("--finding", required=True, help="Finding id from 'season findings'")
    season_search.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_search.add_argument(
        "--age-group",
        default=None,
        help="Age-group selector for legacy participation finding ids (for example U9)",
    )
    season_search.add_argument(
        "--dimensions",
        default="participants,host",
        help="Comma-separated search dimensions (default: participants,host)",
    )
    season_search.add_argument("--json", action="store_true", help="Print search result as JSON")
    add_operational_opt_in_flags(season_search)

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
        "--age-group",
        default=None,
        help="Age-group selector for legacy participation finding ids (for example U9)",
    )
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
    season_apply_repair.add_argument(
        "--accept-regression",
        dest="accept_regressions",
        action="append",
        default=None,
        metavar="CODE",
        help=(
            "Explicitly accept one named cross-rule material regression (for example "
            "'more_gaps_under_7_days'); repeatable and never the default"
        ),
    )
    season_apply_repair.add_argument(
        "--accept-regression-reason",
        default=None,
        help="Mandatory operator reason recorded with any accepted cross-rule regression",
    )
    add_operational_opt_in_flags(season_apply_repair)

    season_accept_deviation = season_sub.add_parser(
        "accept-deviation",
        help="Persist explicit operator acceptance of one participation strong-goal deviation",
    )
    season_accept_deviation.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_accept_deviation.add_argument(
        "--finding", required=True, help="Participation finding id from 'season findings'"
    )
    season_accept_deviation.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_accept_deviation.add_argument(
        "--age-group",
        default=None,
        help="Age-group selector for legacy participation finding ids (for example U9)",
    )
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
    season_revoke_acceptance.add_argument(
        "--age-group",
        default=None,
        help="Age-group selector for legacy participation finding ids (for example U9)",
    )
    season_revoke_acceptance.add_argument("--actor", default=None, help="Operator identity for the revoke record")
    season_revoke_acceptance.add_argument("--note", default="", help="Why the acceptance is revoked")
    season_revoke_acceptance.add_argument("--json", action="store_true", help="Print the revoke result as JSON")


    season_retire_team = season_sub.add_parser(
        "retire-team",
        help="Retire a team: cancel its future hosting obligations and withdraw from away tournaments",
    )
    season_retire_team.add_argument("--season", required=True, help="Season id, e.g. 2026-2027")
    season_retire_team.add_argument("--root", default="season", help="Canonical season-state root (default: season)")
    season_retire_team.add_argument("--club", required=True, help="Registered club identity of the retiring team (as recorded on its roster entries)")
    season_retire_team.add_argument("--host-club", default=None, help="Literal host_club identity on the team's home tournaments, when it differs from --club (e.g. a cooperative team whose hosting responsibility is assigned to one parent club); defaults to --club")
    season_retire_team.add_argument("--team", required=True, help="Team label to retire")
    season_retire_team.add_argument("--age-group", required=True, help="Age group of the retiring team")
    season_retire_team.add_argument("--effective-from", required=True, help="Effective date YYYY-MM-DD (tournaments on/after this date)")
    season_retire_team.add_argument("--request-id", required=True, help="Stable request id for audit traceability")
    season_retire_team.add_argument("--actor", default=None, help="Operator identity")
    season_retire_team.add_argument("--note", default="", help="Retirement reason/note for decisions history")
    season_retire_team.add_argument("--accept-rebalance", action="store_true", help="Accept rebalance proposals for affected away tournaments")
    season_retire_team.add_argument("--rebalance-proposals", default=None, help="JSON file with rebalance proposals to accept (required with --accept-rebalance)")
    season_retire_team.add_argument("--dry-run", action="store_true", help="Preview without writing canonical state")
    season_retire_team.add_argument("--json", action="store_true", help="Print the retirement preview/result as JSON")
