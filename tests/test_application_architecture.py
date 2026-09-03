"""Lightweight architecture checks for the typed application layer."""

from __future__ import annotations

import ast
from pathlib import Path


APPLICATION_ROOT = Path("tournament_scheduler/application")
FORBIDDEN_IMPORTS = {
    "rich",
    "subprocess",
    "tournament_scheduler.cli",
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    package_parts = tuple(path.with_suffix("").parts)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                imported.add(_resolve_relative_import(package_parts, node.level, node.module or ""))
            elif node.module:
                imported.add(node.module)
    return imported


def _resolve_relative_import(package_parts: tuple[str, ...], level: int, module: str) -> str:
    package = package_parts[:-1]
    keep = max(len(package) - level + 1, 0)
    base = package[:keep]
    if module:
        return ".".join((*base, *module.split(".")))
    return ".".join(base)


def test_application_modules_do_not_import_transport_layers():
    offenders: list[str] = []
    for path in sorted(APPLICATION_ROOT.glob("*.py")):
        for imported in _imported_modules(path):
            for forbidden in FORBIDDEN_IMPORTS:
                if imported == forbidden or imported.startswith(f"{forbidden}."):
                    offenders.append(f"{path}: imports {imported}")

    assert offenders == []


def test_application_architecture_doc_describes_rules_and_example():
    text = Path("docs/application-architecture.md").read_text(encoding="utf-8")

    assert "## Dependency rules" in text
    assert "Application modules must not import" in text
    assert "## Example: adding a new command/use case" in text
    assert "rvv-miniputt operator questions|answer|promote|health" in text
