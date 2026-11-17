"""Locality guard for the booking domain package (issue #650)."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "tournament_scheduler"
BOOKING_DIR = PACKAGE_ROOT / "booking"

BOOKING_MODULES = {
    "arena_conflict_decision",
    "accepted_provenance",
    "arena_conflicts",
    "assertion_bridge",
    "assessment_candidates",
    "assessment_export",
    "calendar_audit",
    "calendar_availability",
    "calendar_bookings",
    "accepted_interval_evidence",
    "assessment_events",
    "assessment_sources",
    "assessment_tournament",
    "booking_assessment",
    "booking_authority_projection",
    "booking_cancellation",
    "calendar_observations",
    "rejected_booking_evidence",
    "booking_facts",
    "booking_status_report",
    "booking_status_row",
    "booking_vocabulary",
    "calendar_associations",
    "calendar_evidence",
    "club_booking_sources",
    "club_source_projection",
    "manual_assertions",
    "manual_booking_queue",
    "stated_intervals",
    "calendar_presence",
    "candidate_weekend_availability",
    "candidate_weekends",
    "club_profile_availability",
    "ice_resources",
    "interval_facts",
    "primary_observation",
    "refresh_consequences",
    "sandefjord_allocation",
    "status_crosswalk",
}

FLAT_BOOKING_MODULES = set(BOOKING_MODULES)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                package = "tournament_scheduler.booking".split(".")
                base = ".".join(package[:len(package) - node.level + 1])
                if node.module:
                    base += f".{node.module}"
            modules.add(base)
            if base == "tournament_scheduler.booking":
                modules.update(f"{base}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_booking_domain_modules_live_in_booking_package():
    assert {path.stem for path in BOOKING_DIR.glob("*.py") if path.stem != "__init__"} == BOOKING_MODULES


def test_flat_booking_modules_do_not_reappear_at_package_root():
    flat = sorted(path.name for path in PACKAGE_ROOT.glob("*.py") if path.stem in FLAT_BOOKING_MODULES)
    assert flat == []


def test_booking_package_does_not_depend_on_cli_or_infrastructure():
    offenders = {}
    for path in BOOKING_DIR.glob("*.py"):
        forbidden = sorted(
            module
            for module in _imported_modules(path)
            if module.startswith(("tournament_scheduler.cli", "tournament_scheduler.infrastructure"))
        )
        if forbidden:
            offenders[path.name] = forbidden
    assert offenders == {}


def test_booking_implementations_do_not_import_the_compatibility_facade():
    offenders = [
        path.name
        for path in BOOKING_DIR.glob("*.py")
        if path.stem != "calendar_bookings"
        and "tournament_scheduler.booking.calendar_bookings" in _imported_modules(path)
    ]
    assert offenders == []


def test_booking_internal_imports_are_acyclic():
    """Keep evidence/authority owners below the status and assessment read models."""

    prefix = "tournament_scheduler.booking."
    graph = {
        path.stem: {
            module.removeprefix(prefix).split(".")[0]
            for module in _imported_modules(path)
            if module.startswith(prefix)
        }
        for path in BOOKING_DIR.glob("*.py")
    }
    visited = set()

    def visit(module, stack):
        assert module not in stack, " -> ".join((*stack, module))
        if module in visited:
            return
        for dependency in sorted(graph.get(module, ())):
            visit(dependency, (*stack, module))
        visited.add(module)

    for module in sorted(graph):
        visit(module, ())


def test_booking_facade_reexports_implementations_without_wrapping_them():
    import importlib

    facade = importlib.import_module("tournament_scheduler.booking.calendar_bookings")
    tree = ast.parse((BOOKING_DIR / "calendar_bookings.py").read_text())
    assert not any(isinstance(node, (ast.FunctionDef, ast.ClassDef)) for node in tree.body)
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        owner = importlib.import_module(node.module)
        for alias in node.names:
            assert getattr(facade, alias.asname or alias.name) is getattr(owner, alias.name)