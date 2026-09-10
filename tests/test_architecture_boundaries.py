"""Static import-boundary checks for issue #262 P1.

The canonical LLM-directed decision path must not depend on the legacy
`SeasonPlanner` baseline generator's heuristic policy modules
(`participant_selection.py`, `host_assignment.py`) -- those modules keep
serving `SeasonPlanner` as a baseline/fallback (`season_planner.py` is the
one module allowed to import them), but their heuristic weights/rankings
must never silently control the canonical search, decision, or interactive
orchestration path. This is checked via each module's actual `ast` import
graph, not a text grep, so a future refactor that hides the dependency
behind a local/deferred import still fails the check.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

FORBIDDEN_MODULES = {"participant_selection", "host_assignment"}

CANONICAL_PATH_MODULES = [
    "tournament_scheduler.stage3_optimizer",
    "tournament_scheduler.stage3_decision",
    "tournament_scheduler.pipeline.stage3_planning",
    # tournament_scheduler.cli.pipeline_orchestrator is a package (split for
    # the 300-line-per-file guideline) -- list every submodule individually
    # so this check still inspects the real import graph, not just the
    # __init__.py re-export facade.
    "tournament_scheduler.cli.pipeline_orchestrator.calendars_scrape",
    "tournament_scheduler.cli.pipeline_orchestrator.export_command",
    "tournament_scheduler.cli.pipeline_orchestrator.interactive_decision_emit",
    "tournament_scheduler.cli.pipeline_orchestrator.interactive_state_io",
    "tournament_scheduler.cli.pipeline_orchestrator.judgment",
    "tournament_scheduler.cli.pipeline_orchestrator.manifest",
    "tournament_scheduler.cli.pipeline_orchestrator.operator_publish",
    "tournament_scheduler.cli.pipeline_orchestrator.operator_run",
    "tournament_scheduler.cli.pipeline_orchestrator.plan_adoption",
    "tournament_scheduler.cli.pipeline_orchestrator.refinement_decisions",
    "tournament_scheduler.cli.pipeline_orchestrator.refinement_loop",
    "tournament_scheduler.cli.pipeline_orchestrator.refinement_reexport",
    "tournament_scheduler.cli.pipeline_orchestrator.run_command",
    "tournament_scheduler.cli.pipeline_orchestrator.run_command_interactive",
    "tournament_scheduler.cli.pipeline_orchestrator.run_log",
    "tournament_scheduler.cli.pipeline_orchestrator.shared_host_decisions",
    "tournament_scheduler.cli.pipeline_orchestrator.stage1",
    "tournament_scheduler.cli.pipeline_orchestrator.stage2",
    "tournament_scheduler.cli.pipeline_orchestrator.stage3_optimize_core",
    "tournament_scheduler.cli.pipeline_orchestrator.stage3_optimize_variants",
    "tournament_scheduler.cli.pipeline_orchestrator.stage3_pareto_decision",
    "tournament_scheduler.cli.pipeline_orchestrator.stage3_run",
    "tournament_scheduler.cli.pipeline_orchestrator.verification",
]


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


def _module_path(dotted_name: str) -> Path:
    spec = importlib.util.find_spec(dotted_name)
    assert spec and spec.origin, f"could not resolve module {dotted_name!r}"
    return Path(spec.origin)


def test_stage3_optimizer_does_not_import_legacy_policy_modules() -> None:
    imported = _imported_module_names(_module_path("tournament_scheduler.stage3_optimizer"))
    forbidden_hits = imported & FORBIDDEN_MODULES
    assert not forbidden_hits, (
        f"stage3_optimizer.py must not import legacy SeasonPlanner policy "
        f"modules {sorted(forbidden_hits)} (issue #262 P1) -- the canonical "
        "LLM-directed path must use deterministic facts, not legacy "
        "heuristic weights/rankings."
    )


def test_canonical_path_modules_do_not_import_legacy_policy_modules() -> None:
    failures: dict[str, set[str]] = {}
    for dotted_name in CANONICAL_PATH_MODULES:
        imported = _imported_module_names(_module_path(dotted_name))
        forbidden_hits = imported & FORBIDDEN_MODULES
        if forbidden_hits:
            failures[dotted_name] = forbidden_hits
    assert not failures, (
        "canonical LLM-directed path modules must not import legacy "
        f"SeasonPlanner policy modules (issue #262 P1): {failures} -- those "
        "modules may only be reached indirectly through season_planner.py's "
        "baseline/fallback generation, never from the canonical decision, "
        "optimizer, planning, or interactive-orchestration path."
    )
