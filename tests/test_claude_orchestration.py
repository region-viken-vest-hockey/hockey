from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLAUDE_OPERATE = ROOT / ".claude" / "commands" / "rvv-miniputt" / "operate.md"
SHARED_OPERATE = ROOT / ".agents" / "commands" / "rvv-miniputt" / "operate.md"
SHARED_RUN = ROOT / ".agents" / "commands" / "rvv-miniputt" / "run.md"


def test_claude_operate_command_is_only_a_transport_adapter() -> None:
    text = CLAUDE_OPERATE.read_text(encoding="utf-8")

    assert ".agents/commands/rvv-miniputt/operate.md" in text
    assert ".agents/skills/rvv/SKILL.md" in text
    assert "scripts/rvv-miniputt" not in text


def test_shared_operate_routes_to_existing_internal_procedures() -> None:
    text = SHARED_OPERATE.read_text(encoding="utf-8")

    for procedure in (
        "guide.md",
        "run.md",
        "season.md",
        "publish.md",
        "calendars.md",
        "scrape.md",
    ):
        assert procedure in text
    assert "single operator-facing entry point" in text
    assert "Never publish unless the operator explicitly asks to publish." in text


def test_shared_run_procedure_uses_the_canonical_interactive_command() -> None:
    text = SHARED_RUN.read_text(encoding="utf-8")

    assert "scripts/rvv-miniputt run --interactive" in text
    assert "DecisionContext" in text
    assert "available_actions" in text
    assert "tournament_scheduler.pipeline.stage" not in text
