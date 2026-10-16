"""Shared repository assets resolve through one resolver, in the source tree and the frozen layout."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tournament_scheduler import repository_resources
from tournament_scheduler.repository_resources import package_base_dir, repository_resource

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "windows" / "rvv-miniputt.spec"
SKILL_PARTS = (".agents", "skills", "rvv", "SKILL.md")
PLANNING_SKILL_PARTS = (".agents", "skills", "rvv", "planning", "SKILL.md")


def test_resolver_finds_shared_skill_assets_from_the_source_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    assert package_base_dir() == ROOT
    assert repository_resource(*SKILL_PARTS).is_file()
    assert repository_resource(*PLANNING_SKILL_PARTS).is_file()


def test_resolver_uses_meipass_in_the_frozen_layout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)

    assert package_base_dir() == tmp_path / "_internal"
    assert repository_resource(*SKILL_PARTS) == tmp_path / "_internal" / ".agents" / "skills" / "rvv" / "SKILL.md"


def test_resolver_falls_back_to_source_root_when_frozen_without_meipass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    assert package_base_dir() == repository_resources._SOURCE_ROOT


def test_resolver_rejects_an_empty_path() -> None:
    with pytest.raises(ValueError):
        repository_resource()


def test_runtime_readers_use_the_resolver_paths() -> None:
    from tournament_scheduler.llm_judge import prompts
    from tournament_scheduler.pipeline import audit_context

    assert audit_context._SKILL_MD_PATH == repository_resource(*SKILL_PARTS)
    assert prompts._SKILL_MD_PATH == repository_resource(*PLANNING_SKILL_PARTS)
    assert "Stage gating policy" in prompts._read_skill_md()


def test_pyinstaller_spec_bundles_every_runtime_skill_file() -> None:
    spec = SPEC.read_text(encoding="utf-8")

    # The spec must bundle the same repository-relative skill tree the resolver reads.
    assert '".agents", "skills", "rvv", "**", "SKILL.md"' in spec
    assert "recursive=True" in spec
    for skill in ROOT.glob(".agents/skills/rvv/**/SKILL.md"):
        assert skill.is_file()
