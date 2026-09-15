"""Shared RVV policy and command procedures must not drift across harness adapters."""

from __future__ import annotations

from pathlib import Path

from tournament_scheduler.pipeline.audit_context import AUDIT_CHECKLIST

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = REPO_ROOT / ".agents" / "skills" / "rvv" / "SKILL.md"
SHARED_COMMAND_DIR = REPO_ROOT / ".agents" / "commands" / "rvv-miniputt"
ADAPTER_DIRS = (
    REPO_ROOT / ".claude" / "commands" / "rvv-miniputt",
    REPO_ROOT / ".codex" / "commands" / "rvv-miniputt",
    REPO_ROOT / ".chatgpt" / "commands" / "rvv-miniputt",
)
PI_RUNNER = REPO_ROOT / ".pi" / "lib" / "pipeline-runner.ts"
PI_EXTENSION = REPO_ROOT / ".pi" / "extensions" / "rvv-miniputt.ts"


def _adapter_files():
    for adapter_dir in ADAPTER_DIRS:
        if adapter_dir.exists():
            yield from sorted(adapter_dir.glob("*.md"))


def test_skill_md_contains_all_nine_checklist_items_verbatim():
    text = SKILL_MD.read_text(encoding="utf-8")
    for item in AUDIT_CHECKLIST:
        assert item["question"] in text


def test_skill_md_documents_the_audit_execution_model():
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "## Semantic safety-net audit" in text
    assert "audit-context" in text
    assert "audit-submit" in text
    assert "audit-run" in text


def test_no_adapter_file_duplicates_the_checklist_or_defines_its_own_policy():
    offenders: list[str] = []
    for path in _adapter_files():
        text = path.read_text(encoding="utf-8")
        for item in AUDIT_CHECKLIST:
            if item["question"] in text:
                offenders.append(f"{path}: duplicates checklist item {item['item_id']!r}")
        if "PASS" in text and "REVIEW_REQUIRED" in text and "FAIL" in text:
            offenders.append(f"{path}: appears to redefine the audit status vocabulary")
    assert offenders == []


def test_harness_command_adapters_delegate_to_shared_agent_neutral_procedures():
    offenders: list[str] = []
    for path in _adapter_files():
        shared_path = SHARED_COMMAND_DIR / path.name
        if not shared_path.exists():
            offenders.append(f"{path}: missing shared procedure {shared_path}")
            continue

        text = path.read_text(encoding="utf-8")
        shared_ref = shared_path.relative_to(REPO_ROOT).as_posix()
        if shared_ref not in text:
            offenders.append(f"{path}: does not reference {shared_ref}")

        # Repository commands and operational procedure text belong in the shared
        # file; adapters should contain only harness metadata/transport guidance.
        if "scripts/rvv-miniputt" in text or "python3 -m tournament_scheduler" in text:
            offenders.append(f"{path}: duplicates repository command procedure")

    assert offenders == []


def test_shared_command_procedures_are_harness_neutral():
    offenders: list[str] = []
    forbidden = (".claude/commands/", ".codex/commands/", ".chatgpt/commands/")
    for path in sorted(SHARED_COMMAND_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path}: contains harness-specific path {marker}")
    assert offenders == []


def test_pi_run_adapter_delegates_stage_orchestration_to_canonical_cli():
    """Pi may own UI/model/browser transport, never a second Stage 1-4 runner."""
    text = PI_RUNNER.read_text(encoding="utf-8")
    extension_text = PI_EXTENSION.read_text(encoding="utf-8")
    combined = text + "\n" + extension_text

    forbidden = (
        "tournament_scheduler.pipeline.stage1_config",
        "tournament_scheduler.pipeline.stage2_scraping",
        "tournament_scheduler.pipeline.stage3_planning",
        "tournament_scheduler.pipeline.stage4_export",
        "DEFAULT_PLANNER_ITERATIONS",
    )
    for marker in forbidden:
        assert marker not in combined, f"Pi adapter reintroduced repository-owned orchestration: {marker}"

    assert "runRepoCli" in text
    assert '"run"' in text
    assert '"--interactive"' in text
    assert '"--decision-action"' in text
    assert "DecisionContext" in text
    assert "recovery-inject" in (REPO_ROOT / ".pi" / "lib" / "browser-recovery.ts").read_text(encoding="utf-8")
