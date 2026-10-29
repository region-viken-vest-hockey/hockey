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
    "assessment_candidates",
    "assessment_export",
    "calendar_audit",
    "calendar_availability",
    "calendar_bookings",
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
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
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