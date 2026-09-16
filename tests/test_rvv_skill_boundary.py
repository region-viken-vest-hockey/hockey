from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RVV_SKILL_FILE = ROOT / ".agents" / "skills" / "rvv" / "SKILL.md"
GUIDE_FILE = ROOT / ".agents" / "commands" / "rvv-miniputt" / "guide.md"
SCRAPE_LLM_FILE = ROOT / ".agents" / "commands" / "rvv-miniputt" / "scrape-llm.md"


def test_rvv_skill_is_harness_neutral_and_documents_two_phase_lifecycle() -> None:
    text = RVV_SKILL_FILE.read_text(encoding="utf-8")

    assert "Initial season creation" in text
    assert "Promoted-season maintenance" in text
    assert "scripts/rvv-miniputt run --interactive" in text
    assert "scripts/rvv-miniputt season" in text
    assert "operator audit-context" in text
    assert "operator audit-evidence" in text
    assert "operator audit-submit" in text
    assert "rvv_miniputt_scrape" not in text
    assert "rvv_miniputt_scrape_llm" not in text


def test_shared_guide_routes_promoted_season_without_pi_special_case() -> None:
    text = GUIDE_FILE.read_text(encoding="utf-8")

    assert "shared conversational guide procedure for every agent harness" in text
    assert "season promote" in text
    assert "season approve" in text
    assert "season replan" in text
    assert "Pi may provide its own" not in text


def test_browser_recovery_is_shared_harness_capability_not_pi_extension() -> None:
    text = SCRAPE_LLM_FILE.read_text(encoding="utf-8")

    assert "Browser-enabled harness" in text
    assert "recovery-inject" in text
    assert "scrape-merge" in text
    assert "rvv_miniputt_scrape_llm" not in text
    assert "Pi owns" not in text


def test_repo_does_not_require_rvv_specific_pi_extension() -> None:
    assert not (ROOT / ".pi" / "extensions" / "rvv-miniputt.ts").exists()
    assert not (ROOT / ".pi" / "extensions" / "rvv-miniputt-season.ts").exists()
    assert not (ROOT / ".pi" / "lib" / "pipeline-runner.ts").exists()
    assert not (ROOT / ".pi" / "lib" / "operator-audit.ts").exists()
    assert not (ROOT / ".pi" / "lib" / "browser-recovery.ts").exists()
