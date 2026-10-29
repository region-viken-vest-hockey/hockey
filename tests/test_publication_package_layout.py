"""Locality guard for the publication domain package (issue #650)."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "tournament_scheduler"
PUBLICATION_DIR = PACKAGE_ROOT / "publication"

PUBLICATION_MODULES = {
    "baseline",
    "evidence",
    "export_lifecycle",
    "mutation_history",
    "projection_guard",
    "public_history",
    "scope",
}

FLAT_PUBLICATION_MODULES = {"published_baseline", "published_mutation_history", "export_projection_guard", "publication_scope", "publication_evidence", "export_lifecycle"}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_publication_modules_live_in_publication_package():
    assert {path.stem for path in PUBLICATION_DIR.glob("*.py") if path.stem != "__init__"} == PUBLICATION_MODULES


def test_flat_publication_modules_do_not_reappear_at_package_root():
    flat = sorted(path.name for path in PACKAGE_ROOT.glob("*.py") if path.stem in FLAT_PUBLICATION_MODULES)
    assert flat == []


def test_publication_package_does_not_depend_on_cli_or_infrastructure():
    offenders = {}
    for path in PUBLICATION_DIR.glob("*.py"):
        forbidden = sorted(
            module
            for module in _imported_modules(path)
            if module.startswith(("tournament_scheduler.cli", "tournament_scheduler.infrastructure"))
        )
        if forbidden:
            offenders[path.name] = forbidden
    assert offenders == {}
