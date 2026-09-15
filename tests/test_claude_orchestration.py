from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLAUDE_RUN = ROOT / ".claude" / "commands" / "rvv-miniputt" / "run.md"
SHARED_RUN = ROOT / ".agents" / "commands" / "rvv-miniputt" / "run.md"
PI_REPO_CLI = ROOT / ".pi" / "lib" / "repo-cli.ts"


def test_claude_run_command_is_only_a_transport_adapter() -> None:
    text = CLAUDE_RUN.read_text(encoding="utf-8")

    assert ".agents/commands/rvv-miniputt/run.md" in text
    assert ".agents/skills/rvv/SKILL.md" in text
    for forbidden in (
        "tournament_scheduler.pipeline.stage1_config",
        "tournament_scheduler.pipeline.stage2_scraping",
        "tournament_scheduler.pipeline.stage3_planning",
        "tournament_scheduler.pipeline.stage4_export",
    ):
        assert forbidden not in text


def test_shared_run_procedure_uses_the_canonical_interactive_command() -> None:
    text = SHARED_RUN.read_text(encoding="utf-8")

    assert "scripts/rvv-miniputt run --interactive" in text
    assert "DecisionContext" in text
    assert "available_actions" in text
    assert "tournament_scheduler.pipeline.stage" not in text


def test_pi_and_non_pi_harnesses_target_the_same_python_cli() -> None:
    pi_text = PI_REPO_CLI.read_text(encoding="utf-8")
    launcher_text = (ROOT / "scripts" / "rvv-miniputt").read_text(encoding="utf-8")

    assert 'runPythonModule(ctx, "tournament_scheduler.cli.rvv_cli"' in pi_text
    assert "-m tournament_scheduler.cli.rvv_cli" in launcher_text
