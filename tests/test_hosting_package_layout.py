"""Locality guard for the hosting domain package (issue #650)."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "tournament_scheduler"
HOSTING_DIR = PACKAGE_ROOT / "hosting"

HOSTING_MODULES = {
    "coverage",
    "responsibility",
    "balance_repair",
    "cross_age_repair",
    "cross_age_repair_apply",
    "cross_age_repair_ops",
    "same_age_repair",
    "same_age_repair_apply",
    "representation",
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


def test_hosting_domain_modules_live_in_hosting_package():
    assert {path.stem for path in HOSTING_DIR.glob("*.py") if path.stem != "__init__"} == HOSTING_MODULES


def test_flat_hosting_modules_do_not_reappear_at_package_root():
    flat = sorted(
        path.name
        for path in PACKAGE_ROOT.glob("*.py")
        if path.stem.startswith("hosting_") or path.stem == "host_representation"
    )
    assert flat == []


def test_hosting_package_does_not_depend_on_cli_or_infrastructure():
    offenders = {}
    for path in HOSTING_DIR.glob("*.py"):
        forbidden = sorted(
            module
            for module in _imported_modules(path)
            if module.startswith(("tournament_scheduler.cli", "tournament_scheduler.infrastructure"))
        )
        if forbidden:
            offenders[path.name] = forbidden
    assert offenders == {}
