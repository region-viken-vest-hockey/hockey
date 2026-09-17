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
    SOURCE_ROOT / "application" / "stage3_progress.py",
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

# Only the canonical store may own those files (migration + compatibility
# projection), plus the thin CLI facade that used to own them and now exposes
# legacy path accessors. Any other production module referencing them is
# reintroducing side-state ownership outside the session.
STAGE3_STATE_FILE_OWNERS = {
    SOURCE_ROOT / "application" / "stage3_session_store.py",
    SOURCE_ROOT / "cli" / "pipeline_orchestrator" / "interactive_state_io.py",
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


def test_legacy_side_state_files_are_owned_only_by_the_session_facade() -> None:
    """Only the store (and the CLI facade that delegates to it) may name them.

    This is the architecture guard that keeps a new Stage 3 capability from
    reintroducing ``<feature>_state.json`` ownership outside the one
    session/facade boundary.
    """
    offenders: set[str] = set()
    for path in SOURCE_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts or path in STAGE3_STATE_FILE_OWNERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in KNOWN_STAGE3_STATE_FILES:
                offenders.add(str(path.relative_to(ROOT)))
    assert offenders == set(), (
        "these modules reference legacy Stage 3 side-state files directly instead "
        f"of going through Stage3SessionStore: {sorted(offenders)}"
    )


def test_cli_stage3_state_helpers_delegate_to_the_session_store() -> None:
    facade = SOURCE_ROOT / "cli" / "pipeline_orchestrator" / "interactive_state_io.py"
    imported = _imports(facade)
    assert any(name.endswith("application.stage3_session_store") for name in imported), (
        "interactive_state_io must be a facade over the canonical Stage3SessionStore, "
        "not a second state authority"
    )


# The domain operations a Stage 3 transition invokes live in one CLI-side
# adapter; the CLI transport/orchestration layer must not grow its own copies
# of those bodies (that is exactly the fall-through lifecycle logic this
# architecture removes).
CLI_TRANSPORT_MODULE = SOURCE_ROOT / "cli" / "pipeline_orchestrator" / "run_command_interactive.py"
STAGE3_CAPABILITIES_MODULE = SOURCE_ROOT / "cli" / "pipeline_orchestrator" / "stage3_capabilities.py"

CLI_FORBIDDEN_DOMAIN_IMPORTS = (
    "tournament_scheduler.local_repair_options",
    "tournament_scheduler.arena_conflict_decision",
    "tournament_scheduler.shared_host_decision",
    "tournament_scheduler.stage3_decision",
    "tournament_scheduler.planner",
)

LIFECYCLE_MUTATIONS = ("advance_candidate", "record_history", "finalize")


def _called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def test_cli_transport_does_not_own_domain_transition_bodies() -> None:
    imported = _imports(CLI_TRANSPORT_MODULE)
    offenders = [
        name
        for name in imported
        for forbidden in CLI_FORBIDDEN_DOMAIN_IMPORTS
        if name == forbidden or name.startswith(f"{forbidden}.")
    ]
    assert offenders == [], (
        "Stage 3 repair/arena/shared-host transition bodies belong in the "
        f"capabilities adapter, not the CLI transport layer: {offenders}"
    )


def test_cli_transport_does_not_mutate_session_lifecycle() -> None:
    called = _called_names(CLI_TRANSPORT_MODULE)
    offenders = sorted(name for name in LIFECYCLE_MUTATIONS if name in called)
    assert offenders == [], (
        f"candidate-revision lifecycle must be owned by Stage3Controller, not the CLI transport layer: {offenders}"
    )


def test_cli_pending_resume_is_session_generic() -> None:
    """No-action resume must read the one pending decision, not enumerate
    capability-specific readers (which is what let it fall through and rerun
    Stage 3 for an ordinary attempt-comparison decision)."""
    tree = ast.parse(CLI_TRANSPORT_MODULE.read_text(encoding="utf-8"), filename=str(CLI_TRANSPORT_MODULE))
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_emit_pending_stage3_subdecision_context"
        ),
        None,
    )
    assert function is not None, "the no-action resume helper must stay in the CLI transport layer"
    called = {
        node.func.id
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    for capability_reader in ("_read_shared_host_state", "_read_arena_conflict_state"):
        assert capability_reader not in called, (
            "no-action resume must render Stage3Session.pending_decision instead of "
            f"checking one capability at a time ({capability_reader})"
        )
    assert "pending_decision" in ast.dump(function), (
        "no-action resume must read the generic Stage3Session.pending_decision"
    )


def test_capabilities_adapter_is_domain_only_not_a_second_controller() -> None:
    text = STAGE3_CAPABILITIES_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(STAGE3_CAPABILITIES_MODULE))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("stage3_controller"):
            names = {alias.name for alias in node.names}
            assert "Stage3Controller" not in names, (
                "the capabilities adapter must not construct the lifecycle controller"
            )
    assert "Stage3CapabilityResult" in text, (
        "the capabilities adapter must report typed results through Stage3CapabilityResult"
    )
    for state_file in KNOWN_STAGE3_STATE_FILES:
        assert state_file not in text, "the capabilities adapter must use the session/facade, not legacy side files"
