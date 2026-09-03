from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RVV_SKILL_FILE = ROOT / ".agents" / "skills" / "rvv" / "SKILL.md"
RVV_RUNBOOK_FILE = ROOT / ".agents" / "skills" / "rvv" / "RUNBOOK.md"
RVV_EXTENSION_FILE = ROOT / ".pi" / "extensions" / "rvv-miniputt.ts"
CLAUDE_RUN_FILE = ROOT / ".claude" / "commands" / "rvv-miniputt" / "run.md"
PIPELINE_DOC_FILE = ROOT / "docs" / "rvv-miniputt-pipeline.md"


def test_shared_rvv_skill_points_to_one_canonical_runbook_and_cli() -> None:
    skill = RVV_SKILL_FILE.read_text(encoding="utf-8")
    runbook = RVV_RUNBOOK_FILE.read_text(encoding="utf-8")

    assert "RUNBOOK.md" in skill
    assert "scripts/rvv-miniputt" in skill
    assert "Python—not Pi" in runbook
    assert "blocking_sources" in runbook
    assert "temporarily_unresolved_sources" in runbook
    assert ".pipeline/auth/bookup-storage-state.json" in runbook
    assert "macOS host" in runbook
    assert "Lima" in runbook


def test_rvv_extension_still_exposes_pi_browser_recovery_tools() -> None:
    text = RVV_EXTENSION_FILE.read_text(encoding="utf-8")

    assert 'rvv-miniputt scrape' in text
    assert 'rvv-miniputt scrape-llm' in text
    assert 'rvv_miniputt_scrape' in text
    assert 'rvv_miniputt_scrape_llm' in text


def test_claude_run_adapter_is_thin_and_uses_shared_cli() -> None:
    text = CLAUDE_RUN_FILE.read_text(encoding="utf-8")

    assert ".agents/skills/rvv/SKILL.md" in text
    assert ".agents/skills/rvv/RUNBOOK.md" in text
    assert "scripts/rvv-miniputt run" in text
    assert "Do not invoke Stage 1–4 modules individually" in text
    assert "dotenvx run -f" not in text
    assert "BOOKUP_PASSWORD" not in text


def test_pipeline_doc_spells_out_terminal_only_recovery_bridge() -> None:
    text = PIPELINE_DOC_FILE.read_text(encoding="utf-8")

    assert "scripts/rvv-miniputt recovery-targets" in text
    assert "python3 -m tournament_scheduler.cli.rvv_cli recovery-inject --source" in text
    assert "scripts/rvv-miniputt scrape-merge" in text
    assert "Browser-capability boundary" in text
    assert "Python Stage 2 rerun" in text
