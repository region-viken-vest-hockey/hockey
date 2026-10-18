"""Locality guard for the rules domain package (issue #650)."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "tournament_scheduler"
RULES_DIR = PACKAGE_ROOT / "rules"

RULES_MODULES = {
    "capacity_rules",
    "catalog",
    "catalog_doc",
    "evidence_categories",
    "model",
    "obligations",
    "operational_rules",
    "report",
    "shortfall_summary",
}

FLAT_RULES_MODULES = {"rules_model", "rules_model_obligations", "rules_report", "rules_report_capacity_rules", "rules_report_operational_rules"}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_rules_modules_live_in_rules_package():
    assert {path.stem for path in RULES_DIR.glob("*.py") if path.stem != "__init__"} == RULES_MODULES


def test_flat_rules_modules_do_not_reappear_at_package_root():
    flat = sorted(path.name for path in PACKAGE_ROOT.glob("*.py") if path.stem in FLAT_RULES_MODULES)
    assert flat == []


def test_rules_package_does_not_depend_on_cli_or_infrastructure():
    offenders = {}
    for path in RULES_DIR.glob("*.py"):
        forbidden = sorted(
            module
            for module in _imported_modules(path)
            if module.startswith(("tournament_scheduler.cli", "tournament_scheduler.infrastructure"))
        )
        if forbidden:
            offenders[path.name] = forbidden
    assert offenders == {}
