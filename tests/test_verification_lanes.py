"""Guards for the quick/full/live verification lanes.

These are fast, hermetic architecture tests: they fail in the normal quick lane
if the lane configuration silently regresses -- e.g. a marker loses its CI
lane, the scheduled workflow disappears, or the default selection stops
excluding an unavailable external runtime.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
CHECK_SCRIPT = ROOT / "scripts" / "check"
FULL_WORKFLOW = ROOT / ".github" / "workflows" / "full-verification.yml"
CI_DOC = ROOT / "docs" / "ci.md"


def _pytest_config() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]


def test_expected_markers_are_registered() -> None:
    markers = " ".join(_pytest_config()["markers"])
    for marker in ("slow", "integration", "harness", "live"):
        assert f"{marker}:" in markers, f"marker {marker!r} is not registered"


def test_default_selection_excludes_every_non_quick_lane() -> None:
    addopts = " ".join(_pytest_config()["addopts"])
    for marker in ("slow", "integration", "harness", "live"):
        assert f"not {marker}" in addopts, f"default selection does not exclude {marker}"


def test_no_bookup_authentication_or_mfa_marker_exists() -> None:
    """BookUp is a public deterministic source; it must not carry a manual
    auth/MFA/session-handoff test category."""
    markers = " ".join(_pytest_config()["markers"]).lower()
    for forbidden in ("mfa", "manual-login", "manual_login", "authentication", "credential", "session-handoff"):
        assert forbidden not in markers


def test_check_script_exposes_the_documented_phases() -> None:
    text = CHECK_SCRIPT.read_text(encoding="utf-8")
    for phase_name in ("full)", "full-tests)", "slow)", "integration)", "cli-contracts)", "live)", "harness)"):
        assert phase_name in text, f"scripts/check is missing the {phase_name} phase"
    # The live phase must opt in explicitly rather than running by accident.
    assert "RVV_LIVE_TESTS=1" in text


def test_full_lane_workflow_is_scheduled_and_manual() -> None:
    yaml = pytest.importorskip("yaml")
    assert FULL_WORKFLOW.exists(), "missing scheduled/manual full-verification workflow"
    workflow = yaml.safe_load(FULL_WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML parses the bare ``on:`` key as boolean True.
    triggers = workflow.get("on") or workflow.get(True)
    assert "workflow_dispatch" in triggers
    assert "schedule" in triggers

    text = FULL_WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/check full" in text
    assert "scripts/check live" in text
    # The live job must not be allowed to fail the lane as a whole.
    assert "continue-on-error: true" in text


def test_ci_documents_quick_versus_full_verification() -> None:
    text = CI_DOC.read_text(encoding="utf-8")
    assert "scripts/check full" in text
    for marker in ("slow", "integration", "harness", "live"):
        assert marker in text
    assert "quick" in text.lower()


def test_contract_and_lifecycle_suites_exist() -> None:
    assert (ROOT / "tests" / "test_cli_contract.py").exists()
    assert (ROOT / "tests" / "test_lifecycle_multiprocess.py").exists()
    assert (ROOT / "tests" / "test_scraper_bookup_deterministic.py").exists()
