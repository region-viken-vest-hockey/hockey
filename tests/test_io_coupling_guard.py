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
    "socket",
    "ssl",
    "http",
    "ftplib",
    "smtplib",
    "requests",
    "httpx",
    "aiohttp",
    "urllib3",
    "playwright",
    "selenium",
    "urllib.request",
    "asyncio.subprocess",
    "tournament_scheduler.cli",
    "tournament_scheduler.data_sources",
    "tournament_scheduler.infrastructure.repository_sync",
    "tournament_scheduler.infrastructure.claude_runtime",
    "tournament_scheduler.pipeline.pages_",
    "tournament_scheduler.pipeline.activity_publ",
    "tournament_scheduler.pipeline.scraper_",
    "tournament_scheduler.pipeline.browser_worker",
)

# Calls that bind core code to the process environment, a shell or the checkout.
FORBIDDEN_CALLS = frozenset(
    {"cwd", "getcwd", "chdir", "system", "popen", "spawnl", "spawnv", "execv", "execl"}
)

# Reviewed existing application -> I/O-adapter edge. May only shrink.
APPLICATION_ALLOWLIST = {
    ("tournament_scheduler/application/canonical_season/calendars/source_policy.py",
     "tournament_scheduler.pipeline.scraper_"),
}


def _imports_from_source(source: str, package: tuple[str, ...] = ()) -> set[str]:
    """Every dotted module an import statement can bring in, aliases resolved.

    ``from a import b`` records both ``a`` and ``a.b`` because ``b`` may be a
    submodule (for example ``from urllib import request``).
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: max(len(package) - node.level + 1, 0)]
                module = ".".join((*base, *(node.module.split(".") if node.module else [])))
            else:
                module = node.module or ""
            if module:
                found.add(module)
            found.update(f"{module}.{alias.name}" if module else alias.name for alias in node.names)
    return found


def _imports(path: Path) -> set[str]:
    return _imports_from_source(
        path.read_text(encoding="utf-8"), path.with_suffix("").parts[:-1]
    )


def _matches(module: str, prefix: str) -> bool:
    if prefix.endswith("_"):
        return module.startswith(prefix)
    return module == prefix or module.startswith(prefix + ".")


def _forbidden_calls_in_source(source: str) -> list[str]:
    return sorted(
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in FORBIDDEN_CALLS
    )


def _cwd_calls(path: Path) -> list[str]:
    return _forbidden_calls_in_source(path.read_text(encoding="utf-8"))


def _core_files() -> list[Path]:
    files = [ROOT / name for name in DOMAIN_FILES]
    for package in DOMAIN_PACKAGES:
        files += sorted((ROOT / package).rglob("*.py"))
    return files


def _transport_hits(files: list[Path]) -> set[tuple[str, str]]:
    hits: set[tuple[str, str]] = set()
    for path in files:
        for module in _imports(path):
            for prefix in TRANSPORT_PREFIXES:
                if _matches(module, prefix):
                    hits.add((path.as_posix(), prefix))
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


def _violations(source: str) -> tuple[set[str], list[str]]:
    modules = {m for m in _imports_from_source(source) if any(_matches(m, p) for p in TRANSPORT_PREFIXES)}
    return modules, _forbidden_calls_in_source(source)


def test_guard_detects_every_import_form_and_forbidden_call() -> None:
    """Negative fixtures: new coupling must fail the guard, whatever the spelling."""
    bad_imports = {
        "from urllib import request": "urllib.request",
        "import urllib.request as r": "urllib.request",
        "from urllib.request import urlopen": "urllib.request",
        "from subprocess import run": "subprocess",
        "import subprocess as sp": "subprocess",
        "import socket": "socket",
        "from http import client": "http.client",
        "import requests as rq": "requests",
        "from playwright.sync_api import sync_playwright": "playwright.sync_api",
        "from tournament_scheduler.infrastructure import repository_sync": (
            "tournament_scheduler.infrastructure.repository_sync"
        ),
        "from tournament_scheduler.cli import args": "tournament_scheduler.cli",
    }
    for source, expected in bad_imports.items():
        modules, _ = _violations(source)
        assert any(_matches(m, expected) or m == expected for m in modules), source
    for source in (
        "from pathlib import Path\nPath.cwd()",
        "import os\nos.getcwd()",
        "import os\nos.chdir('x')",
        "import os\nos.system('git status')",
    ):
        assert _violations(source)[1], source
    assert _violations("import json\nfrom pathlib import Path\nPath('x').exists()") == (set(), [])
    # Relative imports resolve against the importing package.
    rel = _imports_from_source("from ...cli import args", ("tournament_scheduler", "season", "x"))
    assert "tournament_scheduler.cli" in rel
