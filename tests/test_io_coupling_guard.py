"""Import/path guards that keep Git, network, cwd and RVV layout out of core code.

Core (domain policy and the application layer) must receive I/O through the
versioned ports in ``application/ports``. The domain packages are currently
clean, so they have no allowlist; the application layer has one reviewed,
frozen exception that may only shrink. Do not add entries: introduce a port.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path("tournament_scheduler")
PORTS = ROOT / "application" / "ports"

DOMAIN_PACKAGES = (
    "rules", "planning", "participation", "placement", "repair", "season",
    "booking", "hosting", "scoring", "stage3", "shape",
)
DOMAIN_FILES = ("models.py", "models_calendar.py")

TRANSPORT_PREFIXES = (
    "subprocess",
    "requests",
    "httpx",
    "playwright",
    "urllib.request",
    "tournament_scheduler.cli",
    "tournament_scheduler.data_sources",
    "tournament_scheduler.infrastructure.repository_sync",
    "tournament_scheduler.infrastructure.claude_runtime",
    "tournament_scheduler.pipeline.pages_",
    "tournament_scheduler.pipeline.activity_publ",
    "tournament_scheduler.pipeline.scraper_",
    "tournament_scheduler.pipeline.browser_worker",
)

# Reviewed existing application -> I/O-adapter edge. May only shrink.
APPLICATION_ALLOWLIST = {
    ("tournament_scheduler/application/canonical_season/calendars/source_policy.py",
     "tournament_scheduler.pipeline.scraper_strategies"),
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = path.with_suffix("").parts[:-1]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: max(len(package) - node.level + 1, 0)]
                found.add(".".join((*base, *(node.module.split(".") if node.module else []))))
            elif node.module:
                found.add(node.module)
    return found


def _matches(module: str, prefix: str) -> bool:
    if prefix.endswith("_"):
        return module.startswith(prefix)
    return module == prefix or module.startswith(prefix + ".")


def _cwd_calls(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"cwd", "getcwd"}
    ]


def _core_files() -> list[Path]:
    files = [ROOT / name for name in DOMAIN_FILES]
    for package in DOMAIN_PACKAGES:
        files += sorted((ROOT / package).rglob("*.py"))
    return files


def _transport_hits(files: list[Path]) -> set[tuple[str, str]]:
    hits: set[tuple[str, str]] = set()
    for path in files:
        for module in _imports(path):
            if any(_matches(module, prefix) for prefix in TRANSPORT_PREFIXES):
                hits.add((path.as_posix(), module))
    return hits


def test_domain_packages_do_not_import_transport_or_use_cwd() -> None:
    files = _core_files()
    assert files, "domain package discovery found nothing"
    assert _transport_hits(files) == set()
    assert [(f.as_posix(), _cwd_calls(f)) for f in files if _cwd_calls(f)] == []


def test_application_layer_reaches_io_only_through_reviewed_edges() -> None:
    files = sorted((ROOT / "application").rglob("*.py"))
    hits = _transport_hits(files)
    assert hits <= APPLICATION_ALLOWLIST, f"new application I/O coupling; add a port: {hits - APPLICATION_ALLOWLIST}"
    assert APPLICATION_ALLOWLIST <= hits, "allowlist entry no longer needed; remove it"
    assert [(f.as_posix(), _cwd_calls(f)) for f in files if _cwd_calls(f)] == []


def test_ports_are_stdlib_only_and_layout_free() -> None:
    allowed_prefixes = (
        "__future__", "dataclasses", "datetime", "enum", "hashlib", "typing", "zoneinfo",
        "tournament_scheduler.application.ports",
    )
    layout_literals = ("season/", ".pipeline", "input.xlsx", "gh-pages", "origin/main", "export/")
    for path in sorted(PORTS.glob("*.py")):
        for module in _imports(path):
            assert module.startswith(allowed_prefixes), f"{path}: unexpected import {module}"
        source = path.read_text(encoding="utf-8")
        for literal in layout_literals:
            assert literal not in source, f"{path}: RVV layout literal {literal!r}"
        assert len(source.splitlines()) <= 300, path
