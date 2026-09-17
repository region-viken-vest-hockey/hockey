"""Architecture guards for the explicit interactive Stage 3 session.

These enforce the ownership boundary the Stage 3 lifecycle rework depends on:
the session/controller own persistence and lifecycle semantics only, domain
capabilities own hockey legality/repair selection, and no new authoritative
feature-specific side-state file may appear outside the session facade.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "tournament_scheduler"

LIFECYCLE_MODULES = (
    SOURCE_ROOT / "application" / "stage3_session.py",
    SOURCE_ROOT / "application" / "stage3_session_store.py",
    SOURCE_ROOT / "application" / "stage3_controller.py",
)

# Domain/planner modules the lifecycle layer must not depend on: it decides
# how an operation is applied/persisted, never what is legal or which repair
# to select.
FORBIDDEN_LIFECYCLE_IMPORTS = (
    "tournament_scheduler.cli",
    "rich",
    "tournament_scheduler.season_planner",
    "tournament_scheduler.stage3_optimizer",
    "tournament_scheduler.local_repair_options",
    "tournament_scheduler.host_team_missing_repair",
    "tournament_scheduler.search_neighborhood_repair",
    "tournament_scheduler.underfilled_roster_repair",
    "tournament_scheduler.host_placement_repair",
)

# The only authoritative interactive Stage 3 side-state filenames allowed in
# production source. A new #348 repair provider must not need its own file.
KNOWN_STAGE3_STATE_FILES = {
    "stage3_interactive_state.json",
    "shared_host_decision_state.json",
    "arena_conflict_decision_state.json",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_lifecycle_modules_do_not_own_domain_or_transport_policy() -> None:
    failures: list[str] = []
    for path in LIFECYCLE_MODULES:
        for imported in _imports(path):
            for forbidden in FORBIDDEN_LIFECYCLE_IMPORTS:
                if imported == forbidden or imported.startswith(f"{forbidden}."):
                    failures.append(f"{path.name}: imports {imported}")
    assert failures == [], (
        "Stage 3 session/controller must stay a lifecycle/persistence boundary; "
        f"domain legality and transport belong elsewhere: {failures}"
    )


def test_no_new_authoritative_stage3_side_state_file() -> None:
    unexpected: set[str] = set()
    for path in SOURCE_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                if value.endswith("_state.json") and value not in KNOWN_STAGE3_STATE_FILES:
                    unexpected.add(value)
    assert unexpected == set(), (
        "new authoritative Stage 3 side-state files must go through the "
        f"Stage3Session repository/facade, not a new file: {sorted(unexpected)}"
    )
