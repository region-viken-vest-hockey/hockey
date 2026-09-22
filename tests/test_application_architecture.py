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
    for path in sorted(APPLICATION_ROOT.rglob("*.py")):
        for imported in _imported_modules(path):
            for forbidden in FORBIDDEN_IMPORTS:
                if imported == forbidden or imported.startswith(f"{forbidden}."):
                    offenders.append(f"{path}: imports {imported}")

    assert offenders == []


def test_canonical_season_facade_delegates_to_focused_modules():
    """The public facade stays small; command families live in focused modules."""

    package = APPLICATION_ROOT / "canonical_season"
    expected_modules = {
        "approvals",
        "baseline",
        "batch",
        "calendars",
        "candidates",
        "constraints",
        "guest_slots",
        "lifecycle",
        "normalization",
        "placements",
        "replacement",
        "roster",
        "shared",
    }
    assert expected_modules <= {path.stem for path in package.glob("*.py")}

    # The public boundary is a delegation facade, not a second implementation:
    # every command family is imported once and referenced only through the
    # internal module that owns it.
    facade = (APPLICATION_ROOT / "canonical_season_service.py").read_text(encoding="utf-8")
    for module in sorted(expected_modules - {"shared"}):
        assert f"{module} as _{module}" in facade
    assert "from .canonical_season.shared import" in facade


def test_canonical_commit_lifecycle_has_a_single_implementation():
    """Only the shared lifecycle module may write canonical season state."""

    package = APPLICATION_ROOT / "canonical_season"
    writers: list[str] = []
    for path in [APPLICATION_ROOT / "canonical_season_service.py", *sorted(package.glob("*.py"))]:
        if path.name == "lifecycle.py":
            continue
        if "store.write(" in path.read_text(encoding="utf-8"):
            writers.append(path.name)

    assert writers == []


def test_canonical_season_public_methods_are_thin_delegations():
    """Every public command delegates to exactly one internal module."""

    source = (APPLICATION_ROOT / "canonical_season_service.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    service = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CanonicalSeasonService"
    )

    offenders: list[str] = []
    for item in service.body:
        if not isinstance(item, ast.FunctionDef) or item.name.startswith("_") or item.name == "load":
            continue
        body = [
            statement
            for statement in item.body
            if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))
        ]
        if len(body) != 1 or not isinstance(body[0], ast.Return):
            offenders.append(item.name)
            continue
        call = body[0].value
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id.startswith("_")
        ):
            offenders.append(item.name)

    assert offenders == [], f"public methods must delegate to an internal module: {offenders}"


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
