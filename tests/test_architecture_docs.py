"""#438: semantic architecture is navigable, source-linked and renderable."""

from __future__ import annotations

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check-architecture-docs.py"


def _checker() -> dict[str, object]:
    return runpy.run_path(str(SCRIPT))


def test_semantic_architecture_documentation_is_current() -> None:
    errors, diagrams = _checker()["check_architecture"]()
    assert errors == []
    assert len(diagrams) == 3


def test_mermaid_diagrams_can_be_extracted_for_ci_renderer(tmp_path: Path) -> None:
    assert _checker()["main"](["--extract-mermaid", str(tmp_path)]) == 0
    assert {path.name for path in tmp_path.glob("*.mmd")} == {
        "01-system-data-flow.mmd",
        "02-planning-decision.mmd",
        "03-season-lifecycle.mmd",
    }
    for path in tmp_path.glob("*.mmd"):
        assert path.read_text(encoding="utf-8").startswith("flowchart ")


def test_missing_local_owner_link_is_rejected() -> None:
    check = _checker()["local_link_errors"]
    source = ROOT / "docs/architecture/planning-model.md"
    assert check(source, "[missing owner](../../tournament_scheduler/no_such_owner.py)")


def test_architecture_source_is_not_an_independent_classification_catalog() -> None:
    text = (ROOT / "docs/architecture/planning-model.md").read_text(encoding="utf-8")
    assert "generated classification and per-rule ownership" in text
    assert "not a manually maintained second rule table" in text
    assert "no global optimality claim" in text.lower()
