"""Locality guard for the planning domain package (issue #650)."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "tournament_scheduler"
PLANNING_DIR = PACKAGE_ROOT / "planning"

PLANNING_MODULES = {
    "contract",
    "contract_distribution",
    "date_policy",
    "derived_state",
    "half",
    "participant_roster_sizing",
    "participant_selection",
    "planner_warnings",
    "roster_size",
    "season_planner",
    "start_date",
    "verification",
}

FLAT_PLANNING_MODULES = {
    "date_policy",
    "effective_start_date",
    "final_verification",
    "participant_roster_sizing",
    "participant_selection",
    "plan_derived_state",
    "planning_contract",
    "planning_contract_distribution",
    "planning_half",
    "roster_size_planning",
    "season_planner",
    "warnings",
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_planning_domain_modules_live_in_planning_package():
    assert {path.stem for path in PLANNING_DIR.glob("*.py") if path.stem != "__init__"} == PLANNING_MODULES


def test_flat_planning_modules_do_not_reappear_at_package_root():
    flat = sorted(path.name for path in PACKAGE_ROOT.glob("*.py") if path.stem in FLAT_PLANNING_MODULES)
    assert flat == []


def test_planning_package_does_not_depend_on_cli_or_infrastructure():
    offenders = {}
    for path in PLANNING_DIR.glob("*.py"):
        forbidden = sorted(
            module
            for module in _imported_modules(path)
            if module.startswith(("tournament_scheduler.cli", "tournament_scheduler.infrastructure"))
        )
        if forbidden:
            offenders[path.name] = forbidden
    assert offenders == {}
