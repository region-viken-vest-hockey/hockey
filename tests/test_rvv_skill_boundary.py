from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RVV_SKILL_FILE = ROOT / ".agents" / "skills" / "rvv" / "SKILL.md"
RVV_RUNBOOK_FILE = ROOT / ".agents" / "skills" / "rvv" / "RUNBOOK.md"
RUN_ADAPTERS = [
    ROOT / ".chatgpt" / "commands" / "rvv-miniputt" / "run.md",
    ROOT / ".claude" / "commands" / "rvv-miniputt" / "run.md",
    ROOT / ".codex" / "commands" / "rvv-miniputt" / "run.md",
    ROOT / ".opencode" / "commands" / "rvv-miniputt" / "run.md",
]


def test_shared_rvv_skill_points_to_one_canonical_runbook_and_cli() -> None:
    skill = RVV_SKILL_FILE.read_text(encoding="utf-8")
    runbook = RVV_RUNBOOK_FILE.read_text(encoding="utf-8")

    assert "RUNBOOK.md" in skill
    assert "scripts/rvv-miniputt" in skill
    assert "harness-independent operational runbook" in runbook
    assert "scripts/rvv-miniputt" in runbook


def test_run_adapters_are_thin_and_delegate_to_shared_runbook() -> None:
    for path in RUN_ADAPTERS:
        text = path.read_text(encoding="utf-8")
        assert ".agents/skills/rvv/SKILL.md" in text
        assert ".agents/skills/rvv/RUNBOOK.md" in text
        assert "scripts/rvv-miniputt run" in text
        assert "Do not invoke Stage 1–4 modules individually" in text
        assert "BOOKUP_PASSWORD" not in text


def test_claude_adapter_does_not_embed_credential_flow() -> None:
    text = RUN_ADAPTERS[1].read_text(encoding="utf-8")

    assert "dotenvx run -f" not in text
    assert "BOOKUP_EMAIL" not in text
    assert "BOOKUP_PASSWORD" not in text
