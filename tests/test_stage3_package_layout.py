"""Locality guard for the Stage 3 domain package (issue #650)."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "tournament_scheduler"
STAGE3_DIR = PACKAGE_ROOT / "stage3"

STAGE3_MODULES = {
    "ab",
    "cpsat",
    "cpsat_club_cap",
    "cpsat_diagnostics",
    "cpsat_slots",
    "decision",
    "engine",
    "optimizer",
    "shadow",
}

FLAT_STAGE3_MODULES = {f"stage3_{name}" for name in STAGE3_MODULES}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_stage3_domain_modules_live_in_stage3_package():
    assert {path.stem for path in STAGE3_DIR.glob("*.py") if path.stem != "__init__"} == STAGE3_MODULES


def test_flat_stage3_modules_do_not_reappear_at_package_root():
    flat = sorted(path.name for path in PACKAGE_ROOT.glob("*.py") if path.stem in FLAT_STAGE3_MODULES)
    assert flat == []


def test_stage3_package_does_not_depend_on_cli_or_infrastructure():
    offenders = {}
    for path in STAGE3_DIR.glob("*.py"):
        forbidden = sorted(
            module
            for module in _imported_modules(path)
            if module.startswith(("tournament_scheduler.cli", "tournament_scheduler.infrastructure"))
        )
        if forbidden:
            offenders[path.name] = forbidden
    assert offenders == {}
