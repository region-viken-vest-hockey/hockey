"""Characterization of the CLI parser composition and its command-family owners.

``cli/args.py`` only composes the top-level command groups; each family owns
its argparse definitions under ``cli/parsers``. These tests pin the public
surface (command order, defaults, required arguments, destinations) across
every extracted family, and keep parser modules transport-only.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from tournament_scheduler.cli.args import build_parser

PARSERS_DIR = Path(__file__).resolve().parents[1] / "tournament_scheduler" / "cli" / "parsers"

TOP_LEVEL_COMMANDS = (
    "status",
    "sources",
    "calendars",
    "registered-teams",
    "activities",
    "run",
    "operator",
    "logs",
    "registrations",
    "scrape",
    "scrape-llm",
    "recovery-targets",
    "recovery-inject",
    "scrape-merge",
    "season",
    "stage3",
    "cancel",
    "replan",
    "adjust",
    "review",
    "tournament",
    "critic",
    "auto-adjust",
    "verdict",
    "candidates",
    "plan",
    "waiver",
    "export-parity",
)


@pytest.fixture(scope="module")
def parser():
    return build_parser()


def _subcommands(parser, *path: str) -> tuple[str, ...]:
    current = parser
    for name in path:
        current = _choices(current)[name]
    return tuple(_choices(current))


def _choices(parser) -> dict:
    import argparse

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    raise AssertionError(f"parser has no subcommands: {parser.prog}")


def test_top_level_command_order_is_stable(parser):
    assert _subcommands(parser) == TOP_LEVEL_COMMANDS


def test_source_health_family_defaults(parser):
    args = parser.parse_args(["status"])
    assert (args.command, args.work_dir, args.json) == ("status", ".pipeline", False)

    args = parser.parse_args(["sources", "status", "--json"])
    assert (args.sources_command, args.json) == ("status", True)

    args = parser.parse_args(["calendars", "--refresh"])
    assert args.refresh is True


def test_publication_family_requires_csv_for_registered_teams(parser):
    args = parser.parse_args(["registered-teams", "--csv", "lag.csv"])
    assert args.csv == "lag.csv"
    assert args.export_dir == ".pipeline/registered_teams_publish_export"
    assert args.push is True and args.verify is True

    args = parser.parse_args(["activities", "--no-push", "--dry-run"])
    assert (args.push, args.dry_run, args.branch) == (False, True, "gh-pages")

    with pytest.raises(SystemExit):
        parser.parse_args(["registered-teams"])


def test_pipeline_run_and_logs_family(parser):
    args = parser.parse_args(["run"])
    assert (args.input, args.export_dir, args.log_level) == ("input.xlsx", "export", "info")
    assert args.timestamped_export is True

    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--log-level", "loud"])

    args = parser.parse_args(["logs", "show"])
    assert args.run_id == "latest"


def test_operator_run_and_gate_families(parser):
    args = parser.parse_args(["operator", "run", "--publish", "--allow-finding", "x"])
    assert args.publish is True
    assert args.allow_findings == ["x"]
    assert args.push is True

    args = parser.parse_args(["operator", "questions", "--all"])
    assert args.all is True

    assert "audit-context" in _subcommands(parser, "operator")


def test_intake_family_requires_registration_inputs(parser):
    args = parser.parse_args(["scrape", "--club", "Sandefjord"])
    assert (args.club, args.work_dir) == ("Sandefjord", ".pipeline")

    with pytest.raises(SystemExit):
        parser.parse_args(["registrations", "export"])


def test_season_families_keep_required_identifiers_and_defaults(parser):
    args = parser.parse_args(["season", "move", "--season", "2026-2027", "--tournament-id", "t1", "--dry-run"])
    assert (args.season, args.tournament_id, args.root, args.dry_run) == ("2026-2027", "t1", "season", True)
    assert args.allow_manual_placement is False

    args = parser.parse_args(["season", "approve", "--season", "s", "--tournament-id", "t1"])
    assert args.placement_locked is True
    assert args.participants_lock is False

    args = parser.parse_args(["season", "guest-reserve", "--season", "s", "--tournament-id", "t1", "--displaced-team", "A"])
    assert (args.count, args.displaced_teams) == (1, ["A"])

    args = parser.parse_args(["season", "replan", "--season", "s", "--apply"])
    assert (args.engine, args.iterations, args.seed, args.apply) == ("local_search", 4000, 0, True)

    args = parser.parse_args(
        ["season", "retire-team", "--season", "s", "--club", "c", "--team", "t", "--age-group", "U10",
         "--effective-from", "2026-11-01", "--request-id", "r1"]
    )
    assert args.accept_rebalance is False and args.dry_run is False

    with pytest.raises(SystemExit):
        parser.parse_args(["season", "move", "--season", "s"])


def test_stage3_family(parser):
    args = parser.parse_args(["stage3", "status", "--json"])
    assert (args.work_dir, args.json) == (".pipeline", True)


def test_schedule_change_family(parser):
    args = parser.parse_args(["cancel", "--tournament-id", "t1", "--no-export"])
    assert (args.no_export, args.force, args.export_dir) == (True, False, "export")

    args = parser.parse_args(["tournament", "add", "--age-group", "U10", "--teams", "A,B", "--date", "2026-11-01", "--arena", "Hall"])
    assert (args.age_group, args.host_club, args.force) == ("U10", None, False)


def test_critique_family(parser):
    args = parser.parse_args(["critic"])
    assert args.work_dir == ".pipeline"


def test_plan_families(parser):
    args = parser.parse_args(["plan", "verify", "candidate.json", "--json"])
    assert (args.candidate, args.json) == ("candidate.json", True)

    args = parser.parse_args(["plan", "ab"])
    assert (args.iterations, args.seed, args.engine, args.solve_budget_seconds) == (4000, 0, "local-search", 30.0)

    with pytest.raises(SystemExit):
        parser.parse_args(["plan", "ab", "--engine", "magic"])

    args = parser.parse_args(["plan", "decide", "ab.json", "--action", "keep_baseline"])
    assert (args.ab_report, args.action) == ("ab.json", "keep_baseline")


def test_waiver_and_export_parity_families(parser):
    args = parser.parse_args(["waiver", "revoke", "w1", "--reason", "done"])
    assert (args.waiver_id, args.reason, args.work_dir) == ("w1", "done", ".pipeline")

    args = parser.parse_args(["export-parity", "--published", "--season", "2026-2027"])
    assert (args.published, args.basename, args.branch) == (True, "season_plan", "gh-pages")


def test_parser_modules_stay_transport_only():
    """Parser families may import argparse and their own siblings only.

    Importing application or domain modules from a parser module would move
    behavior into the CLI transport layer and risks circular imports with the
    command handlers that consume the parsed arguments.
    """
    stdlib = set(sys.stdlib_module_names)
    violations = []
    for module in sorted(PARSERS_DIR.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
                violations += [f"{module.name}: import {name}" for name in names if name not in stdlib]
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    if (node.module or "").split(".")[0] not in stdlib:
                        violations.append(f"{module.name}: from {node.module} import ...")
                elif node.level == 1 and node.module in (None, "common"):
                    continue
                elif node.level == 3 and node.module == "operator_waivers":
                    continue
                else:
                    violations.append(f"{module.name}: relative import level={node.level} module={node.module}")
    assert violations == []
