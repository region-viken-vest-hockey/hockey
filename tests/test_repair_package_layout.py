"""Locality guard for the repair providers package (issue #650)."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "tournament_scheduler"
REPAIR_DIR = PACKAGE_ROOT / "repair"

REPAIR_MODULES = {
    "coupled_placement",
    "date_policy_relocation",
    "home_representation",
    "host_placement",
    "host_representation",
    "host_team_missing",
    "intra_club_distribution",
    "movable_capacity",
    "participant_roster",
    "placement_preserving_roster",
    "responsibility_preserving",
    "search_neighborhood",
    "underfilled_roster",
    "unplaced_placement",
}

FLAT_REPAIR_MODULES = {"coupled_placement_repair", "date_policy_relocation", "home_representation_repair", "host_placement_repair", "host_representation_repair", "host_team_missing_repair", "intra_club_distribution_repair", "movable_capacity_repair", "participant_roster_repair", "placement_preserving_roster_repair", "responsibility_preserving_repair", "search_neighborhood_repair", "underfilled_roster_repair", "unplaced_placement_repair"}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_repair_modules_live_in_repair_package():
    assert {path.stem for path in REPAIR_DIR.glob("*.py") if path.stem != "__init__"} == REPAIR_MODULES


def test_flat_repair_modules_do_not_reappear_at_package_root():
    flat = sorted(path.name for path in PACKAGE_ROOT.glob("*.py") if path.stem in FLAT_REPAIR_MODULES)
    assert flat == []


def test_repair_package_does_not_depend_on_cli_or_infrastructure():
    offenders = {}
    for path in REPAIR_DIR.glob("*.py"):
        forbidden = sorted(
            module
            for module in _imported_modules(path)
            if module.startswith(("tournament_scheduler.cli", "tournament_scheduler.infrastructure"))
        )
        if forbidden:
            offenders[path.name] = forbidden
    assert offenders == {}
