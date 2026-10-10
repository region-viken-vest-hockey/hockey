"""Operator waiver commands: list, create and revoke."""

from __future__ import annotations

import argparse
from ...operator_waivers import WAIVABLE_RULE_IDS


def add_waiver_parsers(sub: argparse._SubParsersAction) -> None:
    # waiver — explicit operator authorization of hard-rule exceptions
    waiver = sub.add_parser(
        "waiver",
        help="Create/list/revoke narrow, audited operator waivers for hard planning rules",
    )
    waiver_sub = waiver.add_subparsers(dest="waiver_command")

    waiver_list = waiver_sub.add_parser("list", help="List operator waivers for a run")
    waiver_list.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory (default: .pipeline)")
    waiver_list.add_argument("--all", action="store_true", help="Include revoked waivers")
    waiver_list.add_argument("--json", action="store_true", help="Print records as JSON")

    waiver_create = waiver_sub.add_parser(
        "create",
        help="Authorize one narrow operator exception (operator-only; agents may never create one)",
    )
    waiver_create.add_argument(
        "--rule",
        required=True,
        choices=sorted(WAIVABLE_RULE_IDS),
        help="Hard planning rule to waive",
    )
    waiver_create.add_argument("--club", default=None, help="Team's club (disambiguates the team identity)")
    waiver_create.add_argument("--team", required=True, help="Team label, e.g. 'Frisk Asker 4'")
    waiver_create.add_argument("--age-group", default=None, help="Team age group, e.g. U11")
    waiver_create.add_argument(
        "--tournament",
        default=None,
        help="Tournament id the authorized participation lands in (required for participation waivers)",
    )
    waiver_create.add_argument(
        "--half",
        default=None,
        choices=["before_christmas", "after_christmas"],
        help="Season half the exception applies to",
    )
    waiver_create.add_argument(
        "--configured-value",
        type=int,
        default=None,
        help="Configured target being exceeded; validated against the run's configured value",
    )
    waiver_create.add_argument(
        "--allowed-value",
        type=int,
        required=True,
        help="Exact actual count the operator authorizes (e.g. 6 for a 6/5 exception)",
    )
    waiver_create.add_argument("--reason", required=True, help="Operator reason (recorded verbatim in the audit)")
    waiver_create.add_argument("--actor", default=None, help="Operator identity (defaults from RVV_OPERATOR/USER)")
    waiver_create.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory (default: .pipeline)")
    waiver_create.add_argument("--json", action="store_true", help="Print the created record as JSON")

    waiver_revoke = waiver_sub.add_parser("revoke", help="Revoke an active waiver and restore hard verification")
    waiver_revoke.add_argument("waiver_id", help="Waiver id to revoke")
    waiver_revoke.add_argument("--reason", default=None, help="Why the exception is withdrawn")
    waiver_revoke.add_argument("--actor", default=None, help="Operator identity (defaults from RVV_OPERATOR/USER)")
    waiver_revoke.add_argument("--work-dir", default=".pipeline", help="Pipeline work directory (default: .pipeline)")
    waiver_revoke.add_argument("--json", action="store_true", help="Print the revoked record as JSON")
