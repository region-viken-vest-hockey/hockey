from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RVV_SKILL_FILE = ROOT / ".agents" / "skills" / "rvv" / "SKILL.md"
RVV_EXTENSION_FILE = ROOT / ".pi" / "extensions" / "rvv-miniputt.ts"


def test_rvv_skill_documents_checkpoint_review_and_single_club_troubleshooting() -> None:
    text = RVV_SKILL_FILE.read_text(encoding="utf-8")

    assert "stage-by-stage pipeline" in text
    assert "review the checkpoint" in text
    assert "rvv_miniputt_scrape" in text
    assert "rvv_miniputt_scrape_llm" in text
    assert "scrape --club" in text
    assert "scrape-llm --club" in text
    assert "recovery-targets" in text
    assert "recovery-inject" in text
    assert "scrape-merge" in text


def test_rvv_extension_exposes_scrape_commands_and_tools() -> None:
    text = RVV_EXTENSION_FILE.read_text(encoding="utf-8")

    assert 'rvv-miniputt scrape' in text
    assert 'rvv-miniputt scrape-llm' in text
    assert 'rvv_miniputt_scrape' in text
    assert 'rvv_miniputt_scrape_llm' in text
