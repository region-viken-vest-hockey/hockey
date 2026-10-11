"""Locality guard for the season domain package (issue #650)."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "tournament_scheduler"
SEASON_DIR = PACKAGE_ROOT / "season"

SEASON_MODULES = {
    "accepted_baseline",
    "banned_dates",
    "baseline_overlay",
    "change_protections",
    "config",
    "exception_policy",
    "history_summary",
    "holiday_exceptions",
    "ice_time_overrides",
    "impact_analysis",
    "maintenance",
    "replan",
    "request_constraints",
    "state",
    "waivers",
}

FLAT_SEASON_MODULES = {
    "canonical_banned_dates",
    "canonical_baseline",
    "canonical_exception_policy",
    "canonical_history_summary",
    "canonical_holiday_exceptions",
    "canonical_ice_time_overrides",
    "canonical_inventory",
    "canonical_replan",
    "canonical_state",
    "change_protections",
    "impact_analysis",
    "operator_waivers",
    "request_constraints",
    "season_baseline",
    "season_config",
    "season_maintenance",
    "season_state",
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


def test_season_domain_modules_live_in_season_package():
    assert {path.stem for path in SEASON_DIR.glob("*.py") if path.stem != "__init__"} == SEASON_MODULES


def test_flat_season_modules_do_not_reappear_at_package_root():
    flat = sorted(path.name for path in PACKAGE_ROOT.glob("*.py") if path.stem in FLAT_SEASON_MODULES)
    assert flat == []


def test_season_package_does_not_depend_on_cli_or_infrastructure():
    offenders = {}
    for path in SEASON_DIR.glob("*.py"):
        forbidden = sorted(
            module
            for module in _imported_modules(path)
            if module.startswith(("tournament_scheduler.cli", "tournament_scheduler.infrastructure"))
        )
        if forbidden:
            offenders[path.name] = forbidden
    assert offenders == {}
