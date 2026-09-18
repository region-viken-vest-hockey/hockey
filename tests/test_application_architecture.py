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


def test_application_architecture_doc_describes_durable_boundary():
    text = Path("docs/application-architecture.md").read_text(encoding="utf-8")

    assert "## Dependency rules" in text
    assert "Application modules must not import" in text
    assert "## Example: adding a cross-adapter capability" in text
    assert ".agents/skills/rvv/SKILL.md" in text
    assert "DecisionContext" in text


def test_canonical_season_state_has_one_persistence_owner():
    """All promoted-season writes stay behind the store; the facade is thin."""

    store_source = Path(
        "tournament_scheduler/infrastructure/canonical_season_store.py"
    ).read_text(encoding="utf-8")
    facade_source = Path("tournament_scheduler/season_state.py").read_text(encoding="utf-8")
    service_source = Path(
        "tournament_scheduler/application/canonical_season_service.py"
    ).read_text(encoding="utf-8")

    assert "class CanonicalSeasonStore" in store_source
    assert "def _write_season_state_atomic" in store_source
    assert "os.replace" in store_source
    assert "CanonicalSeasonStore" in service_source
    assert "class CanonicalSeasonService" in service_source

    # The compatibility facade must not own file persistence mechanics.
    for forbidden in ("tempfile", "os.replace", "fsync", "NamedTemporaryFile"):
        assert forbidden not in facade_source, (
            "season_state.py must stay a thin facade over the canonical store/service; "
            f"found persistence mechanic {forbidden!r}"
        )
