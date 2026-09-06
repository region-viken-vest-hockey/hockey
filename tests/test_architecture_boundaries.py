"""Static import-boundary checks for issue #262 P1.

The canonical LLM-directed decision path (`stage3_optimizer.py`) must not
depend on the legacy `SeasonPlanner` baseline generator's heuristic policy
modules (`participant_selection.py`, `host_assignment.py`) -- those modules
keep serving `SeasonPlanner` as a baseline/fallback, but their heuristic
weights/rankings must never silently control the canonical search. This is
checked via the module's actual `ast` import graph, not a text grep, so a
future refactor that hides the dependency behind a local/deferred import
still fails the check.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

FORBIDDEN_MODULES = {"participant_selection", "host_assignment"}


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[-1])
    return names


def _stage3_optimizer_path() -> Path:
    spec = importlib.util.find_spec("tournament_scheduler.stage3_optimizer")
    assert spec and spec.origin
    return Path(spec.origin)


def test_stage3_optimizer_does_not_import_legacy_policy_modules() -> None:
    imported = _imported_module_names(_stage3_optimizer_path())
    forbidden_hits = imported & FORBIDDEN_MODULES
    assert not forbidden_hits, (
        f"stage3_optimizer.py must not import legacy SeasonPlanner policy "
        f"modules {sorted(forbidden_hits)} (issue #262 P1) -- the canonical "
        "LLM-directed path must use deterministic facts, not legacy "
        "heuristic weights/rankings."
    )
