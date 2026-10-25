"""Exporters are core code: they log, and never write to stdout or own a Rich console (issue #338).

Stdout belongs to the CLI. A stray print or console write in an exporter can corrupt
``--json`` output, so these modules must stay free of both.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "tournament_scheduler"

EXPORTER_MODULES = [
    PACKAGE_ROOT / "spond" / "spond_exporter.py",
    PACKAGE_ROOT / "review" / "review_packet_exporter.py",
    PACKAGE_ROOT / "excel" / "plan_exporter.py",
]


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("module", EXPORTER_MODULES, ids=lambda p: p.stem)
def test_exporter_does_not_print_to_stdout(module: Path) -> None:
    calls = [
        node.lineno
        for node in ast.walk(_tree(module))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"
    ]

    assert calls == []


@pytest.mark.parametrize("module", EXPORTER_MODULES, ids=lambda p: p.stem)
def test_exporter_does_not_own_a_rich_console(module: Path) -> None:
    rich_imports = [
        node.lineno
        for node in ast.walk(_tree(module))
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("rich")
    ]

    assert rich_imports == []
